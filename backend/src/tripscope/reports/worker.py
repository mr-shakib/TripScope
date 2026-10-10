"""Report worker (ADR-19): claims queued report runs from PostgreSQL and generates their files.

It runs apart from the Spark worker so a report never waits behind an ingestion job. Each file is stored in
the lake next to the exact document it was rendered from (`document.json`), so its numbers can be audited.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from tripscope.analytics.service import AnalyticsService
from tripscope.core.errors import TripScopeError
from tripscope.metadata.models import ReportFormat, ReportRun, User
from tripscope.reports import service
from tripscope.reports.builder import ReportBuilder
from tripscope.reports.document import ReportDocument
from tripscope.reports.render_csv import render_report_csv
from tripscope.reports.render_pdf import render_pdf
from tripscope.reports.render_xlsx import render_report_xlsx
from tripscope.storage.object_store import ObjectStore

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    ReportFormat.PDF: "application/pdf",
    ReportFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ReportFormat.CSV: "text/csv; charset=utf-8",
}


def report_prefix(report_id: uuid.UUID, run_id: uuid.UUID) -> str:
    return f"reports/{uuid.UUID(str(report_id))}/{uuid.UUID(str(run_id))}"


@dataclass(frozen=True)
class RenderedFile:
    data: bytes
    file_name: str
    content_type: str
    page_count: int | None


def render(doc: ReportDocument, fmt: ReportFormat, definition: dict[str, Any]) -> RenderedFile:
    stem = service.file_stem(definition, doc.period.start, doc.period.end)
    if fmt == ReportFormat.PDF:
        data, pages = render_pdf(doc)
        return RenderedFile(data, f"{stem}.pdf", CONTENT_TYPES[fmt], pages)
    if fmt == ReportFormat.XLSX:
        return RenderedFile(render_report_xlsx(doc), f"{stem}.xlsx", CONTENT_TYPES[fmt], None)
    return RenderedFile(render_report_csv(doc), f"{stem}.csv", CONTENT_TYPES[fmt], None)


class ReportWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        analytics: AnalyticsService,
        store_factory: Callable[[], ObjectStore],
        worker_id: str | None = None,
        poll_seconds: float = 2.0,
        heartbeat_seconds: float = 5.0,
        stale_after: timedelta = timedelta(minutes=2),
    ) -> None:
        self.sessions = session_factory
        self.builder = ReportBuilder(analytics)
        self.store_factory = store_factory
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:reports"
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_after = stale_after

    def run_forever(self, stop: threading.Event) -> None:
        log.info("report worker started", extra={"worker_id": self.worker_id})
        while not stop.is_set():
            try:
                run_id = self.run_once()
            except Exception:  # never let one bad run stop the worker
                log.exception("report worker iteration failed")
                run_id = None
            if run_id is None:
                stop.wait(self.poll_seconds)
        log.info("report worker stopped", extra={"worker_id": self.worker_id})

    def run_once(self) -> uuid.UUID | None:
        """Fail stale runs, then claim and generate the oldest queued run. Returns its id, or None if idle."""
        with self.sessions() as session, session.begin():
            failed = service.fail_stale(session, stale_after=self.stale_after)
            if failed:
                log.warning("failed stale report runs", extra={"count": failed})
            run_id = service.claim_next(session, self.worker_id)
        if run_id is None:
            return None
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(run_id, done), daemon=True)
        beat.start()
        try:
            self._generate(run_id)
        except TripScopeError as exc:
            log.warning("report run failed", extra={"run_id": str(run_id), "error": exc.message})
            self._fail(run_id, exc.message)
        except Exception:
            log.exception("report run failed unexpectedly", extra={"run_id": str(run_id)})
            self._fail(run_id, "the report could not be generated because of an internal error; try again")
        finally:
            done.set()
            beat.join(timeout=self.heartbeat_seconds * 2)
        return run_id

    def _generate(self, run_id: uuid.UUID) -> None:
        with self.sessions() as session:
            run = session.get(ReportRun, run_id)
            assert run is not None
            requester = session.get(User, run.requested_by)
            definition, fmt, report_id = dict(run.definition), run.format, run.report_id
            prepared_by = requester.display_name if requester else "TripScope"
        log.info("report run started", extra={"run_id": str(run_id), "format": fmt.value})
        doc = self.builder.build(
            template=definition["template"],
            title=definition["title"],
            filters=service.report_filters(definition),
            sections=list(definition["sections"]),
            prepared_by=prepared_by,
            narrative=definition.get("narrative"),
        )
        rendered = render(doc, fmt, definition)
        digest = hashlib.sha256(rendered.data).hexdigest()
        prefix = report_prefix(report_id, run_id)
        store = self.store_factory()
        store.put_bytes(
            f"{prefix}/document.json", doc.model_dump_json().encode(), content_type="application/json"
        )
        key = f"{prefix}/{rendered.file_name}"
        store.put_bytes(key, rendered.data, content_type=rendered.content_type, metadata={"sha256": digest})
        with self.sessions() as session, session.begin():
            service.complete_run(
                session,
                run_id,
                object_key=key,
                file_name=rendered.file_name,
                size_bytes=len(rendered.data),
                sha256=digest,
                dataset_version={
                    "version_id": doc.dataset.version_id,
                    "periods": [p.model_dump(mode="json") for p in doc.dataset.periods],
                },
                page_count=rendered.page_count,
            )
        log.info(
            "report run completed",
            extra={"run_id": str(run_id), "format": fmt.value, "bytes": len(rendered.data)},
        )

    def _fail(self, run_id: uuid.UUID, message: str) -> None:
        with self.sessions() as session, session.begin():
            service.fail_run(session, run_id, message)

    def _heartbeat(self, run_id: uuid.UUID, done: threading.Event) -> None:
        while not done.wait(self.heartbeat_seconds):
            try:
                with self.sessions() as session, session.begin():
                    service.heartbeat(session, run_id)
            except Exception:
                log.exception("report heartbeat failed", extra={"run_id": str(run_id)})
