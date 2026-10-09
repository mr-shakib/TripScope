"""Data sources: manifest entries joined with what the pipeline has recorded about them (FR-02)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from tripscope.api.security import CurrentUser, require_roles
from tripscope.metadata.models import DatasetPeriod, DataSource, IngestionJob, Role

router = APIRouter(prefix="/data-sources", tags=["data sources"])


@router.get("")
def list_sources(
    request: Request,
    user: CurrentUser = Depends(require_roles(Role.ADMIN, Role.ANALYST)),  # noqa: B008
) -> dict[str, Any]:
    manifest = request.app.state.manifest()
    with request.app.state.session_factory() as session:
        registered = {s.source_key: s for s in session.scalars(select(DataSource)).all()}
        published = {(p.dataset_id, p.data_period): p for p in session.scalars(select(DatasetPeriod)).all()}
        latest_jobs: dict[Any, IngestionJob] = {}
        for job in session.scalars(select(IngestionJob).order_by(IngestionJob.created_at)).all():
            latest_jobs[job.data_source_id] = job  # last one wins
        items = []
        for source in manifest.sources:
            row = registered.get(source.key)
            period = published.get((source.dataset, source.period_start))
            job = latest_jobs.get(row.id) if row else None
            items.append(
                {
                    "source_key": source.key,
                    "dataset_id": source.dataset,
                    "period": source.period,
                    "format": source.format,
                    "uri": source.uri,
                    "checksum_pinned": source.expected_sha256 is not None,
                    "file_size_bytes": row.file_size_bytes if row else None,
                    "sha256": row.sha256 if row else None,
                    "schema_version": row.schema_version if row else None,
                    "retrieved_at": row.retrieved_at if row else None,
                    "published": None
                    if period is None
                    else {
                        "row_count": period.row_count,
                        "run_id": str(period.run_id),
                        "published_at": period.published_at,
                        "min_pickup_date": period.min_pickup_date,
                        "max_pickup_date": period.max_pickup_date,
                    },
                    "latest_job": None
                    if job is None
                    else {
                        "job_id": str(job.id),
                        "status": job.status.value,
                        "attempt": job.attempt,
                        "created_at": job.created_at,
                        "finished_at": job.finished_at,
                        "error_summary": job.error_summary,
                    },
                }
            )
    return {
        "sources": items,
        "attribution": manifest.datasets and next(iter(manifest.datasets.values())).source_attribution,
    }
