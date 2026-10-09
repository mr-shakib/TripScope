from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from tests.fixtures.tlc_fixture import PERIOD_START, ZONE_IDS, FixtureRow, write_fixture_parquet
from tripscope.pipeline.acquire import inspect_parquet
from tripscope.pipeline.canonical import map_source_schema
from tripscope.pipeline.rules import FLAGS, QUARANTINE_REASONS, QualityRules
from tripscope.pipeline.spark_transform import (
    CURATED_COLUMNS,
    TransformContext,
    TransformResult,
    transform_file,
)

pytestmark = pytest.mark.spark


def _run(
    spark: Any, tmp_path: Path, rows: list[FixtureRow] | None = None
) -> tuple[TransformResult, list[FixtureRow]]:
    source = tmp_path / "yellow_tripdata_2025-01.parquet"
    rows = write_fixture_parquet(source, rows)
    mapping = map_source_schema("yellow", inspect_parquet(source).columns)
    ctx = TransformContext(
        run_id=str(uuid.uuid4()),
        taxi_type="yellow",
        source_file=source.name,
        period_start=PERIOD_START,
        period_end_exclusive=date(2025, 2, 1),
        ingested_at=datetime(2026, 10, 9, 12, 0, tzinfo=UTC),
        mapping=mapping,
        rules=QualityRules(),
        mapped_zone_ids=ZONE_IDS,
    )
    return transform_file(spark, source, ctx, tmp_path / "out"), rows


def _read_dir(path: Path) -> list[dict[str, Any]]:
    files = sorted(path.glob("*.parquet"))
    return [row for f in files for row in pq.read_table(f).to_pylist()]


def test_counts_reasons_and_flags_match_fixture_expectations(spark: Any, tmp_path: Path) -> None:
    result, rows = _run(spark, tmp_path)
    accepted = [r for r in rows if r.accepted]

    assert result.input_rows == len(rows)
    assert result.accepted_rows == len(accepted)
    assert result.quarantined_rows == len(rows) - len(accepted)
    assert result.duplicate_rows == 1
    for reason in QUARANTINE_REASONS:
        assert result.quarantine_reason_counts[reason] == sum(reason in r.reasons for r in rows), reason
    for flag in FLAGS:
        assert result.flag_counts[flag] == sum(flag in r.flags for r in accepted), flag
    assert result.invalid_timestamp_rows == 2  # missing pickup + drop-off before pickup
    assert result.unmapped_location_rows == 1
    assert result.min_pickup_date == date(2025, 1, 6)
    assert result.max_pickup_date == date(2025, 1, 31)
    assert result.accepted_total_amount_sum == sum(Decimal(str(r.total)) for r in accepted)
    assert all(v == 0 for v in result.cast_failure_counts.values())


def test_curated_rows_have_expected_flags_and_derived_fields(spark: Any, tmp_path: Path) -> None:
    result, rows = _run(spark, tmp_path)
    curated = _read_dir(result.curated_dir)
    assert len(curated) == result.accepted_rows
    assert list(curated[0].keys()) == list(CURATED_COLUMNS)

    by_pickup = {(r["pickup_datetime"], r["total_amount"]): r for r in curated}
    for fixture in (r for r in rows if r.accepted):
        row = by_pickup[(fixture.pickup, Decimal(str(fixture.total)).quantize(Decimal("0.01")))]
        assert set(row["quality_flags"]) == fixture.flags, fixture.label
        assert row["is_distance_valid"] == ("invalid_distance" not in fixture.flags), fixture.label
        assert row["is_duration_valid"] == ("invalid_duration" not in fixture.flags), fixture.label
        assert row["is_amount_valid"] == ("invalid_amount" not in fixture.flags), fixture.label
        assert row["trip_duration_minutes"] == pytest.approx(fixture.duration_minutes), fixture.label
        assert uuid.UUID(row["run_id"])  # lineage: every curated row names the run that produced it
        assert row["source_file"] == "yellow_tripdata_2025-01.parquet"
        assert row["data_period"] == PERIOD_START


def test_calendar_fields_use_local_wall_clock(spark: Any, tmp_path: Path) -> None:
    result, _ = _run(spark, tmp_path)
    curated = {r["pickup_datetime"]: r for r in _read_dir(result.curated_dir)}

    monday = curated[datetime(2025, 1, 6, 8, 0)]
    assert (monday["pickup_date"], monday["pickup_hour"], monday["pickup_day_of_week"]) == (
        date(2025, 1, 6),
        8,
        1,
    )
    late_tuesday = curated[datetime(2025, 1, 7, 23, 30)]
    assert (late_tuesday["pickup_hour"], late_tuesday["pickup_day_of_week"]) == (23, 2)
    sunday = curated[datetime(2025, 1, 12, 12, 0)]
    assert sunday["pickup_day_of_week"] == 7
    last_second = curated[datetime(2025, 1, 31, 23, 59, 59)]
    assert last_second["pickup_date"] == date(2025, 1, 31)


def test_speed_and_fare_per_mile_only_for_valid_inputs(spark: Any, tmp_path: Path) -> None:
    result, _ = _run(spark, tmp_path)
    curated = {r["pickup_datetime"]: r for r in _read_dir(result.curated_dir)}

    normal = curated[datetime(2025, 1, 6, 8, 0)]
    assert normal["average_speed_mph"] == pytest.approx(15.0)
    assert normal["fare_per_mile"] == pytest.approx(22.0 / 5.0)
    implausible = curated[datetime(2025, 1, 9, 15, 0)]
    assert implausible["average_speed_mph"] is None  # 600 mph is flagged, not reported
    zero_distance = curated[datetime(2025, 1, 7, 10, 0)]
    assert zero_distance["average_speed_mph"] is None
    assert zero_distance["fare_per_mile"] is None
    negative = curated[datetime(2025, 1, 7, 11, 0)]
    assert negative["fare_per_mile"] is None


def test_quarantine_preserves_rejected_rows_with_reasons(spark: Any, tmp_path: Path) -> None:
    result, rows = _run(spark, tmp_path)
    quarantined = _read_dir(result.quarantine_dir)
    expected = sorted(sorted(r.reasons) for r in rows if not r.accepted)
    assert sorted(sorted(q["quarantine_reasons"]) for q in quarantined) == expected


def test_flex_fare_rows_keep_nulls_instead_of_fabricated_values(spark: Any, tmp_path: Path) -> None:
    result, _ = _run(spark, tmp_path)
    flex = next(r for r in _read_dir(result.curated_dir) if r["payment_type"] == 0)
    assert flex["passenger_count"] is None
    assert flex["rate_code_id"] is None
    assert flex["airport_fee"] is None
    assert result.missingness_by_column["passenger_count"]["null_count"] == 1


def test_missing_optional_column_is_unavailable_not_fabricated(spark: Any, tmp_path: Path) -> None:
    source = tmp_path / "no_cbd.parquet"
    rows = write_fixture_parquet(source)
    table = pq.read_table(source).drop_columns(["cbd_congestion_fee"])
    table = table.rename_columns(["airport_fee" if c == "Airport_fee" else c for c in table.column_names])
    pq.write_table(table, source)
    mapping = map_source_schema("yellow", inspect_parquet(source).columns)
    assert mapping.unavailable == ("cbd_congestion_fee",)
    assert mapping.mapping["airport_fee"] == "airport_fee"
    ctx = TransformContext(
        run_id="r",
        taxi_type="yellow",
        source_file=source.name,
        period_start=PERIOD_START,
        period_end_exclusive=date(2025, 2, 1),
        ingested_at=datetime(2026, 10, 9, tzinfo=UTC),
        mapping=mapping,
        rules=QualityRules(),
        mapped_zone_ids=ZONE_IDS,
    )
    result = transform_file(spark, source, ctx, tmp_path / "out2")
    curated = _read_dir(result.curated_dir)
    assert all(r["cbd_congestion_fee"] is None for r in curated)
    assert "cbd_congestion_fee" not in result.missingness_by_column
    assert len(curated) == sum(r.accepted for r in rows)
