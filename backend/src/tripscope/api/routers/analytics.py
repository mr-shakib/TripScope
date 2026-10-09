"""Dashboard analytics endpoints. Query parameters are validated by the shared filter models; unknown
parameters are rejected (422) rather than silently ignored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from tripscope.analytics.filters import AnalyticsFilters, BreakdownQuery, TimeSeriesQuery, TopZonesQuery
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


@router.get("/trips-by-hour")
def trips_by_hour(
    request: Request, user: AuthenticatedUser, query: Annotated[BreakdownQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.breakdown(query, metric=query.metric, dimension="hour"))


@router.get("/trips-by-weekday")
def trips_by_weekday(
    request: Request, user: AuthenticatedUser, query: Annotated[BreakdownQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.breakdown(query, metric=query.metric, dimension="weekday"))


@router.get("/top-pickup-zones")
def top_pickup_zones(
    request: Request, user: AuthenticatedUser, query: Annotated[TopZonesQuery, Query()]
) -> dict[str, Any]:
    return dict(
        request.app.state.analytics.breakdown(
            query, metric=query.metric, dimension="pickup_zone", limit=query.limit
        )
    )


@router.get("/zones")
def zones(request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    """TLC zone lookup for filter pickers. IDs 264/265 are listed but flagged as non-geographic."""
    return {"zones": list(request.app.state.analytics.zones().values())}
