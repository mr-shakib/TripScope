"""ClickHouse schema migrations and verified, idempotent loads (spec §7.1 steps 10-11, ADR-03/04).

SQL safety: the only values interpolated into SQL text here are identifiers that passed
`validate_identifier`, the taxi type (regex-checked), an integer period and a lake path built by
`lake.s3_glob` from validated parts. Every other value is a bound server-side parameter.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import resources

from clickhouse_connect.driver.client import Client

from tripscope.core.errors import PipelineError
from tripscope.core.identifiers import validate_identifier
from tripscope.pipeline.spark_transform import CURATED_COLUMNS
from tripscope.pipeline.zones import Zone

log = logging.getLogger(__name__)

LAKE_COLLECTION = "tripscope_lake"


def apply_migrations(client: Client, database: str) -> list[str]:
    """Apply packaged `clickhouse_migrations/*.sql` files once each, in order. Returns versions applied."""
    db = validate_identifier(database)
    client.command(
        f"CREATE TABLE IF NOT EXISTS {db}.schema_migrations "
        "(version String, applied_at DateTime('UTC')) ENGINE = MergeTree ORDER BY version"
    )
    applied = {row[0] for row in client.query(f"SELECT version FROM {db}.schema_migrations").result_rows}
    package = resources.files("tripscope.pipeline") / "clickhouse_migrations"
    newly: list[str] = []
    for entry in sorted(package.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".sql") or entry.name in applied:
            continue
        client.command(entry.read_text().replace("{database}", db))
        client.insert(f"{db}.schema_migrations", [[entry.name, datetime.now(UTC)]], ["version", "applied_at"])
        newly.append(entry.name)
        log.info("applied clickhouse migration", extra={"version": entry.name, "database": db})
    return newly


@dataclass(frozen=True)
class LoadVerification:
    staged_rows: int
    published_rows: int
    staged_amount_sum: Decimal
    min_pickup_date: date
    max_pickup_date: date


def _yyyymm(period: date) -> int:
    return period.year * 100 + period.month


def load_partition(
    client: Client,
    *,
    database: str,
    lake_glob: str,
    taxi_type: str,
    period: date,
    run_id: str,
    expected_rows: int,
    expected_amount_sum: Decimal,
) -> LoadVerification:
    """Stage curated Parquet from the lake, verify it against Spark's metrics, then atomically replace
    the target partition. Nothing is visible unless every check passes; re-runs replace, never append."""
    db = validate_identifier(database)
    stage = validate_identifier(f"taxi_trips_stage_{uuid.UUID(run_id).hex}")
    if not re.fullmatch(r"[a-z]{2,16}", taxi_type):
        raise PipelineError(f"invalid taxi type {taxi_type!r}")
    if expected_rows <= 0:
        raise PipelineError("no accepted rows to load; refusing to publish an empty period")
    if "'" in lake_glob or "\\" in lake_glob:
        raise PipelineError("unsafe lake path")
    columns = ", ".join(CURATED_COLUMNS)
    params = {"taxi_type": taxi_type, "yyyymm": _yyyymm(period)}

    client.command(f"DROP TABLE IF EXISTS {db}.{stage}")
    client.command(f"CREATE TABLE {db}.{stage} AS {db}.taxi_trips")
    try:
        client.command(
            f"INSERT INTO {db}.{stage} ({columns}) "
            f"SELECT {columns} FROM s3({LAKE_COLLECTION}, filename = '{lake_glob}', format = 'Parquet') "
            # Lake paths are hive-style (year=/month=/run_id=) for humans; columns come from the files.
            "SETTINGS use_hive_partitioning = 0"
        )
        staged = client.query(
            "SELECT count(), sum(total_amount), min(pickup_date), max(pickup_date), "
            "countIf(taxi_type != {taxi_type:String}), countIf(toYYYYMM(pickup_date) != {yyyymm:UInt32}) "
            f"FROM {db}.{stage}",
            parameters=params,
        ).first_row
        if staged is None:
            raise PipelineError("staging verification query returned no row")
        rows, amount, min_date, max_date, wrong_type, wrong_period = staged
        amount = Decimal(amount or 0)
        problems = []
        if rows != expected_rows:
            problems.append(f"row count {rows} != Spark accepted rows {expected_rows}")
        if amount != expected_amount_sum:
            problems.append(f"sum(total_amount) {amount} != Spark {expected_amount_sum}")
        if wrong_type or wrong_period:
            problems.append(f"{wrong_type} rows with another taxi type, {wrong_period} outside the period")
        if problems:
            raise PipelineError("staging verification failed: " + "; ".join(problems))

        client.command(
            f"ALTER TABLE {db}.taxi_trips REPLACE PARTITION tuple('{taxi_type}', {_yyyymm(period)}) "
            f"FROM {db}.{stage}"
        )
        target = client.query(
            f"SELECT count(), sum(total_amount) FROM {db}.taxi_trips "
            "WHERE taxi_type = {taxi_type:String} AND toYYYYMM(pickup_date) = {yyyymm:UInt32}",
            parameters=params,
        ).first_row
        if target is None:
            raise PipelineError("post-publish verification query returned no row")
        published, published_amount = target
        if published != rows or Decimal(published_amount or 0) != amount:
            raise PipelineError(
                f"post-publish verification failed: {published} rows in target, {rows} staged"
            )
    finally:
        client.command(f"DROP TABLE IF EXISTS {db}.{stage}")
    return LoadVerification(rows, published, amount, min_date, max_date)


def load_zones(client: Client, *, database: str, zones: list[Zone], source_sha256: str) -> int:
    """Replace the zone lookup atomically (build a new table, then EXCHANGE)."""
    db = validate_identifier(database)
    client.command(f"DROP TABLE IF EXISTS {db}.taxi_zones_new")
    client.command(f"CREATE TABLE {db}.taxi_zones_new AS {db}.taxi_zones")
    try:
        loaded_at = datetime.now(UTC)
        rows = [
            [z.location_id, z.borough, z.zone, z.service_zone, z.is_geographic, source_sha256, loaded_at]
            for z in zones
        ]
        client.insert(
            f"{db}.taxi_zones_new",
            rows,
            ["location_id", "borough", "zone", "service_zone", "is_geographic", "source_sha256", "loaded_at"],
        )
        client.command(f"EXCHANGE TABLES {db}.taxi_zones AND {db}.taxi_zones_new")
    finally:
        client.command(f"DROP TABLE IF EXISTS {db}.taxi_zones_new")
    return len(rows)
