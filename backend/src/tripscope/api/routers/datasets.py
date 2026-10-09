"""Datasets and their published coverage."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from tripscope.api.security import AuthenticatedUser
from tripscope.metadata.models import Dataset

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("")
def list_datasets(request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    # Phase 1: every authenticated role may read every published dataset (dataset grants arrive in Phase 6).
    service = request.app.state.analytics
    with request.app.state.session_factory() as session:
        ids = list(session.scalars(select(Dataset.id).order_by(Dataset.id)))
    return {"datasets": [service.coverage(dataset_id).as_dict() for dataset_id in ids]}


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    return dict(request.app.state.analytics.coverage(dataset_id).as_dict())
