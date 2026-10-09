"""Ingestion job history (read-only in Phase 1; create/retry/cancel arrive with the worker in Phase 2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tripscope.api.security import CurrentUser, require_roles
from tripscope.metadata.models import IngestionJob, ProcessingRun, Role

router = APIRouter(prefix="/ingestion-jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    request: Request,
    user: CurrentUser = Depends(require_roles(Role.ADMIN, Role.ANALYST)),  # noqa: B008
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    with request.app.state.session_factory() as session:
        jobs = session.scalars(
            select(IngestionJob)
            .options(
                selectinload(IngestionJob.data_source),
                selectinload(IngestionJob.runs).selectinload(ProcessingRun.quality),
            )
            .order_by(IngestionJob.created_at.desc())
            .limit(limit)
        ).all()
        return {"jobs": [_job(job) for job in jobs]}


def _job(job: IngestionJob) -> dict[str, Any]:
    runs = []
    for run in job.runs:
        quality = run.quality
        runs.append(
            {
                "run_id": str(run.id),
                "status": run.status.value,
                "current_stage": run.current_stage,
                "duration_seconds": run.duration_seconds,
                "stage_timings": run.stage_timings,
                "schema_version": run.schema_version,
                "unavailable_fields": run.unavailable_fields,
                "error_summary": run.error_summary,
                "input_rows": quality.input_row_count if quality else None,
                "accepted_rows": quality.accepted_row_count if quality else None,
                "quarantined_rows": quality.quarantined_row_count if quality else None,
            }
        )
    return {
        "job_id": str(job.id),
        "source_key": job.data_source.source_key,
        "status": job.status.value,
        "attempt": job.attempt,
        "trigger": job.trigger,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_summary": job.error_summary,
        "runs": runs,
    }
