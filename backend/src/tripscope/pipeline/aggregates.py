"""Pre-aggregate definitions and builders (ADR-16).

Each aggregate is filled from a fact-shaped source table (the verified staging table during a run, or the
published `taxi_trips` partition for a backfill), verified against that source, and swapped in per partition
with `REPLACE PARTITION`. SQL safety: only validated identifiers and integer periods are interpolated.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from clickhouse_connect.driver.client import Client

from tripscope.core.errors import PipelineError
from tripscope.core.identifiers import validate_identifier

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AggregateSpec:
    table: str
    select: str  # SELECT list + GROUP BY over a fact-shaped source aliased `src`


AGGREGATES: tuple[AggregateSpec, ...] = (
    AggregateSpec(
        "trips_hourly_agg",
        """taxi_type, pickup_date, pickup_hour, pickup_day_of_week, pickup_location_id, payment_type,
           vendor_id,
           count() AS trips,
           countIf(is_amount_valid) AS amount_valid_trips,
           ifNull(sumIf(total_amount, is_amount_valid), 0) AS total_amount_valid_sum,
           countIf(is_distance_valid) AS distance_valid_trips,
           ifNull(sumIf(trip_distance, is_distance_valid), 0) AS trip_distance_valid_sum,
           countIf(is_duration_valid) AS duration_valid_trips,
           sumIf(trip_duration_minutes, is_duration_valid) AS trip_duration_valid_sum,
           any(run_id) AS run_id
        {where}
        GROUP BY taxi_type, pickup_date, pickup_hour, pickup_day_of_week, pickup_location_id, payment_type,
                 vendor_id""",
    ),
    AggregateSpec(
        "trips_dropoff_daily_agg",
        """taxi_type, pickup_date, dropoff_location_id, payment_type, vendor_id,
           count() AS trips,
           countIf(is_amount_valid) AS amount_valid_trips,
           ifNull(sumIf(total_amount, is_amount_valid), 0) AS total_amount_valid_sum,
           countIf(is_distance_valid) AS distance_valid_trips,
           ifNull(sumIf(trip_distance, is_distance_valid), 0) AS trip_distance_valid_sum,
           any(run_id) AS run_id
        {where}
        GROUP BY taxi_type, pickup_date, dropoff_location_id, payment_type, vendor_id""",
    ),
    AggregateSpec(
        "fare_distance_buckets",
        """taxi_type, pickup_date, metric, bucket_start, count() AS trips, any(run_id) AS run_id
        FROM (
            SELECT taxi_type, pickup_date, run_id, 'trip_distance' AS metric,
                   least(floor(assumeNotNull(trip_distance)), 50) AS bucket_start
            {where} AND is_distance_valid
            UNION ALL
            SELECT taxi_type, pickup_date, run_id, 'total_amount' AS metric,
                   least(floor(toFloat64(assumeNotNull(total_amount)) / 5) * 5, 200) AS bucket_start
            {where} AND is_amount_valid
        )
        GROUP BY taxi_type, pickup_date, metric, bucket_start""",
    ),
    AggregateSpec(
        "data_quality_daily",
        """taxi_type, pickup_date, arrayJoin(quality_flags) AS flag,
           count() AS flagged_trips, any(run_id) AS run_id
        {where}
        GROUP BY taxi_type, pickup_date, flag""",
    ),
)


def _yyyymm(period: date) -> int:
    return period.year * 100 + period.month


def _period_filter(taxi_type: str, period: date) -> str:
    if not re.fullmatch(r"[a-z]{2,16}", taxi_type):
        raise PipelineError(f"invalid taxi type {taxi_type!r}")
    return f"taxi_type = '{taxi_type}' AND toYYYYMM(pickup_date) = {_yyyymm(period)}"


def _one(client: Client, sql: str) -> Sequence[Any]:
    row = client.query(sql).first_row
    if row is None:
        raise PipelineError("aggregate verification query returned no row")
    return row


def build_aggregate_stages(
    client: Client, *, database: str, source_table: str, taxi_type: str, period: date, tag: str
) -> dict[str, str]:
    """Create and fill one staging table per aggregate from `source_table`; returns {aggregate: stage}."""
    db, source = validate_identifier(database), validate_identifier(source_table)
    period_filter = _period_filter(taxi_type, period)
    where = f"FROM {db}.{source} WHERE {period_filter}"
    stages: dict[str, str] = {}
    try:
        for spec in AGGREGATES:
            stage = validate_identifier(f"{spec.table}_stage_{tag}")
            client.command(f"DROP TABLE IF EXISTS {db}.{stage}")
            client.command(f"CREATE TABLE {db}.{stage} AS {db}.{spec.table}")
            stages[spec.table] = stage
            client.command(f"INSERT INTO {db}.{stage} SELECT {spec.select.format(where=where)}")
        _verify(client, db, f"{db}.{source}", stages, period_filter)
    except Exception:
        drop_stages(client, database=db, stages=stages)
        raise
    return stages


def _verify(client: Client, db: str, source: str, stages: dict[str, str], period_filter: str) -> None:
    """Aggregates must account for every staged trip and every valid dollar exactly."""
    expected = _one(
        client,
        "SELECT count(), ifNull(sumIf(total_amount, is_amount_valid), 0), countIf(is_distance_valid) "
        f"FROM {source} WHERE {period_filter}",
    )
    expected_trips, expected_amount, expected_distance_rows = (
        int(expected[0]),
        Decimal(expected[1]),
        int(expected[2]),
    )
    for table in ("trips_hourly_agg", "trips_dropoff_daily_agg"):
        trips, amount = _one(
            client, f"SELECT sum(trips), sum(total_amount_valid_sum) FROM {db}.{stages[table]}"
        )
        if int(trips) != expected_trips or Decimal(amount) != expected_amount:
            raise PipelineError(
                f"{table} verification failed: {trips} trips / {amount} "
                f"vs {expected_trips} / {expected_amount}"
            )
    buckets = stages["fare_distance_buckets"]
    bucketed = _one(client, f"SELECT sum(trips) FROM {db}.{buckets} WHERE metric = 'trip_distance'")[0]
    if int(bucketed or 0) != expected_distance_rows:
        raise PipelineError(
            f"fare_distance_buckets verification failed: {bucketed} vs {expected_distance_rows}"
        )


def swap_aggregates(
    client: Client, *, database: str, stages: dict[str, str], taxi_type: str, period: date
) -> None:
    db = validate_identifier(database)
    _period_filter(taxi_type, period)
    partition = f"tuple('{taxi_type}', {_yyyymm(period)})"
    for table, stage in stages.items():
        client.command(
            f"ALTER TABLE {db}.{validate_identifier(table)} REPLACE PARTITION {partition} "
            f"FROM {db}.{validate_identifier(stage)}"
        )


def drop_stages(client: Client, *, database: str, stages: dict[str, str]) -> None:
    for stage in stages.values():
        client.command(f"DROP TABLE IF EXISTS {validate_identifier(database)}.{validate_identifier(stage)}")


def rebuild_period(client: Client, *, database: str, taxi_type: str, period: date) -> dict[str, int]:
    """Backfill aggregates for one published month from `taxi_trips` (no Spark needed)."""
    stages = build_aggregate_stages(
        client,
        database=database,
        source_table="taxi_trips",
        taxi_type=taxi_type,
        period=period,
        tag=uuid.uuid4().hex,
    )
    try:
        swap_aggregates(client, database=database, stages=stages, taxi_type=taxi_type, period=period)
        db, period_filter = validate_identifier(database), _period_filter(taxi_type, period)
        return {
            table: int(_one(client, f"SELECT count() FROM {db}.{table} WHERE {period_filter}")[0])
            for table in stages
        }
    finally:
        drop_stages(client, database=database, stages=stages)
