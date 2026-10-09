"""Allowlisted, parameterised ClickHouse query construction (spec §10.2).

Rules enforced here:
- Column names and aggregate expressions come only from constants in this package.
- Every filter value is a typed server-side parameter (`{name:Type}`); request text never enters SQL.
- Only published periods are queryable, and results are row-limited.
- Queries read the hourly pre-aggregate whenever every requested filter is one of its dimensions (ADR-16);
  otherwise they read the fact table. Both produce the same numbers (integration-tested).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from tripscope.analytics.filters import AnalyticsFilters, Granularity
from tripscope.analytics.metrics import METRICS, MetricDefinition
from tripscope.core.identifiers import validate_identifier

MAX_SERIES_POINTS = 5000
MAX_GROUPS = 300


@dataclass(frozen=True)
class FactSource:
    """A table the analytics queries can read, with the expressions that differ between them."""

    table: str
    kind: Literal["raw", "aggregate"]
    trips: str
    hour_bucket: str

    def metric(self, definition: MetricDefinition) -> str:
        return definition.expression if self.kind == "raw" else definition.agg_expression

    def excluded(self, definition: MetricDefinition) -> str | None:
        return (
            definition.excluded_rows_expression
            if self.kind == "raw"
            else definition.agg_excluded_rows_expression
        )


RAW = FactSource("taxi_trips", "raw", "count()", "toStartOfHour(toDateTime(pickup_datetime))")
HOURLY_AGG = FactSource(
    "trips_hourly_agg", "aggregate", "sum(trips)", "toDateTime(pickup_date) + toIntervalHour(pickup_hour)"
)

_DATE_BUCKETS: dict[str, str] = {"day": "pickup_date", "month": "toStartOfMonth(pickup_date)"}

# Group-by dimensions callers may ask for. Every one exists in both RAW and HOURLY_AGG.
DIMENSIONS: dict[str, str] = {
    "hour": "pickup_hour",
    "weekday": "pickup_day_of_week",
    "pickup_zone": "pickup_location_id",
}


@dataclass(frozen=True)
class Query:
    sql: str
    parameters: dict[str, Any]


def choose_source(filters: AnalyticsFilters, *, prefer_raw: bool = False) -> FactSource:
    """The hourly aggregate has no drop-off zone or per-trip distance; those filters need the fact table."""
    needs_raw = (
        bool(filters.dropoff_zone) or filters.min_distance is not None or filters.max_distance is not None
    )
    return RAW if prefer_raw or needs_raw else HOURLY_AGG


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


def _table(database: str, source: FactSource) -> str:
    return f"{validate_identifier(database)}.{source.table}"


def overview_query(database: str, where: Query, source: FactSource = RAW) -> Query:
    selects = [f"{source.metric(m)} AS {m.id}" for m in METRICS.values()]
    selects += [f"{source.excluded(m)} AS excluded__{m.id}" for m in METRICS.values() if source.excluded(m)]
    selects += [
        "min(pickup_date) AS first_date",
        "max(pickup_date) AS last_date",
        "uniqExact(pickup_date) AS days",
    ]
    sql = f"SELECT {', '.join(selects)} FROM {_table(database, source)} WHERE {where.sql}"
    return Query(sql, dict(where.parameters))


def time_series_query(
    database: str, where: Query, *, metric: str, granularity: Granularity, source: FactSource = RAW
) -> Query:
    definition = METRICS[metric]  # KeyError for anything not allowlisted
    bucket = source.hour_bucket if granularity == "hour" else _DATE_BUCKETS[granularity]
    sql = (
        f"SELECT {bucket} AS bucket, {source.metric(definition)} AS value, {source.trips} AS trip_count "
        f"FROM {_table(database, source)} WHERE {where.sql} "
        f"GROUP BY bucket ORDER BY bucket LIMIT {MAX_SERIES_POINTS + 1}"
    )
    return Query(sql, dict(where.parameters))


def grouped_query(
    database: str,
    where: Query,
    *,
    metric: str,
    dimension: str,
    source: FactSource,
    order: Literal["dimension", "value"] = "dimension",
    limit: int = MAX_GROUPS,
) -> Query:
    definition = METRICS[metric]
    column = DIMENSIONS[dimension]
    order_by = "value DESC, trip_count DESC, bucket" if order == "value" else "bucket"
    sql = (
        f"SELECT {column} AS bucket, {source.metric(definition)} AS value, {source.trips} AS trip_count "
        f"FROM {_table(database, source)} WHERE {where.sql} "
        f"GROUP BY bucket ORDER BY {order_by} LIMIT {{group_limit:UInt32}}"
    )
    return Query(sql, {**where.parameters, "group_limit": min(max(limit, 1), MAX_GROUPS)})


def zones_query(database: str) -> Query:
    table = f"{validate_identifier(database)}.taxi_zones"
    return Query(
        f"SELECT location_id, borough, zone, service_zone, is_geographic FROM {table} ORDER BY location_id",
        {},
    )


def daily_quality_query(database: str, where: Query) -> Query:
    """Per-day flag counts plus the day's trip total (both from pre-aggregates; date filters only)."""
    db = validate_identifier(database)
    sql = (
        f"SELECT q.pickup_date AS day, q.flag AS flag, q.flagged AS flagged_trips, t.trips AS trips FROM "
        f"(SELECT pickup_date, flag, sum(flagged_trips) AS flagged FROM {db}.data_quality_daily "
        f"WHERE {where.sql} GROUP BY pickup_date, flag) AS q "
        f"INNER JOIN (SELECT pickup_date, sum(trips) AS trips FROM {db}.trips_hourly_agg "
        f"WHERE {where.sql} GROUP BY pickup_date) AS t ON q.pickup_date = t.pickup_date "
        f"ORDER BY day, flag LIMIT {MAX_SERIES_POINTS * 10}"
    )
    return Query(sql, dict(where.parameters))
