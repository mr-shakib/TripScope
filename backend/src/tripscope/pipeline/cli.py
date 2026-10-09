"""`tripscope-pipeline` command line.

tripscope-pipeline migrate                         # create/upgrade ClickHouse tables
tripscope-pipeline sources                         # list manifest sources
tripscope-pipeline run --source yellow-2025-01     # run sources end to end (repeat --source, or --all)
tripscope-pipeline worker                          # process queued jobs until stopped (SIGTERM/SIGINT)
tripscope-pipeline build-aggregates [--period YYYY-MM]   # backfill aggregates for published months
tripscope-pipeline inspect-file PATH --format csv  # validate a file without ingesting it
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tripscope.core.errors import TripScopeError
from tripscope.core.logging import configure_logging
from tripscope.core.settings import get_settings

log = logging.getLogger("tripscope.pipeline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripscope-pipeline")
    parser.add_argument(
        "--manifest", type=Path, default=None, help="source manifest (default: manifests/sources.yaml)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply ClickHouse schema migrations")
    sub.add_parser("sources", help="list sources in the manifest")
    run = sub.add_parser("run", help="ingest, transform, load and publish sources")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--source", action="append", help="manifest source key (repeatable)")
    target.add_argument("--all", action="store_true", help="every source in the manifest, in order")
    worker = sub.add_parser("worker", help="process queued ingestion jobs")
    worker.add_argument("--once", action="store_true", help="process at most one job, then exit")
    worker.add_argument("--poll-seconds", type=float, default=2.0)
    aggregates = sub.add_parser("build-aggregates", help="rebuild pre-aggregates from published data")
    aggregates.add_argument("--period", action="append", help="YYYY-MM (repeatable; default: all published)")
    inspect = sub.add_parser("inspect-file", help="validate a local file without ingesting it")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--format", choices=["parquet", "csv"], required=True)
    args = parser.parse_args(argv)

    configure_logging()
    try:
        return _dispatch(args)
    except TripScopeError as exc:
        log.error("command failed", extra={"command": args.command, "error": exc.message})
        print(f"error: {exc.message}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "inspect-file":
        return _inspect_file(args.path, args.format)

    from tripscope.pipeline.manifest import load_manifest

    settings = get_settings()
    manifest = load_manifest(args.manifest or settings.resolved_manifest_path)
    if args.command == "sources":
        for source in manifest.sources:
            print(f"{source.key}\t{source.dataset}\t{source.period}\t{source.format}\t{source.uri}")
        return 0

    from tripscope.pipeline.clickhouse_load import apply_migrations
    from tripscope.storage.clickhouse import writer_client

    clickhouse = writer_client(settings)
    if args.command == "migrate":
        applied = apply_migrations(clickhouse, settings.clickhouse_database)
        print(json.dumps({"applied": applied, "database": settings.clickhouse_database}))
        return 0

    apply_migrations(clickhouse, settings.clickhouse_database)
    if args.command == "build-aggregates":
        return _build_aggregates(settings, clickhouse, args.period)

    from tripscope.metadata.db import make_engine, make_session_factory
    from tripscope.pipeline.runner import PipelineDeps, run_source
    from tripscope.pipeline.spark_transform import build_spark_session
    from tripscope.storage.object_store import ObjectStore

    engine = make_engine(settings.database_url)
    spark_holder: list[Any] = []

    def spark_factory() -> Any:
        if not spark_holder:  # one SparkSession per process, reused across runs
            session = build_spark_session(
                master=settings.spark_master,
                driver_memory=settings.spark_driver_memory,
                local_dir=settings.pipeline_work_dir / "spark",
                java_home=settings.spark_java_home,
            )
            session.sparkContext.setLogLevel("WARN")
            spark_holder.append(session)
        return spark_holder[0]

    deps = PipelineDeps(
        settings=settings,
        manifest=manifest,
        engine=engine,
        session_factory=make_session_factory(engine),
        store=ObjectStore.from_settings(settings),
        clickhouse=clickhouse,
        clickhouse_database=settings.clickhouse_database,
        spark_factory=spark_factory,
    )
    try:
        if args.command == "worker":
            return _worker(deps, spark_holder, once=args.once, poll_seconds=args.poll_seconds)
        keys = [s.key for s in manifest.sources] if args.all else args.source
        failures = 0
        for key in keys:
            try:
                print(json.dumps(asdict(run_source(deps, key)), default=str, indent=2))
            except TripScopeError as exc:
                failures += 1
                print(f"error: {key}: {exc.message}", file=sys.stderr)
        return 1 if failures else 0
    finally:
        for session in spark_holder:
            session.stop()
        engine.dispose()


def _worker(deps: Any, spark_holder: list[Any], *, once: bool, poll_seconds: float) -> int:
    from tripscope.pipeline.worker import Worker

    def cancel_spark() -> None:
        for session in spark_holder:
            session.sparkContext.cancelAllJobs()

    worker = Worker(deps, poll_seconds=poll_seconds, on_cancel=cancel_spark)
    if once:
        worker.run_once()
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())  # finish the current job, then exit
    worker.run_forever(stop)
    return 0


def _build_aggregates(settings: Any, clickhouse: Any, periods: list[str] | None) -> int:
    from datetime import date

    from sqlalchemy import select

    from tripscope.metadata.db import make_engine, make_session_factory
    from tripscope.metadata.models import Dataset, DatasetPeriod
    from tripscope.pipeline.aggregates import rebuild_period

    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            rows = session.execute(
                select(Dataset.taxi_type, DatasetPeriod.data_period).join(
                    Dataset, Dataset.id == DatasetPeriod.dataset_id
                )
            ).all()
    finally:
        engine.dispose()
    wanted = {date(int(p[:4]), int(p[5:7]), 1) for p in periods} if periods else None
    for taxi_type, period in sorted(rows, key=lambda r: r[1]):
        if wanted is not None and period not in wanted:
            continue
        counts = rebuild_period(
            clickhouse, database=settings.clickhouse_database, taxi_type=taxi_type, period=period
        )
        print(json.dumps({"taxi_type": taxi_type, "period": period.strftime("%Y-%m"), "rows": counts}))
    return 0


def _inspect_file(path: Path, file_format: str) -> int:
    from tripscope.pipeline.acquire import inspect_csv, inspect_parquet
    from tripscope.pipeline.canonical import map_source_schema

    inspection = inspect_csv(path) if file_format == "csv" else inspect_parquet(path)
    mapping = map_source_schema("yellow", inspection.columns)
    print(json.dumps({"rows": inspection.num_rows, **mapping.as_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
