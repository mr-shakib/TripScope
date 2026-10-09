from __future__ import annotations

import csv
import io
import typing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.fixtures.zones_fixture import write_zone_zip
from tripscope.analytics import filters as f
from tripscope.analytics.export import cell, csv_stream, safe_text
from tripscope.analytics.labels import PAYMENT_TYPES, code_label
from tripscope.analytics.query_builder import (
    DROPOFF_AGG,
    EXPLORER_COLUMNS,
    HOURLY_AGG,
    RAW,
    SORTABLE,
    choose_source,
    grouped_query,
    rows_query,
    where_clause,
)
from tripscope.analytics.service import _quantile_bucket
from tripscope.core.errors import SourceValidationError
from tripscope.pipeline.rules import FLAGS
from tripscope.pipeline.zone_geometry import build_zone_geojson


@pytest.mark.parametrize(
    ("filters", "dimensions", "metrics", "expected"),
    [
        ({}, ["payment_type"], ["total_trips"], HOURLY_AGG),
        ({}, ["dropoff_zone"], ["total_trips"], DROPOFF_AGG),
        ({"payment_type": [1]}, ["dropoff_zone"], ["avg_trip_distance"], DROPOFF_AGG),
        ({"hour": [8]}, ["dropoff_zone"], ["total_trips"], RAW),  # drop-off aggregate has no hour
        ({}, ["dropoff_zone"], ["avg_trip_duration_minutes"], RAW),  # ... and no duration
        ({"dropoff_zone": [161]}, ["time:day"], ["total_recorded_amount"], DROPOFF_AGG),
        ({"dropoff_zone": [161]}, ["time:hour"], ["total_trips"], RAW),
        ({}, ["weekday", "hour"], ["total_trips"], HOURLY_AGG),
        ({}, ["pickup_zone", "dropoff_zone"], ["total_trips"], RAW),
        ({"min_distance": 2}, ["payment_type"], ["total_trips"], RAW),
    ],
)
def test_requests_go_to_the_cheapest_table_that_can_answer(filters, dimensions, metrics, expected) -> None:  # type: ignore[no-untyped-def]
    query = f.AnalyticsFilters.model_validate(filters)
    assert choose_source(query, dimensions=dimensions, metrics=metrics) is expected


def test_request_model_literals_match_the_allowlists() -> None:
    assert typing.get_args(f.SortColumn) == SORTABLE
    assert typing.get_args(f.ExplorerColumn) == EXPLORER_COLUMNS
    assert set(typing.get_args(f.FlagName)) == set(FLAGS)


def test_explorer_preview_is_bounded() -> None:
    assert f.ExplorerQuery(page=200, page_size=50).page == 200
    with pytest.raises(ValidationError, match="limited to the first 10,000 rows"):
        f.ExplorerQuery(page=201, page_size=50)
    with pytest.raises(ValidationError, match="page_size"):
        f.ExplorerQuery(page_size=1000)
    with pytest.raises(ValidationError):
        f.ExplorerQuery(sort="pickup_location_id; DROP TABLE x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        f.ExplorerQuery(flag="not_a_flag")  # type: ignore[arg-type]


def test_row_queries_bind_paging_and_allow_only_known_columns() -> None:
    where = where_clause(
        f.AnalyticsFilters(),
        taxi_type="yellow",
        published_periods=[],
        quality="flagged",
        flag="invalid_amount",
    )
    assert "notEmpty(quality_flags)" in where.sql and "has(quality_flags, {flag:String})" in where.sql
    query = rows_query("tripscope", where, sort="total_amount", order="desc", limit=50, offset=100)
    assert "ORDER BY total_amount DESC NULLS LAST" in query.sql
    assert query.parameters["row_limit"] == 50 and query.parameters["row_offset"] == 100
    with pytest.raises(KeyError):
        rows_query("tripscope", where, sort="pickup_location_id", order="asc", limit=1, offset=0)
    with pytest.raises(KeyError):
        rows_query(
            "tripscope", where, sort="total_amount", order="asc", limit=1, offset=0, columns=["password"]
        )
    with pytest.raises(KeyError):
        grouped_query(
            "tripscope", where, metric="total_trips", dimensions=["dropoff_zone"], source=HOURLY_AGG
        )


@pytest.mark.parametrize("payload", ["=SUM(A1:A9)", "+1+1", "-2+3", "@cmd", "\tx", "\rx"])
def test_text_that_spreadsheets_would_evaluate_is_neutralised(payload: str) -> None:
    assert safe_text(payload) == "'" + payload
    assert cell([payload, "ok"]).startswith("'")


def test_values_keep_their_types_in_csv() -> None:
    assert cell(Decimal("-12.50")) == "-12.50"  # negative amounts stay numeric
    assert cell(None) == ""
    assert cell(True) == "true"
    assert cell(datetime(2025, 1, 6, 8, 0, 5)) == "2025-01-06 08:00:05"
    assert cell(date(2025, 1, 6)) == "2025-01-06"
    assert cell(["invalid_amount", "pickup_zone_unmapped"]) == "invalid_amount;pickup_zone_unmapped"


def test_csv_stream_quotes_and_chunks() -> None:
    rows = [("a,b", 1), ('quote "x"', -2), ("=evil", None)] * 3
    text = b"".join(csv_stream(["name", "value"], iter(rows), chunk_rows=2)).decode()
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == ["name", "value"] and len(parsed) == 10
    assert parsed[1] == ["a,b", "1"] and parsed[2] == ['quote "x"', "-2"] and parsed[3] == ["'=evil", ""]


def test_quantile_bucket_is_an_interval() -> None:
    buckets = [
        {"start": 0.0, "end": 1.0, "trips": 10},
        {"start": 1.0, "end": 2.0, "trips": 30},
        {"start": 50.0, "end": None, "trips": 60},
    ]
    assert _quantile_bucket(buckets, 0.3) == {"start": 1.0, "end": 2.0}
    assert _quantile_bucket(buckets, 0.9) == {"start": 50.0, "end": None}
    assert _quantile_bucket([], 0.5) is None


def test_code_labels_never_invent_meaning() -> None:
    assert code_label(PAYMENT_TYPES, 1, "Payment type") == "Credit card"
    assert code_label(PAYMENT_TYPES, None, "Payment type") == "Not recorded"
    assert code_label(PAYMENT_TYPES, 9, "Payment type") == "Payment type 9 (undocumented)"


def test_zone_geometry_is_reprojected_merged_and_bounded(tmp_path: Path) -> None:
    collection = build_zone_geojson(write_zone_zip(tmp_path / "zones.zip"))
    features = {feature["id"]: feature for feature in collection["features"]}
    assert sorted(features) == [132, 161, 236]
    assert features[161]["geometry"]["type"] == "MultiPolygon"  # two records merged into one zone
    assert features[132]["properties"] == {"location_id": 132, "zone": "JFK Airport", "borough": "Queens"}
    lon, lat = features[132]["geometry"]["coordinates"][0][0]
    assert -73.84 < lon < -73.74 and 40.61 < lat < 40.67  # where JFK actually is


def test_zone_geometry_rejects_wrong_projection_and_out_of_city_shapes(tmp_path: Path) -> None:
    with pytest.raises(SourceValidationError, match="State Plane"):
        build_zone_geojson(write_zone_zip(tmp_path / "wgs.zip", prj='GEOGCS["WGS 84"]'))
    with pytest.raises(SourceValidationError, match="outside New York City"):
        build_zone_geojson(write_zone_zip(tmp_path / "far.zip", far_away=True))
