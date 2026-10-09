"""ClickHouse client factories. The writer is for the pipeline only; the API and AI tools get the reader,
whose limits (read-only, timeout, max rows/memory) are also enforced by a server-side settings profile."""

from __future__ import annotations

from typing import TYPE_CHECKING

import clickhouse_connect
from clickhouse_connect.driver.client import Client

if TYPE_CHECKING:
    from tripscope.core.settings import Settings


def writer_client(settings: Settings, *, database: str | None = None) -> Client:
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_writer_user,
        password=settings.clickhouse_writer_password.get_secret_value(),
        database=database or settings.clickhouse_database,
        connect_timeout=10,
        send_receive_timeout=1800,
        autogenerate_session_id=False,
    )


def reader_client(settings: Settings, *, database: str | None = None) -> Client:
    timeout = settings.analytics_query_timeout_seconds
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_reader_user,
        password=settings.clickhouse_reader_password.get_secret_value(),
        database=database or settings.clickhouse_database,
        connect_timeout=5,
        send_receive_timeout=timeout + 5,
        autogenerate_session_id=False,
        settings={"max_execution_time": timeout},
    )
