"""Liveness and readiness (FR-13). Readiness reports component status without exposing internals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    state = request.app.state
    checks: dict[str, Any] = {}
    try:
        with state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "unavailable"
    try:
        state.analytics.client.query("SELECT 1")
        checks["clickhouse"] = "ok"
    except Exception:
        checks["clickhouse"] = "unavailable"
    try:
        state.object_store().check_bucket()
        checks["object_storage"] = "ok"
    except Exception:
        checks["object_storage"] = "unavailable"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ready" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503
    )
