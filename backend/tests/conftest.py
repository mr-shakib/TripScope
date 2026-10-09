from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]


def _java_home() -> Path | None:
    """SPARK_JAVA_HOME from the environment or the repository .env (unit tests need no other settings)."""
    value = os.environ.get("SPARK_JAVA_HOME") or dotenv_values(REPO_ROOT / ".env").get("SPARK_JAVA_HOME")
    return Path(value) if value else None


@pytest.fixture(scope="session")
def spark(tmp_path_factory: pytest.TempPathFactory) -> Iterator[object]:
    """One local SparkSession for the whole test session (JVM start-up is the slow part)."""
    from tripscope.pipeline.spark_transform import build_spark_session

    session = build_spark_session(
        master="local[2]",
        driver_memory="1g",
        local_dir=tmp_path_factory.mktemp("spark"),
        java_home=_java_home(),
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()
