"""`tripscope-pipeline` command line.

tripscope-pipeline migrate                      # create/upgrade ClickHouse tables
tripscope-pipeline sources                      # list manifest sources
tripscope-pipeline run --source yellow-2025-01  # run one source end to end
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from tripscope.core.errors import TripScopeError
from tripscope.core.logging import configure_logging
from tripscope.core.settings import find_repo_root, get_settings

log = logging.getLogger("tripscope.pipeline")


def _default_manifest() -> Path:
    root = find_repo_root()
    return (root / "manifests" / "sources.yaml") if root else Path("manifests/sources.yaml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripscope-pipeline")
    parser.add_argument(
        "--manifest", type=Path, default=None, help="source manifest (default: manifests/sources.yaml)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply ClickHouse schema migrations")
    sub.add_parser("sources", help="list sources in the manifest")
    run = sub.add_parser("run", help="ingest, transform, load and publish one source")
    run.add_argument("--source", required=True)
    args = parser.parse_args(argv)

    configure_logging()
    try:
        return _dispatch(args)
    except TripScopeError as exc:
        log.error("command failed", extra={"command": args.command, "error": exc.message})
        print(f"error: {exc.message}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    from tripscope.pipeline.manifest import load_manifest

    manifest = load_manifest(args.manifest or _default_manifest())
    if args.command == "sources":
        for source in manifest.sources:
            print(f"{source.key}\t{source.dataset}\t{source.period}\t{source.uri}")
        return 0

    settings = get_settings()
    from tripscope.pipeline.clickhouse_load import apply_migrations
    from tripscope.storage.clickhouse import writer_client

    clickhouse = writer_client(settings)
    if args.command == "migrate":
        applied = apply_migrations(clickhouse, settings.clickhouse_database)
        print(json.dumps({"applied": applied, "database": settings.clickhouse_database}))
        return 0

    from tripscope.metadata.db import make_engine, make_session_factory
    from tripscope.pipeline.runner import PipelineDeps, run_source
    from tripscope.pipeline.spark_transform import build_spark_session
    from tripscope.storage.object_store import ObjectStore

    apply_migrations(clickhouse, settings.clickhouse_database)
    engine = make_engine(settings.database_url)
    spark_holder: list[object] = []

    def spark_factory():  # type: ignore[no-untyped-def]
        session = build_spark_session(
            master=settings.spark_master,
            driver_memory=settings.spark_driver_memory,
            local_dir=settings.pipeline_work_dir / "spark",
            java_home=settings.spark_java_home,
        )
        session.sparkContext.setLogLevel("WARN")
        spark_holder.append(session)
        return session

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
        summary = run_source(deps, args.source)
    finally:
        for session in spark_holder:
            session.stop()  # type: ignore[attr-defined]
        engine.dispose()
    print(json.dumps(asdict(summary), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
