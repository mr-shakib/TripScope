"""Shared integration environment: real services, isolated test resources, fresh per test module.

PostgreSQL `tripscope_test`, ClickHouse `tripscope_test` and bucket `tripscope-test` are wiped and rebuilt for
each module, so modules never depend on each other's runs.
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tests.fixtures.tlc_fixture import fixture_rows, shift_year, write_fixture_parquet, write_open_data_csv
from tests.fixtures.zones_fixture import write_zone_zip
from tripscope.core.passwords import hash_password
from tripscope.core.settings import Settings, get_settings
from tripscope.metadata.db import make_engine, make_session_factory
from tripscope.metadata.models import Role, User
from tripscope.pipeline.clickhouse_load import apply_migrations
from tripscope.pipeline.manifest import load_manifest
from tripscope.pipeline.runner import PipelineDeps
from tripscope.storage.clickhouse import writer_client
from tripscope.storage.object_store import ObjectStore

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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def settings() -> Settings:
    try:
        base = get_settings()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"settings unavailable ({type(exc).__name__}); run `make env` and `docker compose up -d`")
    isolated = base.model_copy(
        update={"postgres_db": TEST_DB, "clickhouse_database": TEST_DB, "s3_bucket": TEST_BUCKET}
    )
    # Guard: these fixtures drop schemas, tables and objects; they must only ever touch the test resources.
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

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.cmd_opts = argparse.Namespace(x=[f"database_url={settings.database_url}"])
    command.upgrade(cfg, "head")

    writer = writer_client(settings)
    for (table,) in writer.query(f"SHOW TABLES FROM {TEST_DB}").result_rows:
        writer.command(f"DROP TABLE IF EXISTS {TEST_DB}.{table}")
    apply_migrations(writer, TEST_DB)
    store = ObjectStore.from_settings(settings)
    store.delete_prefix("")

    work = tmp_path_factory.mktemp("integration")
    sources = work / "sources"
    rows = write_fixture_parquet(sources / "yellow_tripdata_2025-01.parquet")
    csv_rows = shift_year(fixture_rows(), -2)  # same expectations, January 2023
    write_open_data_csv(sources / "open_data_2023-01.csv", csv_rows)
    write_open_data_csv(sources / "open_data_2023-02.csv", shift_year(fixture_rows(), -2), truncated_after=5)
    _zone_csv(sources / "taxi_zone_lookup.csv")
    write_zone_zip(sources / "taxi_zones.zip")
    manifest_doc = {
        "version": 1,
        "datasets": {
            "nyc-tlc-yellow": {
                "name": "Yellow (fixture)",
                "taxi_type": "yellow",
                "source_attribution": "fixture",
            },
            "nyc-open-data-yellow": {
                "name": "Yellow via Open Data (fixture)",
                "taxi_type": "yellow",
                "source_attribution": "fixture",
            },
        },
        "reference": {
            "taxi_zones": {"uri": "file://sources/taxi_zone_lookup.csv"},
            "taxi_zone_shapes": {"uri": "file://sources/taxi_zones.zip"},
        },
        "sources": [
            {
                "key": "fixture-2025-01",
                "dataset": "nyc-tlc-yellow",
                "period": "2025-01",
                "format": "parquet",
                "uri": "file://sources/yellow_tripdata_2025-01.parquet",
                "expected_sha256": _sha(sources / "yellow_tripdata_2025-01.parquet"),
            },
            {
                "key": "fixture-bad-checksum",
                "dataset": "nyc-tlc-yellow",
                "period": "2025-01",
                "format": "parquet",
                "uri": "file://sources/yellow_tripdata_2025-01.parquet",
                "expected_sha256": "0" * 64,
            },
            {
                "key": "fixture-csv-2023-01",
                "dataset": "nyc-open-data-yellow",
                "period": "2023-01",
                "format": "csv",
                "csv_profile": "nyc_open_data",
                "uri": "file://sources/open_data_2023-01.csv",
            },
            {
                "key": "fixture-csv-truncated",
                "dataset": "nyc-open-data-yellow",
                "period": "2023-02",
                "format": "csv",
                "csv_profile": "nyc_open_data",
                "uri": "file://sources/open_data_2023-02.csv",
            },
        ],
    }
    manifest_path = work / "manifests" / "sources.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(yaml.safe_dump(manifest_doc))
    run_settings = settings.model_copy(
        update={"pipeline_work_dir": work / "work", "manifest_path": manifest_path}
    )
    deps = PipelineDeps(
        settings=run_settings,
        manifest=load_manifest(manifest_path),
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
        "csv_rows": csv_rows,
        "factory": factory,
        "settings": run_settings,
        "writer": writer,
        "store": store,
    }
    engine.dispose()
