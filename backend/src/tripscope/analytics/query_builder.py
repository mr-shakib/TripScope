"""Allowlisted, parameterised ClickHouse query construction (spec §10.2).

Rules enforced here:
- Column names and aggregate expressions come only from constants in this package.
- Every filter value is a typed server-side parameter (`{name:Type}`); request text never enters SQL.
- Only published periods are queryable, and results are row-limited.
- Each table declares the filters, dimensions and metrics it can answer. A request goes to the first
  (cheapest) table that covers all of them (ADR-16); answers are identical across tables (integration-tested).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from tripscope.analytics.filters import AnalyticsFilters, Granularity
from tripscope.analytics.metrics import METRICS
from tripscope.core.identifiers import validate_identifier

MAX_SERIES_POINTS = 5000
MAX_GROUPS = 300

ALL_FILTERS = frozenset(
    {"date", "pickup_zone", "dropoff_zone", "payment_type", "vendor_id", "hour", "weekday", "distance"}
)
_TIME = {"time:day": "pickup_date", "time:month": "toStartOfMonth(pickup_date)"}


@dataclass(frozen=True)
class FactSource:
    """A table analytics can read and exactly what it can answer."""

    table: str
    kind: Literal["raw", "aggregate"]
    trips: str
    filters: frozenset[str]
    dimensions: dict[str, str]
    metric_ids: frozenset[str] = field(default_factory=lambda: frozenset(METRICS))

    def metric(self, metric_id: str) -> str:
        definition = METRICS[metric_id]
        return definition.expression if self.kind == "raw" else definition.agg_expression

    def excluded(self, metric_id: str) -> str | None:
        definition = METRICS[metric_id]
        return (
            definition.excluded_rows_expression
            if self.kind == "raw"
            else definition.agg_excluded_rows_expression
        )

    def covers(self, active_filters: set[str], dimensions: Sequence[str], metric_ids: Sequence[str]) -> bool:
        return (
            active_filters <= self.filters
            and all(d in self.dimensions for d in dimensions)
            and all(m in self.metric_ids for m in metric_ids)
        )


RAW = FactSource(
    table="taxi_trips",
    kind="raw",
    trips="count()",
    filters=ALL_FILTERS,
    dimensions={
        **_TIME,
        "time:hour": "toStartOfHour(toDateTime(pickup_datetime))",
        "hour": "pickup_hour",
        "weekday": "pickup_day_of_week",
        "pickup_zone": "pickup_location_id",
        "dropoff_zone": "dropoff_location_id",
        "payment_type": "payment_type",
        "vendor_id": "vendor_id",
    },
)
HOURLY_AGG = FactSource(
    table="trips_hourly_agg",
    kind="aggregate",
    trips="sum(trips)",
    filters=frozenset({"date", "pickup_zone", "payment_type", "vendor_id", "hour", "weekday"}),
    dimensions={
        **_TIME,
        "time:hour": "toDateTime(pickup_date) + toIntervalHour(pickup_hour)",
        "hour": "pickup_hour",
        "weekday": "pickup_day_of_week",
        "pickup_zone": "pickup_location_id",
        "payment_type": "payment_type",
        "vendor_id": "vendor_id",
    },
)
DROPOFF_AGG = FactSource(
    table="trips_dropoff_daily_agg",
    kind="aggregate",
    trips="sum(trips)",
    filters=frozenset({"date", "dropoff_zone", "payment_type", "vendor_id"}),
    dimensions={
        **_TIME,
        "dropoff_zone": "dropoff_location_id",
        "payment_type": "payment_type",
        "vendor_id": "vendor_id",
    },
    # No duration columns in this aggregate.
    metric_ids=frozenset(m for m in METRICS if m != "avg_trip_duration_minutes"),
)
SOURCES: tuple[FactSource, ...] = (HOURLY_AGG, DROPOFF_AGG, RAW)  # cheapest first


@dataclass(frozen=True)
class Query:
    sql: str
    parameters: dict[str, Any]


def active_filters(filters: AnalyticsFilters) -> set[str]:
    active = set()
    if filters.start_date or filters.end_date:
        active.add("date")
    for name in ("pickup_zone", "dropoff_zone", "payment_type", "vendor_id", "hour", "weekday"):
        if getattr(filters, name):
            active.add(name)
    if filters.min_distance is not None or filters.max_distance is not None:
        active.add("distance")
    return active


def choose_source(
    filters: AnalyticsFilters,
    *,
    dimensions: Sequence[str] = (),
    metrics: Sequence[str] = (),
    prefer_raw: bool = False,
) -> FactSource:
    if prefer_raw:
        return RAW
    active = active_filters(filters)
    return next(s for s in SOURCES if s.covers(active, dimensions, metrics))  # RAW covers everything


def _yyyymm(d: date) -> int:
    return d.year * 100 + d.month


QualityScope = Literal["all", "clean", "flagged"]


def where_clause(
    filters: AnalyticsFilters,
    *,
    taxi_type: str,
    published_periods: list[date],
    quality: QualityScope = "all",
    flag: str | None = None,
) -> Query:
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
    for name, column, ch_type in (
        ("pickup_zone", "pickup_location_id", "Int32"),
        ("dropoff_zone", "dropoff_location_id", "Int32"),
        ("payment_type", "payment_type", "Int32"),
        ("vendor_id", "vendor_id", "Int32"),
        ("hour", "pickup_hour", "UInt8"),
        ("weekday", "pickup_day_of_week", "UInt8"),
    ):
        values = getattr(filters, name)
        if values:
            conditions.append(f"{column} IN {{{name}:Array({ch_type})}}")
            params[name] = sorted(set(values))
    if filters.min_distance is not None:
        conditions.append("trip_distance >= {min_distance:Float64}")
        params["min_distance"] = filters.min_distance
    if filters.max_distance is not None:
        conditions.append("trip_distance <= {max_distance:Float64}")
        params["max_distance"] = filters.max_distance
    # Row-level quality scopes exist only on the fact table (explorer and extracts).
    if quality == "clean":
        conditions.append("empty(quality_flags)")
    elif quality == "flagged":
        conditions.append("notEmpty(quality_flags)")
    if flag is not None:
        conditions.append("has(quality_flags, {flag:String})")
        params["flag"] = flag
    return Query(" AND ".join(conditions), params)


def _table(database: str, source: FactSource) -> str:
    return f"{validate_identifier(database)}.{source.table}"


def overview_query(database: str, where: Query, source: FactSource = RAW) -> Query:
    selects = [f"{source.metric(m)} AS {m}" for m in METRICS]
    selects += [f"{source.excluded(m)} AS excluded__{m}" for m in METRICS if source.excluded(m)]
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
    bucket = source.dimensions[f"time:{granularity}"]
    sql = (
        f"SELECT {bucket} AS bucket, {source.metric(metric)} AS value, {source.trips} AS trip_count "
        f"FROM {_table(database, source)} WHERE {where.sql} "
        f"GROUP BY bucket ORDER BY bucket LIMIT {MAX_SERIES_POINTS + 1}"
    )
    return Query(sql, dict(where.parameters))


def grouped_query(
    database: str,
    where: Query,
    *,
    metric: str,
    dimensions: Sequence[str],
    source: FactSource,
    order: Literal["dimension", "value"] = "dimension",
    limit: int = MAX_GROUPS,
) -> Query:
    """Group by one or more allowlisted dimensions (KeyError for anything else)."""
    columns = [f"{source.dimensions[d]} AS d{i}" for i, d in enumerate(dimensions)]
    keys = ", ".join(f"d{i}" for i in range(len(dimensions)))
    order_by = f"value DESC, trip_count DESC, {keys}" if order == "value" else keys
    sql = (
        f"SELECT {', '.join(columns)}, {source.metric(metric)} AS value, {source.trips} AS trip_count "
        f"FROM {_table(database, source)} WHERE {where.sql} "
        f"GROUP BY {keys} ORDER BY {order_by} LIMIT {{group_limit:UInt32}}"
    )
    return Query(sql, {**where.parameters, "group_limit": min(max(limit, 1), MAX_GROUPS)})


# Histogram buckets (identical on both paths): 1-mile buckets to 50+, $5 buckets to $200+ (outliers capped).
DISTRIBUTIONS: dict[str, dict[str, str]] = {
    "trip_distance": {
        "valid": "is_distance_valid",
        "bucket": "least(floor(assumeNotNull(trip_distance)), 50)",
    },
    "total_amount": {
        "valid": "is_amount_valid",
        "bucket": "least(floor(toFloat64(assumeNotNull(total_amount)) / 5) * 5, 200)",
    },
}


def distribution_query(database: str, where: Query, *, metric: str, from_buckets: bool) -> Query:
    db = validate_identifier(database)
    if from_buckets:
        sql = (
            f"SELECT bucket_start AS bucket, sum(trips) AS trips FROM {db}.fare_distance_buckets "
            f"WHERE metric = {{distribution_metric:String}} AND {where.sql} GROUP BY bucket ORDER BY bucket"
        )
        return Query(sql, {**where.parameters, "distribution_metric": metric})
    spec = DISTRIBUTIONS[metric]
    sql = (
        f"SELECT {spec['bucket']} AS bucket, count() AS trips FROM {db}.taxi_trips "
        f"WHERE {where.sql} AND {spec['valid']} GROUP BY bucket ORDER BY bucket"
    )
    return Query(sql, dict(where.parameters))


# Explorer: columns callers can see and sort by (all from the curated fact table).
EXPLORER_COLUMNS: tuple[str, ...] = (
    "pickup_datetime",
    "dropoff_datetime",
    "pickup_location_id",
    "dropoff_location_id",
    "trip_distance",
    "trip_duration_minutes",
    "passenger_count",
    "payment_type",
    "vendor_id",
    "rate_code_id",
    "fare_amount",
    "tip_amount",
    "tolls_amount",
    "total_amount",
    "congestion_surcharge",
    "airport_fee",
    "cbd_congestion_fee",
    "average_speed_mph",
    "quality_flags",
    "source_file",
    "run_id",
)
SORTABLE = (
    "pickup_datetime",
    "trip_distance",
    "trip_duration_minutes",
    "total_amount",
    "fare_amount",
    "tip_amount",
    "passenger_count",
    "average_speed_mph",
)


def rows_query(
    database: str,
    where: Query,
    *,
    sort: str,
    order: Literal["asc", "desc"],
    limit: int,
    offset: int,
    columns: Sequence[str] = EXPLORER_COLUMNS,
) -> Query:
    if sort not in SORTABLE or any(c not in EXPLORER_COLUMNS for c in columns):
        raise KeyError("column not allowlisted")
    direction = "DESC" if order == "desc" else "ASC"
    tie_break = ", pickup_datetime, dropoff_datetime" if sort != "pickup_datetime" else ", dropoff_datetime"
    sql = (
        f"SELECT {', '.join(columns)} FROM {validate_identifier(database)}.taxi_trips WHERE {where.sql} "
        f"ORDER BY {sort} {direction} NULLS LAST{tie_break} "
        "LIMIT {row_limit:UInt32} OFFSET {row_offset:UInt32}"
    )
    return Query(sql, {**where.parameters, "row_limit": limit, "row_offset": offset})


def count_query(database: str, where: Query) -> Query:
    return Query(
        f"SELECT count() FROM {validate_identifier(database)}.taxi_trips WHERE {where.sql}",
        dict(where.parameters),
    )


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
