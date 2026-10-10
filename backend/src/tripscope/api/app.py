"""FastAPI application factory."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tripscope.ai.provider import build_provider
from tripscope.ai.service import AIAnalyst
from tripscope.analytics.service import AnalyticsService
from tripscope.api.routers import ai, analytics, auth, datasets, explorer, health, jobs, reports, sources
from tripscope.api.security import LoginThrottle
from tripscope.core.errors import TripScopeError
from tripscope.core.logging import configure_logging, request_id_var
from tripscope.core.settings import Settings, get_settings
from tripscope.metadata.db import make_engine, make_session_factory
from tripscope.pipeline.manifest import Manifest, load_manifest
from tripscope.storage.clickhouse import reader_client
from tripscope.storage.object_store import ObjectStore

log = logging.getLogger("tripscope.api")
API_PREFIX = "/api/v1"


class ManifestCache:
    """Loads the source manifest, re-reading it only when the file changes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self._manifest: Manifest | None = None
        self._lock = threading.Lock()

    def __call__(self) -> Manifest:
        with self._lock:
            mtime = self.path.stat().st_mtime
            if self._manifest is None or mtime != self._mtime:
                self._manifest, self._mtime = load_manifest(self.path), mtime
            return self._manifest


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    production = settings.app_env == "production"
    app = FastAPI(
        title="TripScope API",
        version="0.1.0",
        docs_url=None if production else "/api/docs",
        redoc_url=None,
        openapi_url=None if production else "/api/openapi.json",
    )

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.login_throttle = LoginThrottle()
    app.state.analytics = AnalyticsService(
        client_factory=lambda: reader_client(settings),
        session_factory=session_factory,
        database=settings.clickhouse_database,
        max_range_days=settings.analytics_max_range_days,
    )
    app.state.ai = AIAnalyst(
        settings=settings,
        analytics=app.state.analytics,
        session_factory=session_factory,
        provider=build_provider(settings),
    )
    app.state.object_store = lambda: ObjectStore.from_settings(settings)
    app.state.manifest = ManifestCache(settings.resolved_manifest_path)
    app.state.geometry_cache = {}
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-Request-ID"],
            expose_headers=["X-Total-Rows", "X-Exported-Rows", "X-Truncated", "X-Report-SHA256"],
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if 8 <= len(incoming) <= 64 and incoming.isalnum() else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": elapsed_ms,
            },
        )
        request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith(API_PREFIX):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(TripScopeError)
    async def domain_error(request: Request, exc: TripScopeError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.details or None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())[1:]) or None,
                "message": err.get("msg"),
                "type": err.get("type"),
            }
            for err in exc.errors()
        ]
        return _error(422, "validation_failed", "request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(
            500, "internal_error", "an unexpected error occurred; quote the request ID when reporting it"
        )

    app.include_router(health.router)
    for router in (
        auth.router,
        datasets.router,
        analytics.router,
        explorer.router,
        jobs.router,
        sources.router,
        reports.router,
        ai.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    return app
