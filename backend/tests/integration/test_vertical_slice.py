"""Phase 1 vertical slice against the real services (docker compose), fully isolated from product data:
PostgreSQL `tripscope_test`, ClickHouse `tripscope_test`, bucket `tripscope-test`.

fixture Parquet (real 2025 schema) → acquire/validate → raw lake → Spark → curated lake → ClickHouse
→ published period → authenticated API → values equal to an independent computation from the fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
import pytest
from alembic import command
from alembic.config import Config
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tests.fixtures.tlc_fixture import expected_overview, write_fixture_parquet
from tripscope.api.app import create_app
from tripscope.core.errors import PipelineError
from tripscope.core.passwords import hash_password
from tripscope.core.settings import Settings, get_settings
from tripscope.metadata.db import make_engine, make_session_factory
from tripscope.metadata.models import DataQualityMetrics, DatasetPeriod, IngestionJob, JobStatus, Role, User
from tripscope.pipeline.clickhouse_load import apply_migrations
from tripscope.pipeline.manifest import Manifest
from tripscope.pipeline.runner import PipelineDeps, run_source
from tripscope.storage.clickhouse import reader_client, writer_client
from tripscope.storage.object_store import ObjectStore

pytestmark = [pytest.mark.integration, pytest.mark.spark]

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
TEST_DB = "tripscope_test"
TEST_BUCKET = "tripscope-test"
PASSWORD = "integration-password-123"


def _zone_csv(path: Path) -> None:
    lines = ['"LocationID","Borough","Zone","service_zone"']
    lines += [f'{i},"Borough{i}","Zone {i}","Boro Zone"' for i in range(1, 264)]
    lines += ['264,"Unknown","N/A","N/A"', '265,"N/A","Outside of NYC","N/A"']
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture(scope="module")
def settings() -> Settings:
    try:
        base = get_settings()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"settings unavailable ({type(exc).__name__}); run `make env` and `docker compose up -d`")
    isolated = base.model_copy(
        update={"postgres_db": TEST_DB, "clickhouse_database": TEST_DB, "s3_bucket": TEST_BUCKET}
    )
    # Guard: this module drops schemas, tables and objects; it must only ever touch the test resources.
    assert isolated.database_url.endswith(f"/{TEST_DB}") and isolated.s3_bucket == TEST_BUCKET
    return isolated


@pytest.fixture(scope="module")
def env(settings: Settings, tmp_path_factory: pytest.TempPathFactory, spark: Any) -> Iterator[dict[str, Any]]:
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - environment guard
        pytest.skip("PostgreSQL not reachable; start `docker compose up -d`")

    # Fresh metadata schema.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.cmd_opts = argparse.Namespace(x=[f"database_url={settings.database_url}"])
    command.upgrade(cfg, "head")

    # Fresh ClickHouse tables and lake bucket.
    writer = writer_client(settings)
    for table in ("taxi_trips", "taxi_zones", "schema_migrations"):
        writer.command(f"DROP TABLE IF EXISTS {TEST_DB}.{table}")
    apply_migrations(writer, TEST_DB)
    store = ObjectStore.from_settings(settings)
    store.delete_prefix("")

    work = tmp_path_factory.mktemp("integration")
    source_dir = work / "sources"
    rows = write_fixture_parquet(source_dir / "yellow_tripdata_2025-01.parquet")
    _zone_csv(source_dir / "taxi_zone_lookup.csv")
    sha = hashlib.sha256((source_dir / "yellow_tripdata_2025-01.parquet").read_bytes()).hexdigest()
    manifest = Manifest.model_validate(
        {
            "version": 1,
            "base_dir": work,
            "datasets": {
                "nyc-tlc-yellow": {
                    "name": "Yellow (fixture)",
                    "taxi_type": "yellow",
                    "source_attribution": "test fixture",
                }
            },
            "reference": {"taxi_zones": {"uri": "file://sources/taxi_zone_lookup.csv"}},
            "sources": [
                {
                    "key": "fixture-2025-01",
                    "dataset": "nyc-tlc-yellow",
                    "period": "2025-01",
                    "format": "parquet",
                    "uri": "file://sources/yellow_tripdata_2025-01.parquet",
                    "expected_sha256": sha,
                },
                {
                    "key": "fixture-bad-checksum",
                    "dataset": "nyc-tlc-yellow",
                    "period": "2025-01",
                    "format": "parquet",
                    "uri": "file://sources/yellow_tripdata_2025-01.parquet",
                    "expected_sha256": "0" * 64,
                },
            ],
        }
    )
    deps = PipelineDeps(
        settings=settings.model_copy(update={"pipeline_work_dir": work / "work"}),
        manifest=manifest,
        engine=engine,
        session_factory=make_session_factory(engine),
        store=store,
        clickhouse=writer,
        clickhouse_database=TEST_DB,
        spark_factory=lambda: spark,
    )
    factory = make_session_factory(engine)
    with factory() as session, session.begin():
        for role in Role:
            session.add(
                User(
                    email=f"{role.value}@test.local",
                    display_name=role.value,
                    password_hash=hash_password(PASSWORD),
                    role=role,
                )
            )
    yield {
        "deps": deps,
        "rows": rows,
        "factory": factory,
        "settings": settings,
        "writer": writer,
        "store": store,
    }
    engine.dispose()


@pytest.fixture(scope="module")
def first_run(env: dict[str, Any]) -> Any:
    return run_source(env["deps"], "fixture-2025-01")


def test_pipeline_publishes_expected_rows(env: dict[str, Any], first_run: Any) -> None:
    rows = env["rows"]
    accepted = [r for r in rows if r.accepted]
    assert first_run.input_rows == len(rows)
    assert first_run.accepted_rows == first_run.published_rows == len(accepted)
    assert first_run.quarantined_rows == len(rows) - len(accepted)
    count, amount = (
        env["writer"].query(f"SELECT count(), sum(total_amount) FROM {TEST_DB}.taxi_trips").first_row
    )
    assert count == len(accepted)
    assert Decimal(amount) == sum(Decimal(str(r.total)) for r in accepted)


def test_metadata_records_lineage_quality_and_publication(env: dict[str, Any], first_run: Any) -> None:
    with env["factory"]() as session:
        job = session.get(IngestionJob, first_run.job_id)
        assert job is not None and job.status == JobStatus.COMPLETED
        quality = session.get(DataQualityMetrics, first_run.run_id)
        assert quality is not None
        assert quality.duplicate_row_count == 1
        assert quality.quarantine_reason_counts["pickup_outside_source_period"] == 2
        assert quality.invalid_timestamp_count == 2
        assert quality.missingness_by_column["passenger_count"]["null_count"] == 1
        period = session.scalar(select(DatasetPeriod).where(DatasetPeriod.dataset_id == "nyc-tlc-yellow"))
        assert period is not None and period.run_id == first_run.run_id
        assert str(period.min_pickup_date) == "2025-01-06" and str(period.max_pickup_date) == "2025-01-31"


def test_lake_holds_immutable_raw_curated_and_quarantine(env: dict[str, Any], first_run: Any) -> None:
    store = env["store"]
    raw = store.head("raw/taxi_type=yellow/year=2025/month=01/yellow_tripdata_2025-01.parquet")
    assert raw is not None and len(raw["Metadata"]["sha256"]) == 64
    assert store.list_keys(f"curated/taxi_type=yellow/year=2025/month=01/run_id={first_run.run_id}/")
    assert store.list_keys(f"quarantine/run_id={first_run.run_id}/")


def test_rerun_replaces_partition_instead_of_duplicating(env: dict[str, Any], first_run: Any) -> None:
    second = run_source(env["deps"], "fixture-2025-01")
    assert second.run_id != first_run.run_id
    count, runs = (
        env["writer"].query(f"SELECT count(), uniqExact(run_id) FROM {TEST_DB}.taxi_trips").first_row
    )
    assert count == first_run.accepted_rows and runs == 1
    with env["factory"]() as session:
        period = session.scalar(select(DatasetPeriod))
        assert period is not None and period.run_id == second.run_id
        assert session.get(IngestionJob, second.job_id).attempt == 2  # type: ignore[union-attr]


def test_checksum_failure_is_recorded_and_publishes_nothing(env: dict[str, Any], first_run: Any) -> None:
    before = env["writer"].query(f"SELECT count() FROM {TEST_DB}.taxi_trips").first_row[0]
    with pytest.raises(PipelineError, match="checksum mismatch"):
        run_source(env["deps"], "fixture-bad-checksum")
    with env["factory"]() as session:
        job = session.scalars(select(IngestionJob).order_by(IngestionJob.created_at.desc())).first()
        assert job is not None and job.status == JobStatus.FAILED
        assert job.error_summary and "checksum mismatch" in job.error_summary
    assert env["writer"].query(f"SELECT count() FROM {TEST_DB}.taxi_trips").first_row[0] == before


@pytest.fixture(scope="module")
def api(env: dict[str, Any], first_run: Any) -> TestClient:
    return TestClient(create_app(env["settings"]))


def _login(client: TestClient, role: Role) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": f"{role.value}@test.local", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def test_api_overview_matches_independent_expectation(api: TestClient, env: dict[str, Any]) -> None:
    _login(api, Role.ANALYST)
    body = api.get("/api/v1/analytics/overview").json()
    expected = expected_overview(env["rows"])
    kpis = body["kpis"]
    assert kpis["total_trips"]["value"] == expected["total_trips"]
    assert kpis["total_recorded_amount"]["value"] == pytest.approx(
        expected["total_recorded_amount"], abs=0.005
    )
    assert kpis["avg_total_amount"]["value"] == pytest.approx(expected["avg_total_amount"])
    assert kpis["avg_trip_distance"]["value"] == pytest.approx(expected["avg_trip_distance"])
    assert kpis["avg_trip_duration_minutes"]["value"] == pytest.approx(expected["avg_trip_duration_minutes"])
    assert kpis["total_recorded_amount"]["excluded_rows"] == expected["excluded_amount_rows"]
    assert kpis["avg_trip_distance"]["excluded_rows"] == expected["excluded_distance_rows"]
    assert kpis["avg_trip_duration_minutes"]["excluded_rows"] == expected["excluded_duration_rows"]
    assert body["meta"]["coverage"]["periods"][0]["period"] == "2025-01"


def test_api_time_series_and_filters(api: TestClient, env: dict[str, Any]) -> None:
    _login(api, Role.VIEWER)
    series = api.get("/api/v1/analytics/trips-over-time?granularity=day").json()
    accepted = [r for r in env["rows"] if r.accepted]
    assert sum(p["trips"] for p in series["points"]) == len(accepted)
    by_day = {p["bucket"]: p["trips"] for p in series["points"]}
    assert by_day["2025-01-06"] == sum(1 for r in accepted if r.pickup and r.pickup.day == 6)

    jfk = api.get("/api/v1/analytics/overview?pickup_zone=132").json()
    assert jfk["kpis"]["total_trips"]["value"] == sum(1 for r in accepted if r.pu == 132)
    empty = api.get("/api/v1/analytics/overview?start_date=2025-03-01").json()
    assert empty["data_state"] == "empty" and empty["kpis"] is None
    assert api.get("/api/v1/ingestion-jobs").status_code == 403  # viewer


def test_login_failures_are_audited_and_uniform(api: TestClient, env: dict[str, Any]) -> None:
    unknown = api.post("/api/v1/auth/login", json={"email": "nobody@test.local", "password": "x" * 12})
    wrong = api.post("/api/v1/auth/login", json={"email": "analyst@test.local", "password": "x" * 12})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]
    with env["factory"]() as session:
        failures = session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'auth.login' AND outcome = 'failure'")
        ).scalar()
        assert failures is not None and failures >= 2


def test_clickhouse_reader_is_read_only_and_bounded(env: dict[str, Any]) -> None:
    reader = reader_client(env["settings"])
    assert reader.query(f"SELECT count() FROM {TEST_DB}.taxi_trips").first_row[0] > 0
    for statement in (
        f"INSERT INTO {TEST_DB}.taxi_zones (location_id) VALUES (999)",
        f"DROP TABLE {TEST_DB}.taxi_zones",
        f"CREATE TABLE {TEST_DB}.x (a UInt8) ENGINE = Memory",
        f"ALTER TABLE {TEST_DB}.taxi_trips DELETE WHERE 1",
    ):
        with pytest.raises(Exception, match=r"ACCESS_DENIED|Not enough privileges|readonly"):
            reader.command(statement)
    for query in (
        "SELECT * FROM url('http://seaweedfs:8333/', 'CSV')",
        f"SELECT count() FROM s3(tripscope_lake, filename='{TEST_BUCKET}/raw/*', format='Parquet')",
        "SELECT sleep(1) SETTINGS max_execution_time = 600",
    ):
        with pytest.raises(
            Exception, match=r"ACCESS_DENIED|Not enough privileges|SETTING_CONSTRAINT_VIOLATION"
        ):
            reader.query(query)


def test_object_store_identities_are_least_privilege(env: dict[str, Any]) -> None:
    values = dotenv_values(REPO / ".env")
    endpoint = env["settings"].s3_endpoint_url
    cfg = BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 1})
    clickhouse_identity = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        config=cfg,
        aws_access_key_id=values["S3_CLICKHOUSE_ACCESS_KEY"],
        aws_secret_access_key=values["S3_CLICKHOUSE_SECRET_KEY"],
    )
    clickhouse_identity.list_objects_v2(Bucket=TEST_BUCKET, MaxKeys=1)  # read allowed
    with pytest.raises(ClientError):
        clickhouse_identity.put_object(Bucket=TEST_BUCKET, Key=f"probe-{uuid.uuid4()}", Body=b"x")
    anonymous = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        config=cfg,
        aws_access_key_id="nobody",
        aws_secret_access_key="not-a-real-secret",
    )
    with pytest.raises(ClientError):
        anonymous.list_objects_v2(Bucket=TEST_BUCKET, MaxKeys=1)
