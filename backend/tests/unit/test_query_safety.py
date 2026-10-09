from __future__ import annotations

import re
from datetime import date

import pytest
from pydantic import ValidationError

from tripscope.analytics.filters import AnalyticsFilters, TimeSeriesQuery
from tripscope.analytics.metrics import METRICS
from tripscope.analytics.query_builder import overview_query, time_series_query, where_clause
from tripscope.core.identifiers import redact, validate_identifier

PERIODS = [date(2025, 1, 1)]
INJECTION = "1; DROP TABLE taxi_trips; --"


def _where(**filters: object):  # type: ignore[no-untyped-def]
    return where_clause(
        AnalyticsFilters.model_validate(filters), taxi_type="yellow", published_periods=PERIODS
    )


def test_filter_values_are_bound_parameters_not_sql_text() -> None:
    where = _where(
        start_date="2025-01-06",
        end_date="2025-01-12",
        pickup_zone=[132, 161],
        payment_type=[1],
        hour=[8],
        min_distance=1.5,
    )
    assert "2025-01-06" not in where.sql and "132" not in where.sql and "1.5" not in where.sql
    assert "{start_date:Date}" in where.sql and "{pickup_zone:Array(Int32)}" in where.sql
    assert where.parameters["pickup_zone"] == [132, 161]
    assert where.parameters["published_months"] == [202501]


def test_only_published_months_and_taxi_type_are_always_constrained() -> None:
    where = _where()
    assert where.sql == (
        "taxi_type = {taxi_type:String} AND toYYYYMM(pickup_date) IN {published_months:Array(UInt32)}"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"pickup_zone": [INJECTION]},
        {"pickup_zone": [0]},
        {"pickup_zone": [266]},
        {"payment_type": [7]},
        {"hour": [24]},
        {"start_date": INJECTION},
        {"dataset_id": "yellow'; DROP"},
        {"min_distance": -1},
        {"start_date": "2025-02-01", "end_date": "2025-01-01"},
        {"min_distance": 5, "max_distance": 1},
        {"pickup_zone": list(range(1, 60))},
        {"order_by": "fare_amount"},
    ],
)
def test_invalid_or_unknown_filters_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AnalyticsFilters.model_validate(bad)


def test_time_series_metric_and_granularity_are_allowlisted() -> None:
    with pytest.raises(ValidationError):
        TimeSeriesQuery.model_validate({"metric": "sum(fare_amount)"})
    with pytest.raises(ValidationError):
        TimeSeriesQuery.model_validate({"granularity": "second"})
    with pytest.raises(KeyError):
        time_series_query("tripscope", _where(), metric="fare_amount); DROP", granularity="day")


def test_generated_sql_uses_only_known_identifiers() -> None:
    allowed = {
        "SELECT",
        "AS",
        "FROM",
        "WHERE",
        "AND",
        "IN",
        "NOT",
        "GROUP",
        "BY",
        "ORDER",
        "LIMIT",
        "tripscope",
        "taxi_trips",
        "taxi_type",
        "String",
        "toYYYYMM",
        "pickup_date",
        "published_months",
        "Array",
        "UInt32",
        "count",
        "sumIf",
        "avgIf",
        "countIf",
        "total_amount",
        "is_amount_valid",
        "trip_distance",
        "is_distance_valid",
        "trip_duration_minutes",
        "is_duration_valid",
        "min",
        "max",
        "uniqExact",
        "first_date",
        "last_date",
        "days",
        "bucket",
        "value",
        "trips",
        "toStartOfHour",
        "pickup_datetime",
        "excluded__total_recorded_amount",
        "excluded__avg_total_amount",
        "excluded__avg_trip_distance",
        "excluded__avg_trip_duration_minutes",
        *METRICS,
    }
    sqls = [overview_query("tripscope", _where()).sql]
    sqls += [time_series_query("tripscope", _where(), metric=m, granularity="hour").sql for m in METRICS]
    for sql in sqls:
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql))
        assert words <= allowed, words - allowed


def test_database_identifier_validation() -> None:
    assert validate_identifier("tripscope_test") == "tripscope_test"
    for bad in ["tripscope; DROP", "Trip", "a-b", "", "x" * 70]:
        with pytest.raises(ValueError):
            validate_identifier(bad)
    with pytest.raises(ValueError):
        overview_query("tripscope.taxi_trips --", _where())


def test_redact_removes_secrets_from_messages() -> None:
    assert redact("auth failed for key s3cr3tvalue123", ["s3cr3tvalue123"]) == "auth failed for key ***"


def test_applied_filters_echo_only_what_was_set() -> None:
    filters = AnalyticsFilters.model_validate({"start_date": "2025-01-06", "pickup_zone": [132]})
    assert filters.applied() == {
        "start_date": "2025-01-06",
        "pickup_zone": [132],
        "dataset_id": "nyc-tlc-yellow",
    }
