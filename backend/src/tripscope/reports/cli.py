"""`tripscope-report-worker` command line: generate queued report files until stopped (SIGTERM/SIGINT).

tripscope-report-worker            # poll for queued report runs
tripscope-report-worker --once     # generate at most one run, then exit
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from tripscope.analytics.service import AnalyticsService
from tripscope.core.logging import configure_logging
from tripscope.core.settings import get_settings
from tripscope.metadata.db import make_engine, make_session_factory
from tripscope.reports.worker import ReportWorker
from tripscope.storage.clickhouse import reader_client
from tripscope.storage.object_store import ObjectStore

log = logging.getLogger("tripscope.reports")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripscope-report-worker")
    parser.add_argument("--once", action="store_true", help="generate at most one queued run, then exit")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    configure_logging()
    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    worker = ReportWorker(
        session_factory=sessions,
        # Reports read through the same read-only ClickHouse user and limits as the dashboard.
        analytics=AnalyticsService(
            client_factory=lambda: reader_client(settings),
            session_factory=sessions,
            database=settings.clickhouse_database,
            max_range_days=settings.analytics_max_range_days,
        ),
        store_factory=lambda: ObjectStore.from_settings(settings),
        poll_seconds=args.poll_seconds,
    )
    if args.once:
        worker.run_once()
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())  # finish the current file, then exit
    worker.run_forever(stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
