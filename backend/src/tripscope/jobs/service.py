"""Ingestion job lifecycle backed by PostgreSQL (ADR-15): enqueue, retry, cancel, claim, stale detection.

States: queued → running → completed | failed | cancelled. A partial unique index guarantees at most one
queued-or-running job per source, so concurrent requests cannot start duplicate runs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tripscope.core.errors import ConflictError, NotFoundError
from tripscope.metadata.models import DataSource, IngestionJob, JobStatus
from tripscope.metadata.sources import register_source
from tripscope.pipeline.manifest import Manifest

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)
RETRYABLE = (JobStatus.FAILED, JobStatus.CANCELLED)


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    session: Session,
    manifest: Manifest,
    source_key: str,
    *,
    requested_by: uuid.UUID | None,
    trigger: str = "api",
    retry_of: uuid.UUID | None = None,
) -> IngestionJob:
    try:
        source = manifest.get_source(source_key)
    except KeyError as exc:
        raise NotFoundError(f"source {source_key!r} is not in the manifest") from exc
    data_source = register_source(session, manifest, source)
    active = session.scalar(
        select(IngestionJob).where(
            IngestionJob.data_source_id == data_source.id, IngestionJob.status.in_(ACTIVE)
        )
    )
    if active is not None:
        raise ConflictError(
            f"{source_key} already has a {active.status.value} job",
            details={"job_id": str(active.id), "status": active.status.value},
        )
    attempts = session.query(IngestionJob).filter(IngestionJob.data_source_id == data_source.id).count()
    job = IngestionJob(
        data_source_id=data_source.id,
        status=JobStatus.QUEUED,
        trigger=trigger,
        requested_by=requested_by,
        attempt=attempts + 1,
        retry_of_job_id=retry_of,
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as exc:  # lost a race with another request for the same source
        raise ConflictError(f"{source_key} already has an active job") from exc
    return job


def get_job(session: Session, job_id: uuid.UUID) -> IngestionJob:
    job = session.get(IngestionJob, job_id)
    if job is None:
        raise NotFoundError("job not found")
    return job


def retry(
    session: Session, manifest: Manifest, job_id: uuid.UUID, *, requested_by: uuid.UUID | None
) -> IngestionJob:
    job = get_job(session, job_id)
    if job.status not in RETRYABLE:
        raise ConflictError(f"only failed or cancelled jobs can be retried (this job is {job.status.value})")
    source_key = session.get(DataSource, job.data_source_id).source_key  # type: ignore[union-attr]
    return enqueue(session, manifest, source_key, requested_by=requested_by, trigger="retry", retry_of=job.id)


def cancel(session: Session, job_id: uuid.UUID, *, requested_by_label: str) -> IngestionJob:
    """Queued jobs are cancelled immediately; running jobs get a cancel request the worker acts on."""
    job = get_job(session, job_id)
    now = _now()
    if job.status == JobStatus.QUEUED:
        job.status, job.finished_at = JobStatus.CANCELLED, now
        job.cancel_requested_at = now
        job.error_summary = f"cancelled by {requested_by_label} before it started"
    elif job.status == JobStatus.RUNNING:
        job.cancel_requested_at = job.cancel_requested_at or now
    else:
        raise ConflictError(f"job already finished ({job.status.value})")
    session.flush()
    return job


def claim_next(session: Session, worker_id: str) -> uuid.UUID | None:
    """Atomically move the oldest queued job to running for this worker (safe with many workers)."""
    row = session.execute(
        text(
            """
            UPDATE ingestion_jobs
            SET status = 'running', worker_id = :worker, started_at = now(), heartbeat_at = now()
            WHERE id = (
                SELECT id FROM ingestion_jobs WHERE status = 'queued'
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING id
            """
        ),
        {"worker": worker_id},
    ).first()
    return row[0] if row else None


def heartbeat(session: Session, job_id: uuid.UUID) -> bool:
    """Record liveness; returns True when cancellation has been requested."""
    session.execute(update(IngestionJob).where(IngestionJob.id == job_id).values(heartbeat_at=_now()))
    requested = session.scalar(select(IngestionJob.cancel_requested_at).where(IngestionJob.id == job_id))
    return requested is not None


def fail_stale(session: Session, *, stale_after: timedelta) -> int:
    """Fail running jobs whose worker stopped heartbeating (crash, OOM kill, host restart)."""
    cutoff = _now() - stale_after
    jobs = session.scalars(
        select(IngestionJob).where(
            IngestionJob.status == JobStatus.RUNNING, IngestionJob.heartbeat_at < cutoff
        )
    ).all()
    for job in jobs:
        job.status, job.finished_at = JobStatus.FAILED, _now()
        job.error_summary = f"worker {job.worker_id or 'unknown'} stopped responding; retry the job"
        for run in job.runs:
            if run.status == JobStatus.RUNNING:
                run.status, run.completed_at = JobStatus.FAILED, _now()
                run.error_summary = job.error_summary
    return len(jobs)
