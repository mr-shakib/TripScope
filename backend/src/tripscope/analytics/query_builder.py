"""Allowlisted, parameterised ClickHouse query construction (spec §10.2).

Rules enforced here:
- Column names and aggregate expressions come only from constants in this package.
- Every filter value is a typed server-side parameter (`{name:Type}`); request text never enters SQL.
- Only published periods are queryable, and results are row-limited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from tripscope.analytics.filters import AnalyticsFilters, Granularity
from tripscope.analytics.metrics import METRICS
from tripscope.core.identifiers import validate_identifier

TRIPS_TABLE = "taxi_trips"
MAX_SERIES_POINTS = 5000

_BUCKETS: dict[str, str] = {
    "hour": "toStartOfHour(pickup_datetime)",
    "day": "pickup_date",
    "month": "toStartOfMonth(pickup_date)",
}


@dataclass(frozen=True)
class Query:
    sql: str
    parameters: dict[str, Any]


def _yyyymm(d: date) -> int:
    return d.year * 100 + d.month


def where_clause(filters: AnalyticsFilters, *, taxi_type: str, published_periods: list[date]) -> Query:
    conditions = [
        "taxi_type = {taxi_type:String}",
        "toYYYYMM(pickup_date) IN {published_months:Array(UInt32)}",
    ]
    params: dict[str, Any] = {
        "taxi_type": taxi_type,
        "published_months": sorted({_yyyymm(p) for p in published_periods}),
    }
    if filters.start_date is not None:
        conditions.append("pickup_date >= {start_date:Date}")
        params["start_date"] = filters.start_date
    if filters.end_date is not None:
        conditions.append("pickup_date <= {end_date:Date}")
        params["end_date"] = filters.end_date
    for field, column, ch_type in (
        ("pickup_zone", "pickup_location_id", "Int32"),
        ("dropoff_zone", "dropoff_location_id", "Int32"),
        ("payment_type", "payment_type", "Int32"),
        ("vendor_id", "vendor_id", "Int32"),
        ("hour", "pickup_hour", "UInt8"),
    ):
        values = getattr(filters, field)
        if values:
            conditions.append(f"{column} IN {{{field}:Array({ch_type})}}")
            params[field] = sorted(set(values))
    if filters.min_distance is not None:
        conditions.append("trip_distance >= {min_distance:Float64}")
        params["min_distance"] = filters.min_distance
    if filters.max_distance is not None:
        conditions.append("trip_distance <= {max_distance:Float64}")
        params["max_distance"] = filters.max_distance
    return Query(" AND ".join(conditions), params)


def overview_query(database: str, where: Query) -> Query:
    selects = [f"{m.expression} AS {m.id}" for m in METRICS.values()]
    selects += [
        f"{m.excluded_rows_expression} AS excluded__{m.id}"
        for m in METRICS.values()
        if m.excluded_rows_expression
    ]
    selects += [
        "min(pickup_date) AS first_date",
        "max(pickup_date) AS last_date",
        "uniqExact(pickup_date) AS days",
    ]
    sql = f"SELECT {', '.join(selects)} FROM {validate_identifier(database)}.{TRIPS_TABLE} WHERE {where.sql}"
    return Query(sql, dict(where.parameters))


def time_series_query(database: str, where: Query, *, metric: str, granularity: Granularity) -> Query:
    definition = METRICS[metric]  # KeyError for anything not allowlisted
    bucket = _BUCKETS[granularity]
    sql = (
        f"SELECT {bucket} AS bucket, {definition.expression} AS value, count() AS trips "
        f"FROM {validate_identifier(database)}.{TRIPS_TABLE} WHERE {where.sql} "
        f"GROUP BY bucket ORDER BY bucket LIMIT {MAX_SERIES_POINTS + 1}"
    )
    return Query(sql, dict(where.parameters))
