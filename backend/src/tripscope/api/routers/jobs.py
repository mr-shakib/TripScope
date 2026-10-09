"""Ingestion jobs (FR-03): queue, inspect, retry, cancel; processing-run logs.

Admins start, retry and cancel jobs (spec §3.1); admins and analysts can inspect them. Every state-changing
call is audited.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tripscope.api.security import CurrentUser, require_roles
from tripscope.core.errors import NotFoundError
from tripscope.jobs import service
from tripscope.metadata.audit import record_audit
from tripscope.metadata.models import DataSource, IngestionJob, JobStatus, ProcessingRun, Role

router = APIRouter(tags=["jobs"])

Viewer = Annotated[CurrentUser, Depends(require_roles(Role.ADMIN, Role.ANALYST))]
Operator = Annotated[CurrentUser, Depends(require_roles(Role.ADMIN))]


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$")


class JobListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["queued", "running", "completed", "failed", "cancelled"] | None = None
    source_key: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=200)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _run_dict(run: ProcessingRun, *, with_logs: bool = False) -> dict[str, Any]:
    quality = run.quality
    data = {
        "run_id": str(run.id),
        "status": run.status.value,
        "current_stage": run.current_stage,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "duration_seconds": run.duration_seconds,
        "stage_timings": run.stage_timings,
        "schema_version": run.schema_version,
        "unavailable_fields": run.unavailable_fields,
        "error_summary": run.error_summary,
        "input_rows": quality.input_row_count if quality else None,
        "accepted_rows": quality.accepted_row_count if quality else None,
        "quarantined_rows": quality.quarantined_row_count if quality else None,
        "clickhouse_rows": run.clickhouse_rows,
    }
    if with_logs:
        data["logs"] = run.log_events
    return data


def _job_dict(job: IngestionJob) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "source_key": job.data_source.source_key,
        "period": job.data_source.data_period.strftime("%Y-%m"),
        "status": job.status.value,
        "attempt": job.attempt,
        "trigger": job.trigger,
        "requested_by": str(job.requested_by) if job.requested_by else None,
        "retry_of_job_id": str(job.retry_of_job_id) if job.retry_of_job_id else None,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "heartbeat_at": job.heartbeat_at,
        "worker_id": job.worker_id,
        "cancel_requested": job.cancel_requested_at is not None,
        "error_summary": job.error_summary,
        "runs": [_run_dict(run) for run in job.runs],
    }


def _load_job(session: Session, job_id: uuid.UUID) -> IngestionJob:
    job = session.scalar(
        select(IngestionJob)
        .where(IngestionJob.id == job_id)
        .options(
            selectinload(IngestionJob.data_source),
            selectinload(IngestionJob.runs).selectinload(ProcessingRun.quality),
        )
    )
    if job is None:
        raise NotFoundError("job not found")
    return job


@router.get("/ingestion-jobs")
def list_jobs(request: Request, user: Viewer, query: Annotated[JobListQuery, Query()]) -> dict[str, Any]:
    stmt = (
        select(IngestionJob)
        .options(
            selectinload(IngestionJob.data_source),
            selectinload(IngestionJob.runs).selectinload(ProcessingRun.quality),
        )
        .order_by(IngestionJob.created_at.desc())
        .limit(query.limit)
    )
    if query.status:
        stmt = stmt.where(IngestionJob.status == JobStatus(query.status))
    if query.source_key:
        stmt = stmt.join(DataSource).where(DataSource.source_key == query.source_key)
    with request.app.state.session_factory() as session:
        return {"jobs": [_job_dict(job) for job in session.scalars(stmt).all()]}


@router.get("/ingestion-jobs/{job_id}")
def get_job(job_id: uuid.UUID, request: Request, user: Viewer) -> dict[str, Any]:
    with request.app.state.session_factory() as session:
        return _job_dict(_load_job(session, job_id))


@router.post("/ingestion-jobs", status_code=201)
def create_job(body: CreateJob, request: Request, user: Operator) -> dict[str, Any]:
    manifest = request.app.state.manifest()
    with request.app.state.session_factory() as session, session.begin():
        job = service.enqueue(session, manifest, body.source_key, requested_by=user.id, trigger="api")
        record_audit(
            session,
            action="job.queued",
            outcome="success",
            actor_user_id=user.id,
            actor_label=user.email,
            target_type="ingestion_job",
            target_id=str(job.id),
            client_ip=_client_ip(request),
            details={"source_key": body.source_key},
        )
        job_id = job.id
    with request.app.state.session_factory() as session:
        return _job_dict(_load_job(session, job_id))


@router.post("/ingestion-jobs/{job_id}/retry", status_code=201)
def retry_job(job_id: uuid.UUID, request: Request, user: Operator) -> dict[str, Any]:
    manifest = request.app.state.manifest()
    with request.app.state.session_factory() as session, session.begin():
        job = service.retry(session, manifest, job_id, requested_by=user.id)
        record_audit(
            session,
            action="job.retried",
            outcome="success",
            actor_user_id=user.id,
            actor_label=user.email,
            target_type="ingestion_job",
            target_id=str(job.id),
            client_ip=_client_ip(request),
            details={"retry_of": str(job_id)},
        )
        new_id = job.id
    with request.app.state.session_factory() as session:
        return _job_dict(_load_job(session, new_id))


@router.post("/ingestion-jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, request: Request, user: Operator) -> dict[str, Any]:
    with request.app.state.session_factory() as session, session.begin():
        job = service.cancel(session, job_id, requested_by_label=user.email)
        record_audit(
            session,
            action="job.cancel_requested",
            outcome="success",
            actor_user_id=user.id,
            actor_label=user.email,
            target_type="ingestion_job",
            target_id=str(job_id),
            client_ip=_client_ip(request),
            details={"status": job.status.value},
        )
    with request.app.state.session_factory() as session:
        return _job_dict(_load_job(session, job_id))


@router.get("/processing-runs/{run_id}/logs")
def run_logs(run_id: uuid.UUID, request: Request, user: Viewer) -> dict[str, Any]:
    with request.app.state.session_factory() as session:
        run = session.scalar(
            select(ProcessingRun)
            .where(ProcessingRun.id == run_id)
            .options(selectinload(ProcessingRun.quality))
        )
        if run is None:
            raise NotFoundError("processing run not found")
        return _run_dict(run, with_logs=True)
