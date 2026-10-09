from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.fixtures.tlc_fixture import fixture_rows, shift_year, write_open_data_csv
from tripscope.analytics.filters import AnalyticsFilters
from tripscope.analytics.query_builder import (
    HOURLY_AGG,
    RAW,
    choose_source,
    grouped_query,
    overview_query,
    time_series_query,
    where_clause,
)
from tripscope.analytics.schema_report import SourceSchema, build_schema_report
from tripscope.core.errors import SourceValidationError
from tripscope.pipeline.acquire import inspect_csv
from tripscope.pipeline.manifest import SourceSpec


def test_open_data_csv_passes_structural_validation(tmp_path: Path) -> None:
    path = tmp_path / "ok.csv"
    rows = shift_year(fixture_rows(), -2)
    write_open_data_csv(path, rows)
    inspection = inspect_csv(path)
    assert inspection.num_rows == len(rows)
    assert inspection.columns["airport_fee"] == "string" and "cbd_congestion_fee" not in inspection.columns


def test_truncated_export_is_reported_with_line_and_row_count(tmp_path: Path) -> None:
    path = tmp_path / "truncated.csv"
    write_open_data_csv(path, fixture_rows(), truncated_after=4)
    with pytest.raises(
        SourceValidationError, match=r"line 6: the export ends with a server response.*after 4 rows"
    ):
        inspect_csv(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('"a","b"\n"1","2","3"\n', "line 2 has 3 fields"),
        ('"a",""\n"1","2"\n', "empty column names"),
        ('"a","A"\n"1","2"\n', "duplicate column names"),
        ('"a","b"\n', "no rows"),
    ],
)
def test_malformed_csv_is_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(content)
    with pytest.raises(SourceValidationError, match=message):
        inspect_csv(path)


def test_non_utf8_csv_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "latin1.csv"
    path.write_bytes('"zone"\n"Café"\n'.encode("latin-1"))
    with pytest.raises(SourceValidationError, match="UTF-8"):
        inspect_csv(path)


def test_csv_sources_require_a_profile() -> None:
    base = {"key": "x-2023-01", "dataset": "d", "period": "2023-01", "uri": "https://h/x.csv"}
    with pytest.raises(ValidationError, match="csv_profile"):
        SourceSpec.model_validate({**base, "format": "csv"})
    with pytest.raises(ValidationError, match="only applies"):
        SourceSpec.model_validate({**base, "format": "parquet", "csv_profile": "nyc_open_data"})
    spec = SourceSpec.model_validate({**base, "format": "csv", "csv_profile": "nyc_open_data"})
    assert spec.timestamp_format == "MM/dd/yyyy hh:mm:ss a"


def _schema(period: str, columns: dict[str, str], version: str) -> SourceSchema:
    mapping = {"airport_fee": next(c for c in columns if c.lower() == "airport_fee")}
    return SourceSchema(period, f"s-{period}", "parquet", version, columns, mapping, 10)


def test_schema_report_detects_added_case_renamed_and_retyped_columns() -> None:
    old = _schema("2024-12", {"VendorID": "int64", "airport_fee": "double"}, "v1")
    new = _schema(
        "2025-01", {"VendorID": "int32", "Airport_fee": "double", "cbd_congestion_fee": "double"}, "v2"
    )
    same = _schema(
        "2025-02", {"VendorID": "int32", "Airport_fee": "double", "cbd_congestion_fee": "double"}, "v2"
    )
    report = build_schema_report([same, new, old])  # order-independent
    first, second = report["drift"]
    assert first["added"] == ["cbd_congestion_fee"]
    assert first["renamed_case"] == [{"from": "airport_fee", "to": "Airport_fee"}]
    assert first["type_changed"] == [{"column": "VendorID", "from": "int64", "to": "int32"}]
    assert second["changed"] is False
    assert [v["periods"] for v in report["schema_versions"]] == [["2024-12"], ["2025-01", "2025-02"]]
    airport = next(f for f in report["fields"] if f["name"] == "airport_fee")
    assert airport["source_column_by_period"] == {
        "2024-12": "airport_fee",
        "2025-01": "Airport_fee",
        "2025-02": "Airport_fee",
    }


def test_queries_use_the_aggregate_only_when_every_filter_is_covered() -> None:
    assert choose_source(AnalyticsFilters()) is HOURLY_AGG
    assert (
        choose_source(AnalyticsFilters(pickup_zone=[1], payment_type=[1], hour=[8], vendor_id=[2]))
        is HOURLY_AGG
    )
    assert choose_source(AnalyticsFilters(dropoff_zone=[1])) is RAW
    assert choose_source(AnalyticsFilters(min_distance=1)) is RAW
    assert choose_source(AnalyticsFilters(max_distance=5)) is RAW
    assert choose_source(AnalyticsFilters(), prefer_raw=True) is RAW


def test_aggregate_sql_recomputes_averages_from_sums_and_counts() -> None:
    where = where_clause(AnalyticsFilters(), taxi_type="yellow", published_periods=[])
    sql = overview_query("tripscope", where, HOURLY_AGG).sql
    assert "FROM tripscope.trips_hourly_agg" in sql
    assert "sum(trip_distance_valid_sum) / nullIf(sum(distance_valid_trips), 0) AS avg_trip_distance" in sql
    assert "avgIf" not in sql
    hourly = time_series_query(
        "tripscope", where, metric="total_trips", granularity="hour", source=HOURLY_AGG
    )
    assert "toDateTime(pickup_date) + toIntervalHour(pickup_hour) AS bucket" in hourly.sql


def test_group_limit_is_a_bounded_parameter() -> None:
    where = where_clause(AnalyticsFilters(), taxi_type="yellow", published_periods=[])
    query = grouped_query(
        "tripscope",
        where,
        metric="total_trips",
        dimension="pickup_zone",
        source=RAW,
        order="value",
        limit=10_000,
    )
    assert query.parameters["group_limit"] == 300 and "{group_limit:UInt32}" in query.sql
    with pytest.raises(KeyError):
        grouped_query("tripscope", where, metric="total_trips", dimension="dropoff_zone; DROP", source=RAW)
