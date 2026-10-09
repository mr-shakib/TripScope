"""PySpark transformation of one source file (spec §7.1 steps 5-9).

normalize → detect duplicates → quarantine reasons → derived fields → quality flags → write curated
and quarantine Parquet → compute run-level quality metrics. The annotated DataFrame is persisted once so
the metric aggregation and both writes reuse the same shuffle.

Timestamps: TLC Parquet stores NYC local wall-clock times (`isAdjustedToUTC=false`), which Spark reads as
TIMESTAMP_NTZ. The session timezone is UTC so no implicit conversion ever happens (ADR-06).
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pyspark import StorageLevel
from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from tripscope.core.errors import PipelineError
from tripscope.pipeline.canonical import CANONICAL_FIELDS, CANONICAL_NAMES, SchemaMapping
from tripscope.pipeline.rules import FLAGS, QUARANTINE_REASONS, QualityRules

log = logging.getLogger(__name__)

MONEY = T.DecimalType(14, 2)
SPARK_TYPES: dict[str, T.DataType] = {
    "int": T.IntegerType(),
    "long": T.LongType(),
    "double": T.DoubleType(),
    "money": MONEY,
    "string": T.StringType(),
    "timestamp": T.TimestampNTZType(),
}

DERIVED_COLUMNS = (
    "pickup_date",
    "pickup_hour",
    "pickup_day_of_week",
    "trip_duration_minutes",
    "average_speed_mph",
    "fare_per_mile",
    "is_distance_valid",
    "is_duration_valid",
    "is_amount_valid",
    "quality_flags",
)
LINEAGE_COLUMNS = ("source_file", "run_id", "ingested_at", "data_period")
CURATED_COLUMNS: tuple[str, ...] = ("taxi_type", *CANONICAL_NAMES, *DERIVED_COLUMNS, *LINEAGE_COLUMNS)
ROWS_PER_CURATED_FILE = 1_000_000


@dataclass(frozen=True)
class TransformContext:
    run_id: str
    taxi_type: str
    source_file: str
    period_start: date
    period_end_exclusive: date
    ingested_at: datetime
    mapping: SchemaMapping
    rules: QualityRules
    mapped_zone_ids: list[int]


@dataclass
class TransformResult:
    input_rows: int
    accepted_rows: int
    quarantined_rows: int
    duplicate_rows: int
    invalid_timestamp_rows: int
    unmapped_location_rows: int
    quarantine_reason_counts: dict[str, int]
    flag_counts: dict[str, int]
    missingness_by_column: dict[str, dict[str, Any]]
    cast_failure_counts: dict[str, int]
    accepted_total_amount_sum: Decimal
    min_pickup_date: date | None
    max_pickup_date: date | None
    curated_dir: Path
    quarantine_dir: Path
    spark_version: str
    extra: dict[str, Any] = field(default_factory=dict)


def check_java(java_home: Path | None) -> str:
    """Fail early with an actionable message if the JVM cannot run Spark 4 (Java 17+ with all modules).

    Checks the same JVM PySpark will launch: `java_home`, else $JAVA_HOME, else `java` on PATH.
    """
    home = java_home or (Path(os.environ["JAVA_HOME"]) if os.environ.get("JAVA_HOME") else None)
    java = str(home / "bin" / "java") if home else shutil.which("java")
    if not java or not Path(java).exists():
        raise PipelineError("Java not found. Install JDK 17+ or set SPARK_JAVA_HOME to a full JDK.")
    command = [java, "--list-modules"]  # java path comes from configuration, never from user input
    modules = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
    if "jdk.incubator.vector" not in modules.stdout:
        raise PipelineError(
            f"The JVM at {java} lacks modules Spark 4 needs (jdk.incubator.vector); it is probably a trimmed "
            "runtime. Set SPARK_JAVA_HOME to a full JDK 17+ (e.g. /usr/lib/jvm/java-21-openjdk-amd64)."
        )
    return java


def build_spark_session(
    *, master: str, driver_memory: str, local_dir: Path, java_home: Path | None = None
) -> SparkSession:
    check_java(java_home)
    if java_home is not None:
        os.environ["JAVA_HOME"] = str(java_home)
    local_dir.mkdir(parents=True, exist_ok=True)
    return (
        SparkSession.builder.master(master)
        .appName("tripscope-pipeline")
        .config("spark.driver.memory", driver_memory)
        .config("spark.driver.extraJavaOptions", "-Duser.timezone=UTC")
        .config("spark.executor.extraJavaOptions", "-Duser.timezone=UTC")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.sql.parquet.compression.codec", "zstd")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        .config("spark.sql.warehouse.dir", str(local_dir / "warehouse"))
        .config("spark.local.dir", str(local_dir / "tmp"))
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )


def _source_column(df: DataFrame, name: str) -> Column:
    column = F.col(f"`{name}`")
    source_type = df.schema[name].dataType
    if isinstance(source_type, T.TimestampType):
        # A UTC-adjusted source timestamp: convert explicitly to NYC wall-clock time (never implicit).
        return F.from_utc_timestamp(column, "America/New_York").cast(T.TimestampNTZType())
    return column


def normalize(df: DataFrame, mapping: SchemaMapping) -> DataFrame:
    """Select canonical columns with explicit types; absent fields become typed NULLs.

    `try_cast` keeps a malformed value from aborting the run; every such value is counted in
    `_cast_failures` so it is reported, not silently lost.
    """
    columns: list[Column] = []
    failures: list[Column] = []
    for canonical in CANONICAL_FIELDS:
        target = SPARK_TYPES[canonical.kind]
        source = mapping.mapping.get(canonical.name)
        if source is None:
            columns.append(F.lit(None).cast(target).alias(canonical.name))
            continue
        raw = _source_column(df, source)
        typed = raw.try_cast(target)
        columns.append(typed.alias(canonical.name))
        failures.append(F.when(raw.isNotNull() & typed.isNull(), F.lit(canonical.name)))
    cast_failures = F.array_compact(F.array(*failures)) if failures else F.array().cast("array<string>")
    return df.select(*columns, cast_failures.alias("_cast_failures"))


def _ts_literal(value: date) -> Column:
    return F.lit(value.isoformat() + " 00:00:00").cast(T.TimestampNTZType())


def annotate(normalized: DataFrame, ctx: TransformContext) -> DataFrame:
    rules = ctx.rules
    pickup, dropoff = F.col("pickup_datetime"), F.col("dropoff_datetime")

    # Exact duplicates across every canonical field; the first occurrence (by read order) is kept.
    with_ids = normalized.withColumn("_row_id", F.monotonically_increasing_id())
    duplicate_window = Window.partitionBy(*[F.col(c) for c in CANONICAL_NAMES]).orderBy("_row_id")
    df = with_ids.withColumn("_dup_rank", F.row_number().over(duplicate_window))

    reasons = F.array_compact(
        F.array(
            F.when(pickup.isNull() | dropoff.isNull(), F.lit("missing_required_timestamp")),
            F.when(dropoff < pickup, F.lit("dropoff_before_pickup")),
            F.when(
                (pickup < _ts_literal(ctx.period_start)) | (pickup >= _ts_literal(ctx.period_end_exclusive)),
                F.lit("pickup_outside_source_period"),
            ),
            F.when(F.col("_dup_rank") > 1, F.lit("duplicate_record")),
        )
    )
    df = df.withColumn("quarantine_reasons", reasons)

    duration_minutes = (
        F.unix_micros(dropoff.cast(T.TimestampType())) - F.unix_micros(pickup.cast(T.TimestampType()))
    ) / F.lit(60_000_000.0)
    distance = F.col("trip_distance")
    total, fare = F.col("total_amount"), F.col("fare_amount")
    passengers = F.col("passenger_count")

    def flag(condition: Column) -> Column:
        return F.coalesce(condition, F.lit(False))

    distance_valid = flag(distance.isNotNull() & (distance > 0) & (distance <= rules.max_trip_distance_miles))
    duration_valid = flag(
        duration_minutes.isNotNull()
        & (duration_minutes > 0)
        & (duration_minutes <= rules.max_trip_duration_minutes)
    )
    amount_valid = flag(
        total.isNotNull()
        & (total >= 0)
        & (total <= F.lit(rules.max_total_amount_usd).cast(MONEY))
        & (fare.isNull() | (fare >= 0))
    )
    df = (
        df.withColumn("pickup_date", F.to_date(pickup))
        .withColumn("pickup_hour", F.hour(pickup))
        .withColumn("pickup_day_of_week", ((F.dayofweek(pickup) + 5) % 7) + 1)  # ISO: Monday=1 … Sunday=7
        .withColumn("trip_duration_minutes", duration_minutes)
        .withColumn("is_distance_valid", distance_valid)
        .withColumn("is_duration_valid", duration_valid)
        .withColumn("is_amount_valid", amount_valid)
    )
    raw_speed = F.when(
        F.col("is_distance_valid") & F.col("is_duration_valid"),
        F.try_divide(distance, F.col("trip_duration_minutes") / F.lit(60.0)),
    )
    df = df.withColumn("_raw_speed", raw_speed)
    implausible_speed = flag(F.col("_raw_speed") > rules.max_average_speed_mph)
    df = df.withColumn("_implausible_speed", implausible_speed).withColumn(
        "average_speed_mph", F.when(~F.col("_implausible_speed"), F.col("_raw_speed"))
    )
    df = df.withColumn(
        "fare_per_mile",
        F.when(
            F.col("is_distance_valid") & F.col("is_amount_valid") & fare.isNotNull(),
            F.try_divide(fare.cast(T.DoubleType()), distance),
        ),
    )

    zone_ids = ctx.mapped_zone_ids
    pickup_zone, dropoff_zone = F.col("pickup_location_id"), F.col("dropoff_location_id")
    flags = F.array_compact(
        F.array(
            F.when(~F.col("is_distance_valid"), F.lit("invalid_distance")),
            F.when(~F.col("is_duration_valid"), F.lit("invalid_duration")),
            F.when(~F.col("is_amount_valid"), F.lit("invalid_amount")),
            F.when(F.col("_implausible_speed"), F.lit("implausible_speed")),
            F.when(passengers.isNull() | (passengers == 0), F.lit("passenger_count_missing_or_zero")),
            F.when(pickup_zone.isNull() | ~pickup_zone.isin(zone_ids), F.lit("pickup_zone_unmapped")),
            F.when(dropoff_zone.isNull() | ~dropoff_zone.isin(zone_ids), F.lit("dropoff_zone_unmapped")),
        )
    )
    return (
        df.withColumn("quality_flags", flags)
        .withColumn("taxi_type", F.lit(ctx.taxi_type))
        .withColumn("source_file", F.lit(ctx.source_file))
        .withColumn("run_id", F.lit(ctx.run_id))
        .withColumn("ingested_at", F.lit(ctx.ingested_at).cast(T.TimestampType()))
        .withColumn("data_period", F.lit(ctx.period_start).cast(T.DateType()))
    )


def _count_when(condition: Column) -> Column:
    return F.sum(F.when(condition, 1).otherwise(0))


def compute_metrics(annotated: DataFrame, ctx: TransformContext) -> dict[str, Any]:
    accepted = F.size("quarantine_reasons") == 0

    def has_reason(reason: str) -> Column:
        return F.array_contains("quarantine_reasons", reason)

    aggregations = [
        F.count(F.lit(1)).alias("input_rows"),
        _count_when(accepted).alias("accepted_rows"),
        _count_when(has_reason("missing_required_timestamp") | has_reason("dropoff_before_pickup")).alias(
            "invalid_timestamp_rows"
        ),
        _count_when(
            accepted
            & (
                F.array_contains("quality_flags", "pickup_zone_unmapped")
                | F.array_contains("quality_flags", "dropoff_zone_unmapped")
            )
        ).alias("unmapped_location_rows"),
        F.sum(F.when(accepted, F.col("total_amount"))).alias("accepted_total_amount_sum"),
        F.min(F.when(accepted, F.col("pickup_date"))).alias("min_pickup_date"),
        F.max(F.when(accepted, F.col("pickup_date"))).alias("max_pickup_date"),
    ]
    aggregations += [_count_when(has_reason(r)).alias(f"reason__{r}") for r in QUARANTINE_REASONS]
    aggregations += [
        _count_when(accepted & F.array_contains("quality_flags", f)).alias(f"flag__{f}") for f in FLAGS
    ]
    aggregations += [_count_when(F.col(c).isNull()).alias(f"null__{c}") for c in CANONICAL_NAMES]
    aggregations += [
        _count_when(F.array_contains("_cast_failures", c)).alias(f"castfail__{c}")
        for c in ctx.mapping.mapping
    ]
    row: dict[str, Any] = annotated.agg(*aggregations).collect()[0].asDict()
    count_prefixes = ("reason__", "flag__", "null__", "castfail__")
    for key, value in row.items():
        if value is None and (key.startswith(count_prefixes) or key.endswith("_rows")):
            row[key] = 0  # SUM over zero rows is NULL; a count of nothing is 0
    return row


def transform_file(
    spark: SparkSession, source_path: Path, ctx: TransformContext, output_dir: Path
) -> TransformResult:
    curated_dir = output_dir / "curated"
    quarantine_dir = output_dir / "quarantine"

    raw = spark.read.parquet(str(source_path))
    annotated = annotate(normalize(raw, ctx.mapping), ctx).persist(StorageLevel.MEMORY_AND_DISK)
    try:
        metrics = compute_metrics(annotated, ctx)
        accepted_rows = int(metrics["accepted_rows"])
        files = max(1, math.ceil(accepted_rows / ROWS_PER_CURATED_FILE))
        accepted = annotated.filter(F.size("quarantine_reasons") == 0).select(*CURATED_COLUMNS)
        (
            accepted.repartitionByRange(files, "pickup_datetime")
            .sortWithinPartitions("pickup_datetime")
            .write.mode("overwrite")
            .parquet(str(curated_dir))
        )
        quarantined = annotated.filter(F.size("quarantine_reasons") > 0).select(
            "taxi_type", *CANONICAL_NAMES, "quarantine_reasons", "_cast_failures", *LINEAGE_COLUMNS
        )
        quarantined.coalesce(1).write.mode("overwrite").parquet(str(quarantine_dir))
    finally:
        annotated.unpersist()

    input_rows = int(metrics["input_rows"])
    available = set(ctx.mapping.mapping)
    missingness = {
        c: {
            "null_count": int(metrics[f"null__{c}"]),
            "null_pct": round(100.0 * int(metrics[f"null__{c}"]) / input_rows, 4) if input_rows else None,
        }
        for c in CANONICAL_NAMES
        if c in available
    }
    total_sum = metrics["accepted_total_amount_sum"]
    return TransformResult(
        input_rows=input_rows,
        accepted_rows=accepted_rows,
        quarantined_rows=input_rows - accepted_rows,
        duplicate_rows=int(metrics["reason__duplicate_record"]),
        invalid_timestamp_rows=int(metrics["invalid_timestamp_rows"]),
        unmapped_location_rows=int(metrics["unmapped_location_rows"]),
        quarantine_reason_counts={r: int(metrics[f"reason__{r}"]) for r in QUARANTINE_REASONS},
        flag_counts={f: int(metrics[f"flag__{f}"]) for f in FLAGS},
        missingness_by_column=missingness,
        cast_failure_counts={c: int(metrics[f"castfail__{c}"]) for c in ctx.mapping.mapping},
        accepted_total_amount_sum=Decimal(total_sum) if total_sum is not None else Decimal("0.00"),
        min_pickup_date=metrics["min_pickup_date"],
        max_pickup_date=metrics["max_pickup_date"],
        curated_dir=curated_dir,
        quarantine_dir=quarantine_dir,
        spark_version=spark.version,
    )
