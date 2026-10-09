"""Phase 3 against the real services: new chart queries (aggregate vs fact-table equality), period comparison,
zone geometry, data explorer and CSV extracts."""

from __future__ import annotations

import csv
import io
from collections import Counter
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.integration.conftest import PASSWORD
from tripscope.analytics.filters import AnalyticsFilters
from tripscope.api.app import create_app
from tripscope.metadata.models import Role
from tripscope.pipeline.runner import run_source

pytestmark = [pytest.mark.integration, pytest.mark.spark]


@pytest.fixture(scope="module")
def api(env: dict[str, Any]) -> TestClient:
    return TestClient(create_app(env["settings"]))


def _login(client: TestClient, role: Role = Role.ANALYST) -> None:
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": f"{role.value}@test.local", "password": PASSWORD}
        ).status_code
        == 200
    )


def test_zone_geometry_is_unavailable_until_built(api: TestClient) -> None:
    _login(api)
    response = api.get("/api/v1/analytics/zones/geometry")
    assert response.status_code == 404 and "build-zone-geometry" in response.json()["error"]["message"]


@pytest.fixture(scope="module")
def published(env: dict[str, Any], api: TestClient) -> Any:
    summary = run_source(env["deps"], "fixture-2025-01")
    api.app.state.geometry_cache.clear()  # type: ignore[attr-defined]
    return summary


def accepted(env: dict[str, Any]) -> list[Any]:
    return [r for r in env["rows"] if r.accepted]


def test_run_builds_zone_geometry(api: TestClient, published: Any) -> None:
    _login(api)
    collection = api.get("/api/v1/analytics/zones/geometry").json()
    assert collection["type"] == "FeatureCollection"
    assert sorted(f["id"] for f in collection["features"]) == [132, 161, 236]


def test_previous_period_comparison(api: TestClient, env: dict[str, Any], published: Any) -> None:
    _login(api)
    body = api.get(
        "/api/v1/analytics/overview?start_date=2025-01-13&end_date=2025-01-19&compare=previous"
    ).json()
    rows = accepted(env)
    in_range = lambda lo, hi: sum(1 for r in rows if r.pickup and lo <= r.pickup.day <= hi)  # noqa: E731
    assert body["kpis"]["total_trips"]["value"] == in_range(13, 19)
    comparison = body["comparison"]
    assert (comparison["start_date"], comparison["end_date"]) == ("2025-01-06", "2025-01-12")
    assert comparison["kpis"]["total_trips"]["value"] == in_range(6, 12)
    early = api.get(
        "/api/v1/analytics/overview?start_date=2025-01-06&end_date=2025-01-12&compare=previous"
    ).json()
    assert (
        early["comparison"]["available"] is False
        and "before published coverage" in early["comparison"]["reason"]
    )
    no_dates = api.get("/api/v1/analytics/overview?compare=previous").json()
    assert no_dates["comparison"]["available"] is False


@pytest.mark.parametrize(
    "filters", [{}, {"payment_type": [1, 2]}, {"start_date": "2025-01-07", "end_date": "2025-01-12"}]
)
@pytest.mark.parametrize("dimension", ["payment_type", "vendor_id", "dropoff_zone"])
@pytest.mark.parametrize("metric", ["total_trips", "total_recorded_amount", "avg_trip_distance"])
def test_new_breakdowns_equal_the_fact_table(
    api: TestClient, published: Any, filters: dict[str, Any], dimension: str, metric: str
) -> None:
    service = api.app.state.analytics  # type: ignore[attr-defined]
    query = AnalyticsFilters.model_validate(filters)
    fast = service.breakdown(query, metric=metric, dimension=dimension)
    raw = service.breakdown(query, metric=metric, dimension=dimension, prefer_raw=True)
    expected_table = "trips_dropoff_daily_agg" if dimension == "dropoff_zone" else "trips_hourly_agg"
    assert fast["meta"]["source_table"] == expected_table and raw["meta"]["source_table"] == "taxi_trips"
    assert [(g["key"], g["trips"]) for g in fast["groups"]] == [(g["key"], g["trips"]) for g in raw["groups"]]
    assert [g["value"] for g in fast["groups"]] == pytest.approx([g["value"] for g in raw["groups"]])


def test_hour_weekday_matrix_equals_the_fact_table(
    api: TestClient, env: dict[str, Any], published: Any
) -> None:
    service = api.app.state.analytics  # type: ignore[attr-defined]
    fast = service.hour_weekday_matrix(AnalyticsFilters(), metric="total_trips")
    raw = service.hour_weekday_matrix(AnalyticsFilters(), metric="total_trips", prefer_raw=True)
    assert fast["cells"] == raw["cells"]
    expected = Counter((r.pickup.isoweekday(), r.pickup.hour) for r in accepted(env))
    assert {(c["weekday"], c["hour"]): c["trips"] for c in fast["cells"]} == dict(expected)


def test_distribution_buckets_equal_the_fact_table(
    api: TestClient, env: dict[str, Any], published: Any
) -> None:
    _login(api)
    for metric, valid_flag in (("trip_distance", "invalid_distance"), ("total_amount", "invalid_amount")):
        fast = api.get(f"/api/v1/analytics/distribution?metric={metric}").json()
        raw = api.get(
            f"/api/v1/analytics/distribution?metric={metric}&vendor_id=1&vendor_id=2&vendor_id=6&vendor_id=7"
        ).json()
        assert (
            fast["meta"]["source_table"] == "fare_distance_buckets"
            and raw["meta"]["source_table"] == "taxi_trips"
        )
        assert fast["buckets"] == raw["buckets"]
        valid = [r for r in accepted(env) if valid_flag not in r.flags]
        assert fast["summary"]["counted_trips"] == len(valid)
        assert fast["summary"]["excluded_trips"] == len(accepted(env)) - len(valid)


def test_top_flows_and_labels(api: TestClient, env: dict[str, Any], published: Any) -> None:
    _login(api, Role.VIEWER)
    flows = api.get("/api/v1/analytics/top-flows?limit=3").json()["flows"]
    expected = Counter((r.pu, r.do) for r in accepted(env)).most_common(1)[0]
    assert (flows[0]["pickup_zone"], flows[0]["dropoff_zone"], flows[0]["trips"]) == (
        *expected[0],
        expected[1],
    )
    payments = api.get("/api/v1/analytics/payment-types").json()["groups"]
    assert {g["label"] for g in payments} <= {"Credit card", "Cash", "Flex Fare", "Dispute"}
    assert any(m["id"] == "avg_daily_trips" for m in api.get("/api/v1/analytics/metrics").json()["metrics"])


def test_explorer_rows_sort_filter_and_bounds(api: TestClient, env: dict[str, Any], published: Any) -> None:
    _login(api, Role.VIEWER)
    rows = accepted(env)
    page = api.get("/api/v1/explorer/rows?sort=total_amount&order=desc&page_size=25").json()
    assert page["total"] == len(rows)
    assert Decimal(str(page["rows"][0]["total_amount"])) == max(Decimal(str(r.total)) for r in rows)
    flagged = api.get("/api/v1/explorer/rows?quality=flagged").json()
    assert flagged["total"] == sum(1 for r in rows if r.flags)
    clean = api.get("/api/v1/explorer/rows?quality=clean").json()
    assert clean["total"] == sum(1 for r in rows if not r.flags)
    amount = api.get("/api/v1/explorer/rows?flag=invalid_amount").json()
    assert amount["total"] == sum(1 for r in rows if "invalid_amount" in r.flags)
    assert all("invalid_amount" in r["quality_flags"] for r in amount["rows"])
    assert api.get("/api/v1/explorer/rows?page=500&page_size=25").status_code == 422
    assert api.get("/api/v1/explorer/rows?sort=password").status_code == 422
    fields = api.get("/api/v1/explorer/fields").json()
    cbd = next(f for f in fields["fields"] if f["name"] == "cbd_congestion_fee")
    assert cbd["available_in"] == 1 and cbd["unit"] == "USD"


def test_csv_extract_is_bounded_typed_and_audited(
    api: TestClient, env: dict[str, Any], published: Any
) -> None:
    _login(api, Role.VIEWER)
    request = {
        "filters": {"payment_type": [1]},
        "scope": {"sort": "total_amount", "order": "desc"},
        "columns": ["pickup_datetime", "total_amount", "quality_flags"],
        "max_rows": 3,
    }
    response = api.post("/api/v1/exports", json=request)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    matching = sum(1 for r in accepted(env) if r.payment == 1)
    assert response.headers["x-total-rows"] == str(matching)
    assert response.headers["x-exported-rows"] == "3" and response.headers["x-truncated"] == "true"
    parsed = list(csv.reader(io.StringIO(response.text)))
    assert parsed[0] == ["pickup_datetime", "total_amount", "quality_flags"] and len(parsed) == 4
    assert Decimal(parsed[1][1]) >= Decimal(parsed[2][1]) >= Decimal(parsed[3][1])
    assert api.post("/api/v1/exports", json={**request, "columns": ["password"]}).status_code == 422
    assert api.post("/api/v1/exports", json={**request, "format": "xlsx"}).status_code == 422
    with env["factory"]() as session:
        audit = session.execute(
            text(
                "SELECT details FROM audit_events WHERE action = 'export.csv' "
                "ORDER BY occurred_at DESC LIMIT 1"
            )
        ).scalar_one()
        assert audit["exported_rows"] == 3 and audit["matching_rows"] == matching
