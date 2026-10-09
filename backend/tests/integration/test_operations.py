"""Phase 2 operations against the real services: aggregates vs raw equality, job queue + worker through the
API (roles, conflicts, cancel, retry, logs), CSV sources (accepted and truncated), stale-job recovery,
quality and schema reports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.conftest import PASSWORD, TEST_DB
from tripscope.analytics.filters import AnalyticsFilters, BreakdownQuery, TimeSeriesQuery
from tripscope.api.app import create_app
from tripscope.jobs import service
from tripscope.metadata.models import DataSource, IngestionJob, JobStatus, Role
from tripscope.pipeline.worker import Worker

pytestmark = [pytest.mark.integration, pytest.mark.spark]


@pytest.fixture(scope="module")
def api(env: dict[str, Any]) -> TestClient:
    return TestClient(create_app(env["settings"]))


def _login(client: TestClient, role: Role) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": f"{role.value}@test.local", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def _worker(env: dict[str, Any]) -> Worker:
    return Worker(env["deps"], worker_id="test-worker", poll_seconds=0.1, heartbeat_seconds=0.2)


@pytest.fixture(scope="module")
def published(env: dict[str, Any], api: TestClient) -> dict[str, Any]:
    """Queue the Parquet fixture through the API as an admin and let the worker run it."""
    _login(api, Role.ADMIN)
    created = api.post("/api/v1/ingestion-jobs", json={"source_key": "fixture-2025-01"})
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "queued"
    assert _worker(env).run_once() == uuid.UUID(created.json()["job_id"])
    job = api.get(f"/api/v1/ingestion-jobs/{created.json()['job_id']}").json()
    assert job["status"] == "completed", job
    return job


def test_worker_runs_api_queued_job_and_records_stages_and_logs(
    published: dict[str, Any], api: TestClient
) -> None:
    run = published["runs"][-1]
    assert set(run["stage_timings"]) == {
        "acquire",
        "inspect",
        "store_raw",
        "zones",
        "spark_transform",
        "store_curated",
        "load_clickhouse",
        "publish",
    }
    assert published["worker_id"] == "test-worker" and published["trigger"] == "api"
    logs = api.get(f"/api/v1/processing-runs/{run['run_id']}/logs").json()["logs"]
    messages = [entry["message"] for entry in logs]
    assert "run started" in messages and "period published" in messages
    assert all(entry["level"] in {"info", "warning", "error"} for entry in logs)


def test_only_admins_start_jobs_and_viewers_cannot_see_them(
    api: TestClient, published: dict[str, Any]
) -> None:
    _login(api, Role.ANALYST)
    assert api.post("/api/v1/ingestion-jobs", json={"source_key": "fixture-2025-01"}).status_code == 403
    assert api.get("/api/v1/ingestion-jobs").status_code == 200
    assert api.get("/api/v1/data-sources").status_code == 200
    _login(api, Role.VIEWER)
    assert api.get("/api/v1/ingestion-jobs").status_code == 403
    assert api.get("/api/v1/data-sources").status_code == 403
    _login(api, Role.ADMIN)
    assert api.post("/api/v1/ingestion-jobs", json={"source_key": "not-in-manifest"}).status_code == 404
    assert api.post("/api/v1/ingestion-jobs", json={"source_key": "Bad Key"}).status_code == 422


def test_duplicate_queue_is_rejected_and_queued_job_cancels_immediately(
    api: TestClient, env: dict[str, Any]
) -> None:
    _login(api, Role.ADMIN)
    first = api.post("/api/v1/ingestion-jobs", json={"source_key": "fixture-2025-01"}).json()
    duplicate = api.post("/api/v1/ingestion-jobs", json={"source_key": "fixture-2025-01"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["details"]["job_id"] == first["job_id"]
    cancelled = api.post(f"/api/v1/ingestion-jobs/{first['job_id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and "before it started" in cancelled["error_summary"]
    assert api.post(f"/api/v1/ingestion-jobs/{first['job_id']}/cancel").status_code == 409
    assert _worker(env).run_once() is None  # nothing left to claim

    retried = api.post(f"/api/v1/ingestion-jobs/{first['job_id']}/retry")
    assert retried.status_code == 201
    body = retried.json()
    assert body["retry_of_job_id"] == first["job_id"] and body["attempt"] == first["attempt"] + 1
    api.post(f"/api/v1/ingestion-jobs/{body['job_id']}/cancel")
    with env["factory"]() as session:
        audited = session.execute(
            select(IngestionJob.status).where(IngestionJob.id == uuid.UUID(body["job_id"]))
        ).scalar_one()
        assert audited == JobStatus.CANCELLED


def test_cancel_request_during_run_publishes_nothing(env: dict[str, Any], published: dict[str, Any]) -> None:
    writer = env["writer"]
    before = writer.query(f"SELECT any(run_id), count() FROM {TEST_DB}.taxi_trips").first_row
    with env["factory"]() as session, session.begin():
        job = service.enqueue(session, env["deps"].manifest, "fixture-2025-01", requested_by=None)
        # A cancel request recorded after the worker claims the job but before the first stage starts.
        job.cancel_requested_at = datetime.now(UTC)
        job_id = job.id
    assert _worker(env).run_once() == job_id
    with env["factory"]() as session:
        job = session.get(IngestionJob, job_id)
        assert job is not None and job.status == JobStatus.CANCELLED
        assert "nothing was published" in (job.error_summary or "")
    assert writer.query(f"SELECT any(run_id), count() FROM {TEST_DB}.taxi_trips").first_row == before


def test_stale_running_job_is_failed(env: dict[str, Any], published: dict[str, Any]) -> None:
    with env["factory"]() as session, session.begin():
        job = service.enqueue(session, env["deps"].manifest, "fixture-csv-truncated", requested_by=None)
        job.status, job.worker_id = JobStatus.RUNNING, "dead-worker"
        job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        job_id = job.id
    with env["factory"]() as session, session.begin():
        assert service.fail_stale(session, stale_after=timedelta(minutes=2)) == 1
    with env["factory"]() as session:
        job = session.get(IngestionJob, job_id)
        assert (
            job is not None and job.status == JobStatus.FAILED and "dead-worker" in (job.error_summary or "")
        )


def test_csv_source_is_ingested_with_the_same_outcomes(env: dict[str, Any], api: TestClient) -> None:
    _login(api, Role.ADMIN)
    created = api.post("/api/v1/ingestion-jobs", json={"source_key": "fixture-csv-2023-01"}).json()
    _worker(env).run_once()
    job = api.get(f"/api/v1/ingestion-jobs/{created['job_id']}").json()
    assert job["status"] == "completed", job["error_summary"]
    rows = env["csv_rows"]
    run = job["runs"][-1]
    assert run["input_rows"] == len(rows)
    assert run["accepted_rows"] == sum(r.accepted for r in rows)
    assert run["unavailable_fields"] == ["cbd_congestion_fee"]
    timestamps = (
        env["writer"]
        .query(
            f"SELECT min(pickup_datetime), max(pickup_datetime) FROM {TEST_DB}.taxi_trips "
            "WHERE toYYYYMM(pickup_date) = 202301"
        )
        .first_row
    )
    accepted = [r.pickup for r in rows if r.accepted and r.pickup]
    assert timestamps[0].replace(tzinfo=None) == min(accepted)  # 12-hour clock parsed to local wall-clock
    assert timestamps[1].replace(tzinfo=None) == max(accepted)
    with env["factory"]() as session:
        source = session.scalar(select(DataSource).where(DataSource.source_key == "fixture-csv-2023-01"))
        assert source is not None and source.file_format == "csv"


def test_truncated_csv_export_is_rejected(env: dict[str, Any], api: TestClient) -> None:
    _login(api, Role.ADMIN)
    created = api.post("/api/v1/ingestion-jobs", json={"source_key": "fixture-csv-truncated"}).json()
    _worker(env).run_once()
    job = api.get(f"/api/v1/ingestion-jobs/{created['job_id']}").json()
    assert job["status"] == "failed"
    assert "server response instead of data" in job["error_summary"] and "truncated" in job["error_summary"]
    count = (
        env["writer"]
        .query(f"SELECT count() FROM {TEST_DB}.taxi_trips WHERE toYYYYMM(pickup_date) = 202302")
        .first_row[0]
    )
    assert count == 0


FILTER_CASES: list[dict[str, Any]] = [
    {},
    {"start_date": "2025-01-06", "end_date": "2025-01-12"},
    {"pickup_zone": [132, 161]},
    {"payment_type": [1], "hour": [8, 9, 23]},
    {"vendor_id": [1]},
    {"weekday": [1, 2], "pickup_zone": [161]},
]


@pytest.mark.parametrize("filters", FILTER_CASES)
def test_aggregates_answer_exactly_like_the_fact_table(
    env: dict[str, Any], published: dict[str, Any], api: TestClient, filters: dict[str, Any]
) -> None:
    service_ = api.app.state.analytics  # type: ignore[attr-defined]
    query = AnalyticsFilters.model_validate(filters)
    agg, raw = service_.overview(query), service_.overview(query, prefer_raw=True)
    assert agg["meta"]["source_table"] == "trips_hourly_agg" and raw["meta"]["source_table"] == "taxi_trips"
    assert agg["data_state"] == raw["data_state"]
    if agg["kpis"]:
        for metric, kpi in agg["kpis"].items():
            assert kpi["excluded_rows"] == raw["kpis"][metric]["excluded_rows"], metric
            assert kpi["value"] == pytest.approx(raw["kpis"][metric]["value"], rel=1e-9), metric
    for granularity in ("day", "hour"):
        series = TimeSeriesQuery.model_validate(
            {**filters, "metric": "total_recorded_amount", "granularity": granularity}
        )
        a, r = service_.time_series(series), service_.time_series(series, prefer_raw=True)
        assert [(str(p["bucket"]), p["trips"]) for p in a["points"]] == [
            (str(p["bucket"]), p["trips"]) for p in r["points"]
        ]
        assert [p["value"] for p in a["points"]] == pytest.approx([p["value"] for p in r["points"]])
    for dimension in ("hour", "weekday", "pickup_zone"):
        breakdown = BreakdownQuery.model_validate({**filters, "metric": "avg_trip_distance"})
        a = service_.breakdown(breakdown, metric="avg_trip_distance", dimension=dimension)
        r = service_.breakdown(breakdown, metric="avg_trip_distance", dimension=dimension, prefer_raw=True)
        assert [(g["key"], g["trips"]) for g in a["groups"]] == [(g["key"], g["trips"]) for g in r["groups"]]


def test_filters_outside_the_aggregate_fall_back_to_the_fact_table(
    api: TestClient, published: dict[str, Any]
) -> None:
    _login(api, Role.VIEWER)
    assert (
        api.get("/api/v1/analytics/overview?dropoff_zone=236").json()["meta"]["source_table"] == "taxi_trips"
    )
    assert api.get("/api/v1/analytics/overview?min_distance=2").json()["meta"]["source_table"] == "taxi_trips"
    assert (
        api.get("/api/v1/analytics/overview?pickup_zone=161").json()["meta"]["source_table"]
        == "trips_hourly_agg"
    )


def test_breakdown_endpoints_label_groups(
    api: TestClient, env: dict[str, Any], published: dict[str, Any]
) -> None:
    _login(api, Role.VIEWER)
    accepted = [r for r in env["rows"] if r.accepted]
    weekdays = api.get("/api/v1/analytics/trips-by-weekday").json()["groups"]
    assert sum(g["trips"] for g in weekdays) == len(accepted)
    assert {g["label"] for g in weekdays} <= {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    hours = api.get("/api/v1/analytics/trips-by-hour").json()["groups"]
    assert next(g for g in hours if g["key"] == 8)["label"] == "08:00"
    zones = api.get("/api/v1/analytics/top-pickup-zones?limit=3").json()["groups"]
    assert zones[0]["trips"] >= zones[-1]["trips"] and zones[0]["mapped"] is True
    unmapped = api.get("/api/v1/analytics/top-pickup-zones?pickup_zone=264").json()["groups"]
    assert unmapped == [
        {"key": 264, "value": 1.0, "trips": 1, "label": "Unmapped (264)", "borough": None, "mapped": False}
    ]
    assert len(api.get("/api/v1/analytics/zones").json()["zones"]) == 265


def test_quality_and_schema_reports(api: TestClient, env: dict[str, Any], published: dict[str, Any]) -> None:
    _login(api, Role.VIEWER)
    quality = api.get("/api/v1/datasets/nyc-tlc-yellow/quality").json()
    rows = env["rows"]
    assert quality["totals"]["input_rows"] == len(rows)
    assert quality["totals"]["quarantined_rows"] == sum(not r.accepted for r in rows)
    assert quality["totals"]["quarantine_reasons"]["duplicate_record"] == 1
    flagged = {f: n for f, n in quality["totals"]["flags"].items() if n}
    daily = {}
    for entry in quality["daily_flags"]:
        daily[entry["flag"]] = daily.get(entry["flag"], 0) + entry["flagged_trips"]
    assert daily == flagged  # daily aggregate and run metrics agree
    assert api.get("/api/v1/datasets/nyc-tlc-yellow/quality?bogus=1").status_code == 422

    schema = api.get("/api/v1/datasets/nyc-open-data-yellow/schema").json()
    assert schema["sources"][0]["file_format"] == "csv"
    cbd = next(f for f in schema["fields"] if f["name"] == "cbd_congestion_fee")
    assert cbd["available_in"] == 0
