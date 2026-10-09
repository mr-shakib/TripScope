"""Job worker (ADR-15): claims queued jobs from PostgreSQL and runs them one at a time.

A heartbeat thread records liveness and watches for cancel requests; on cancel it sets the run's cancel event
and cancels in-flight Spark jobs, so long transforms stop promptly. Jobs whose worker stops heartbeating are
failed by any live worker after `stale_after`.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta

from tripscope.core.errors import PipelineError
from tripscope.jobs import service
from tripscope.metadata.models import DataSource, IngestionJob
from tripscope.pipeline.runner import JobCancelledError, PipelineDeps, run_source

log = logging.getLogger(__name__)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    def __init__(
        self,
        deps: PipelineDeps,
        *,
        worker_id: str | None = None,
        poll_seconds: float = 2.0,
        heartbeat_seconds: float = 5.0,
        stale_after: timedelta = timedelta(minutes=2),
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self.deps = deps
        self.worker_id = worker_id or default_worker_id()
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_after = stale_after
        self.on_cancel = on_cancel  # e.g. cancel all running Spark jobs

    def run_forever(self, stop: threading.Event) -> None:
        log.info("worker started", extra={"worker_id": self.worker_id})
        while not stop.is_set():
            try:
                job_id = self.run_once()
            except Exception:  # never let one bad job stop the worker
                log.exception("worker iteration failed")
                job_id = None
            if job_id is None:
                stop.wait(self.poll_seconds)
        log.info("worker stopped", extra={"worker_id": self.worker_id})

    def run_once(self) -> uuid.UUID | None:
        """Fail stale jobs, then claim and run the oldest queued job. Returns its id, or None if idle."""
        factory = self.deps.session_factory
        with factory() as session, session.begin():
            failed = service.fail_stale(session, stale_after=self.stale_after)
            if failed:
                log.warning("failed stale jobs", extra={"count": failed})
            job_id = service.claim_next(session, self.worker_id)
        if job_id is None:
            return None
        with factory() as session:
            job = session.get(IngestionJob, job_id)
            assert job is not None
            source_key = session.get(DataSource, job.data_source_id).source_key  # type: ignore[union-attr]
            trigger, requested_by = job.trigger, job.requested_by
        log.info("job claimed", extra={"job_id": str(job_id), "source": source_key})

        cancel_event, done = threading.Event(), threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job_id, cancel_event, done), daemon=True)
        beat.start()
        try:
            run_source(
                self.deps,
                source_key,
                trigger=trigger,
                requested_by=requested_by,
                job_id=job_id,
                cancel_event=cancel_event,
            )
        except JobCancelledError:
            log.info("job cancelled", extra={"job_id": str(job_id)})
        except PipelineError as exc:
            log.warning("job failed", extra={"job_id": str(job_id), "error": exc.message})
        finally:
            done.set()
            beat.join(timeout=self.heartbeat_seconds * 2)
        return job_id

    def _heartbeat(self, job_id: uuid.UUID, cancel_event: threading.Event, done: threading.Event) -> None:
        while not done.wait(self.heartbeat_seconds):
            try:
                with self.deps.session_factory() as session, session.begin():
                    cancel_requested = service.heartbeat(session, job_id)
            except Exception:
                log.exception("heartbeat failed", extra={"job_id": str(job_id)})
                continue
            if cancel_requested and not cancel_event.is_set():
                log.info("cancel requested", extra={"job_id": str(job_id)})
                cancel_event.set()
                if self.on_cancel is not None:
                    self.on_cancel()
