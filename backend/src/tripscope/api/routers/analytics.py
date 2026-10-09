"""Dashboard analytics endpoints. Query parameters are validated by the shared filter models; unknown
parameters are rejected (422) rather than silently ignored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from tripscope.analytics.filters import AnalyticsFilters, TimeSeriesQuery
from tripscope.api.security import AuthenticatedUser

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def overview(
    request: Request, user: AuthenticatedUser, filters: Annotated[AnalyticsFilters, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.overview(filters))


@router.get("/trips-over-time")
def trips_over_time(
    request: Request, user: AuthenticatedUser, query: Annotated[TimeSeriesQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.time_series(query))
