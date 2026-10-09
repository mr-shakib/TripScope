"""Datasets, their published coverage, schema registry and data-quality summaries."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from tripscope.analytics.filters import AnalyticsFilters
from tripscope.api.security import AuthenticatedUser
from tripscope.metadata.models import Dataset

router = APIRouter(prefix="/datasets", tags=["datasets"])


class QualityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: str | None = None
    end_date: str | None = None


@router.get("")
def list_datasets(request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    # Every authenticated role may read every published dataset (dataset-level grants arrive in Phase 6).
    service = request.app.state.analytics
    with request.app.state.session_factory() as session:
        ids = list(session.scalars(select(Dataset.id).order_by(Dataset.id)))
    return {"datasets": [service.coverage(dataset_id).as_dict() for dataset_id in ids]}


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    return dict(request.app.state.analytics.coverage(dataset_id).as_dict())


@router.get("/{dataset_id}/schema")
def get_schema(dataset_id: str, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    return dict(request.app.state.analytics.schema(dataset_id))


@router.get("/{dataset_id}/quality")
def get_quality(
    dataset_id: str, request: Request, user: AuthenticatedUser, query: Annotated[QualityQuery, Query()]
) -> dict[str, Any]:
    filters = AnalyticsFilters.model_validate(
        {"dataset_id": dataset_id, **query.model_dump(exclude_none=True)}
    )
    return dict(request.app.state.analytics.quality(filters))
