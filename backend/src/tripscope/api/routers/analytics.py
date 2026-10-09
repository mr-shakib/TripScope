"""Dashboard analytics endpoints. Query parameters are validated by the shared filter models; unknown
parameters are rejected (422) rather than silently ignored."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response

from tripscope.analytics.filters import (
    BreakdownQuery,
    DistributionQuery,
    FlowQuery,
    OverviewQuery,
    TimeSeriesQuery,
    TopZonesQuery,
    ZoneQuery,
)
from tripscope.analytics.metrics import METRICS
from tripscope.api.security import AuthenticatedUser
from tripscope.core.errors import NotFoundError
from tripscope.pipeline.zone_geometry import GEOMETRY_KEY

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _filters(query: Any) -> Any:
    """The shared filter part of an endpoint-specific query model."""
    from tripscope.analytics.filters import AnalyticsFilters

    return AnalyticsFilters.model_validate(query.model_dump(include=set(AnalyticsFilters.model_fields)))


@router.get("/metrics")
def metric_catalogue(user: AuthenticatedUser) -> dict[str, Any]:
    return {"metrics": [m.public() for m in METRICS.values()]}


@router.get("/overview")
def overview(
    request: Request, user: AuthenticatedUser, query: Annotated[OverviewQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.overview(_filters(query), compare=query.compare))


@router.get("/trips-over-time")
def trips_over_time(
    request: Request, user: AuthenticatedUser, query: Annotated[TimeSeriesQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.time_series(query))


def _breakdown(
    request: Request, query: BreakdownQuery | TopZonesQuery | ZoneQuery, dimension: str, limit: int = 300
) -> dict[str, Any]:
    service = request.app.state.analytics
    return dict(service.breakdown(_filters(query), metric=query.metric, dimension=dimension, limit=limit))


@router.get("/trips-by-hour")
def trips_by_hour(
    request: Request, user: AuthenticatedUser, query: Annotated[BreakdownQuery, Query()]
) -> dict[str, Any]:
    return _breakdown(request, query, "hour")


@router.get("/trips-by-weekday")
def trips_by_weekday(
    request: Request, user: AuthenticatedUser, query: Annotated[BreakdownQuery, Query()]
) -> dict[str, Any]:
    return _breakdown(request, query, "weekday")


@router.get("/payment-types")
def payment_types(
    request: Request, user: AuthenticatedUser, query: Annotated[BreakdownQuery, Query()]
) -> dict[str, Any]:
    return _breakdown(request, query, "payment_type")


@router.get("/vendors")
def vendors(
    request: Request, user: AuthenticatedUser, query: Annotated[BreakdownQuery, Query()]
) -> dict[str, Any]:
    return _breakdown(request, query, "vendor_id")


@router.get("/top-pickup-zones")
def top_pickup_zones(
    request: Request, user: AuthenticatedUser, query: Annotated[TopZonesQuery, Query()]
) -> dict[str, Any]:
    return _breakdown(request, query, "pickup_zone", query.limit)


@router.get("/top-dropoff-zones")
def top_dropoff_zones(
    request: Request, user: AuthenticatedUser, query: Annotated[TopZonesQuery, Query()]
) -> dict[str, Any]:
    return _breakdown(request, query, "dropoff_zone", query.limit)


@router.get("/zone-totals")
def zone_totals(
    request: Request, user: AuthenticatedUser, query: Annotated[ZoneQuery, Query()]
) -> dict[str, Any]:
    """Every zone's value for the choropleth (≤ 265 rows)."""
    return _breakdown(request, query, f"{query.side}_zone", query.limit)


@router.get("/hour-weekday")
def hour_weekday(
    request: Request, user: AuthenticatedUser, query: Annotated[BreakdownQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.hour_weekday_matrix(_filters(query), metric=query.metric))


@router.get("/top-flows")
def top_flows(
    request: Request, user: AuthenticatedUser, query: Annotated[FlowQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.flows(_filters(query), metric=query.metric, limit=query.limit))


@router.get("/distribution")
def distribution(
    request: Request, user: AuthenticatedUser, query: Annotated[DistributionQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.distribution(_filters(query), metric=query.metric))


@router.get("/zones")
def zones(request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    """TLC zone lookup for filter pickers. IDs 264/265 are listed but flagged as non-geographic."""
    return {"zones": list(request.app.state.analytics.zones().values())}


@router.get("/zones/geometry")
def zone_geometry(request: Request, user: AuthenticatedUser) -> Response:
    """Simplified WGS84 zone boundaries (GeoJSON) built by the pipeline from the TLC shapefile."""
    cache = request.app.state.geometry_cache
    if cache.get("body") is None:
        body = request.app.state.object_store().get_bytes(GEOMETRY_KEY)
        if body is None:
            raise NotFoundError(
                "zone boundaries have not been built yet; run `tripscope-pipeline build-zone-geometry`"
            )
        json.loads(body)  # refuse to serve anything that is not valid JSON
        cache["body"] = body
    return Response(cache["body"], media_type="application/geo+json")
