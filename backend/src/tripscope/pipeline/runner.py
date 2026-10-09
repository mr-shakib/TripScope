"""End-to-end orchestration for one manifest source (spec §7.1 steps 1-13).

discover → acquire → inspect → raw upload → zones → Spark transform → curated/quarantine upload →
ClickHouse stage/verify/replace → publish period → record metrics. Every stage is timed and recorded on the
processing run; failures mark the job failed with a redacted, actionable summary.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from tripscope.core.errors import PipelineError
from tripscope.core.identifiers import redact
from tripscope.metadata.models import (
    DataQualityMetrics,
    DatasetPeriod,
    DataSource,
    IngestionJob,
    JobStatus,
    ProcessingRun,
)
from tripscope.metadata.sources import register_source
from tripscope.pipeline import lake
from tripscope.pipeline.acquire import AcquiredFile, acquire, inspect_csv, inspect_parquet
from tripscope.pipeline.canonical import map_source_schema
from tripscope.pipeline.clickhouse_load import LoadVerification, load_partition, load_zones
from tripscope.pipeline.manifest import Manifest, SourceSpec
from tripscope.pipeline.rules import QualityRules
from tripscope.pipeline.spark_transform import TransformContext, TransformResult, transform_file
from tripscope.pipeline.zones import Zone, mapped_zone_ids, parse_zone_lookup

if TYPE_CHECKING:
    from clickhouse_connect.driver.client import Client
    from pyspark.sql import SparkSession

    from tripscope.core.settings import Settings
    from tripscope.storage.object_store import ObjectStore, UploadedPrefix

log = logging.getLogger(__name__)


@dataclass
class PipelineDeps:
    settings: Settings
    manifest: Manifest
    engine: Engine
    session_factory: sessionmaker[Session]
    store: ObjectStore
    clickhouse: Client
    clickhouse_database: str
    spark_factory: Callable[[], SparkSession]
    rules: QualityRules = field(default_factory=QualityRules)


@dataclass(frozen=True)
class RunSummary:
    job_id: uuid.UUID
    run_id: uuid.UUID
    source_key: str
    input_rows: int
    accepted_rows: int
    quarantined_rows: int
    published_rows: int
    duration_seconds: float
    stage_seconds: dict[str, float]


class _RunRecorder:
    """Persists stage progress and timings so job status is visible while a run is in flight."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        run_id: uuid.UUID,
        logs: _RunLogHandler,
        cancel_check: Callable[[], None],
    ) -> None:
        self._factory = factory
        self.run_id = run_id
        self.timings: dict[str, float] = {}
        self.logs = logs
        self.cancel_check = cancel_check

    def _update(self, **fields: Any) -> None:
        with self._factory() as session, session.begin():
            run = session.get(ProcessingRun, self.run_id)
            assert run is not None
            for key, value in fields.items():
                setattr(run, key, value)

    def flush_logs(self) -> None:
        self._update(log_events=list(self.logs.entries))

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        self.cancel_check()
        self._update(current_stage=name)
        log.info("stage started", extra={"stage": name})
        started = time.perf_counter()
        status = "failed"
        try:
            yield
            status = "completed"
        finally:
            seconds = round(time.perf_counter() - started, 3)
            self.timings[name] = seconds
            log.info("pipeline stage", extra={"stage": name, "status": status, "seconds": seconds})
            self._update(stage_timings=dict(self.timings), log_events=list(self.logs.entries))


def _now() -> datetime:
    return datetime.now(UTC)


class JobCancelledError(PipelineError):
    code = "job_cancelled"


MAX_LOG_EVENTS = 500


class _RunLogHandler(logging.Handler):
    """Collects this run's TripScope log records (same thread) for the run's log view, secrets redacted."""

    _SKIP = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

    def __init__(self, secrets: list[str]) -> None:
        super().__init__(level=logging.INFO)
        self.thread_id = threading.get_ident()
        self.secrets = secrets
        self.entries: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id or len(self.entries) >= MAX_LOG_EVENTS:
            return
        fields = {k: v for k, v in vars(record).items() if k not in self._SKIP and not k.startswith("_")}
        self.entries.append(
            {
                "at": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": redact(record.getMessage(), self.secrets),
                "fields": {k: redact(str(v), self.secrets) for k, v in fields.items()},
            }
        )


@contextmanager
def _source_lock(engine: Engine, source_key: str) -> Iterator[None]:
    """PostgreSQL session advisory lock on a dedicated autocommit connection: one run per source at a time,
    released automatically if the process dies (the connection closes)."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        locked = conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": source_key}).scalar()
        if not locked:
            raise PipelineError(f"another run for {source_key} is in progress")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": source_key})


def _acquire_reference_zones(deps: PipelineDeps, work: Path) -> tuple[list[Zone], str]:
    ref = deps.manifest.reference.get("taxi_zones")
    if ref is None:
        raise PipelineError("manifest has no reference.taxi_zones entry; zone mapping cannot be validated")
    file_name = Path(ref.uri).name
    local = deps.manifest.resolve_local_path(ref.uri) if ref.uri.startswith("file:") else None
    acquired = acquire(
        ref.uri,
        dest_dir=work / "reference",
        file_name=file_name,
        max_bytes=1_000_000,
        expected_sha256=ref.expected_sha256,
        local_path=local,
    )
    zones = parse_zone_lookup(acquired.local_path)
    deps.store.put_immutable(
        lake.reference_key("taxi_zones", acquired.sha256, file_name),
        acquired.local_path,
        sha256=acquired.sha256,
        metadata={"source-uri": ref.uri, "retrieved-at": acquired.retrieved_at.isoformat()},
    )
    return zones, acquired.sha256


def ensure_zone_geometry(deps: PipelineDeps, work: Path, *, force: bool = False) -> bool:
    """Build the map's zone GeoJSON into the lake if it is missing (or `force`). Returns True if built."""
    from tripscope.pipeline.zone_geometry import GEOMETRY_KEY, build_zone_geojson, geojson_bytes

    ref = deps.manifest.reference.get("taxi_zone_shapes")
    if ref is None or (not force and deps.store.head(GEOMETRY_KEY) is not None):
        return False
    file_name = Path(ref.uri).name
    local = deps.manifest.resolve_local_path(ref.uri) if ref.uri.startswith("file:") else None
    acquired = acquire(
        ref.uri,
        dest_dir=work / "reference",
        file_name=file_name,
        max_bytes=50 * 1024 * 1024,
        expected_sha256=ref.expected_sha256,
        local_path=local,
    )
    deps.store.put_immutable(
        lake.reference_key("taxi_zone_shapes", acquired.sha256, file_name),
        acquired.local_path,
        sha256=acquired.sha256,
        metadata={"source-uri": ref.uri, "retrieved-at": acquired.retrieved_at.isoformat()},
    )
    collection = build_zone_geojson(acquired.local_path)
    deps.store.put_bytes(
        GEOMETRY_KEY,
        geojson_bytes(collection),
        content_type="application/geo+json",
        metadata={"source-sha256": acquired.sha256, "zones": str(len(collection["features"]))},
    )
    log.info("zone geometry built", extra={"zones": len(collection["features"])})
    return True


def run_source(
    deps: PipelineDeps,
    source_key: str,
    *,
    trigger: str = "cli",
    requested_by: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    cancel_event: threading.Event | None = None,
) -> RunSummary:
    """Run one source. With `job_id`, executes a job a worker has already claimed (status running);
    otherwise creates a new job (CLI). `cancel_event` is set by the worker when a cancel is requested."""
    source = deps.manifest.get_source(source_key)
    with _source_lock(deps.engine, source.key):
        run = _SourceRun(
            deps, source, trigger=trigger, requested_by=requested_by, job_id=job_id, cancel_event=cancel_event
        )
        return run.execute()


class _SourceRun:
    """State for a single run of one source; each method is one pipeline stage."""

    def __init__(
        self,
        deps: PipelineDeps,
        source: SourceSpec,
        *,
        trigger: str,
        requested_by: uuid.UUID | None,
        job_id: uuid.UUID | None,
        cancel_event: threading.Event | None,
    ) -> None:
        self.deps, self.source = deps, source
        self.factory = deps.session_factory
        self.taxi_type = deps.manifest.datasets[source.dataset].taxi_type
        self.work = deps.settings.pipeline_work_dir
        self.started = _now()
        self.acquired: AcquiredFile | None = None
        self.result: TransformResult | None = None
        self.schema_version = ""
        self.cancel_event = cancel_event
        with self.factory() as session, session.begin():
            data_source = register_source(session, deps.manifest, source)
            if job_id is None:
                attempts = (
                    session.query(IngestionJob).filter(IngestionJob.data_source_id == data_source.id).count()
                )
                job = IngestionJob(
                    data_source_id=data_source.id,
                    status=JobStatus.RUNNING,
                    trigger=trigger,
                    requested_by=requested_by,
                    attempt=attempts + 1,
                    started_at=self.started,
                )
                session.add(job)
                session.flush()
            else:
                claimed = session.get(IngestionJob, job_id)
                if (
                    claimed is None
                    or claimed.status != JobStatus.RUNNING
                    or claimed.data_source_id != data_source.id
                ):
                    raise PipelineError(f"job {job_id} is not a running job for {source.key}")
                job = claimed
                self.started = job.started_at or self.started
            run = ProcessingRun(
                job_id=job.id,
                status=JobStatus.RUNNING,
                started_at=self.started,
                quality_rules=deps.rules.model_dump(),
            )
            session.add(run)
            session.flush()
            self.job_id, self.run_id, self.data_source_id = job.id, run.id, data_source.id
        self.logs = _RunLogHandler(deps.settings.secret_values())
        self.recorder = _RunRecorder(self.factory, self.run_id, self.logs, self._check_cancelled)
        self.run_dir = self.work / "runs" / str(self.run_id)

    def _cancel_requested(self) -> bool:
        if self.cancel_event is not None and self.cancel_event.is_set():
            return True
        with self.factory() as session:
            requested = session.scalar(
                select(IngestionJob.cancel_requested_at).where(IngestionJob.id == self.job_id)
            )
        return requested is not None

    def _check_cancelled(self) -> None:
        if self._cancel_requested():
            raise JobCancelledError("cancelled by request")

    def execute(self) -> RunSummary:
        tripscope_logger = logging.getLogger("tripscope")
        tripscope_logger.addHandler(self.logs)
        try:
            log.info("run started", extra={"source": self.source.key, "run_id": str(self.run_id)})
            with self.recorder.stage("acquire"):
                acquired = self._acquire()
            with self.recorder.stage("inspect"):
                if self.source.format == "csv":
                    inspection = inspect_csv(acquired.local_path)
                else:
                    inspection = inspect_parquet(acquired.local_path)
                log.info(
                    "source inspected",
                    extra={
                        "rows": inspection.num_rows,
                        "columns": len(inspection.columns),
                        "format": self.source.format,
                    },
                )
                mapping = map_source_schema(self.taxi_type, inspection.columns)
                self.schema_version = mapping.schema_version
            with self.recorder.stage("store_raw"):
                self._store_raw(
                    acquired,
                    mapping.as_dict(),
                    inspection.num_rows,
                    inspection.created_by,
                    list(mapping.unavailable),
                )
            with self.recorder.stage("zones"):
                zones = self._load_reference_data()
            with self.recorder.stage("spark_transform"):
                ctx = TransformContext(
                    run_id=str(self.run_id),
                    taxi_type=self.taxi_type,
                    source_file=self.source.file_name,
                    period_start=self.source.period_start,
                    period_end_exclusive=self.source.period_end_exclusive,
                    ingested_at=self.started,
                    mapping=mapping,
                    rules=self.deps.rules,
                    mapped_zone_ids=mapped_zone_ids(zones),
                    source_format=self.source.format,
                    timestamp_format=self.source.timestamp_format,
                )
                result = transform_file(self.deps.spark_factory(), acquired.local_path, ctx, self.run_dir)
                self.result = result
            with self.recorder.stage("store_curated"):
                store = self.deps.store
                curated = store.upload_directory(
                    result.curated_dir,
                    lake.curated_prefix(self.taxi_type, self.source.period_start, str(self.run_id)),
                )
                quarantine = store.upload_directory(
                    result.quarantine_dir, lake.quarantine_prefix(str(self.run_id))
                )
                if not curated.keys:
                    raise PipelineError("Spark produced no curated files")
            with self.recorder.stage("load_clickhouse"):
                verification = load_partition(
                    self.deps.clickhouse,
                    database=self.deps.clickhouse_database,
                    lake_glob=lake.s3_glob(store.bucket, curated.prefix),
                    taxi_type=self.taxi_type,
                    period=self.source.period_start,
                    run_id=str(self.run_id),
                    expected_rows=result.accepted_rows,
                    expected_amount_sum=result.accepted_total_amount_sum,
                    before_publish=self._check_cancelled,
                )
            with self.recorder.stage("publish"):
                duration = self._publish(result, verification, curated, quarantine)
        except Exception as exc:
            cancelled = isinstance(exc, JobCancelledError) or self._cancel_requested()
            if cancelled:
                stage = self.recorder.timings and list(self.recorder.timings)[-1]
                summary = f"cancelled by request (during {stage or 'start-up'}); nothing was published"
            else:
                summary = redact(f"{type(exc).__name__}: {exc}", self.deps.settings.secret_values())[:2000]
            log.error("pipeline run ended", extra={"run_id": str(self.run_id), "error": summary})
            self._record_failure(summary, cancelled=cancelled)
            if cancelled:
                raise JobCancelledError(f"run {self.run_id} {summary}") from exc
            raise PipelineError(f"run {self.run_id} failed: {summary}") from exc
        finally:
            tripscope_logger.removeHandler(self.logs)
            shutil.rmtree(self.run_dir, ignore_errors=True)

        return RunSummary(
            job_id=self.job_id,
            run_id=self.run_id,
            source_key=self.source.key,
            input_rows=result.input_rows,
            accepted_rows=result.accepted_rows,
            quarantined_rows=result.quarantined_rows,
            published_rows=verification.published_rows,
            duration_seconds=duration,
            stage_seconds=dict(self.recorder.timings),
        )

    def _load_reference_data(self) -> list[Zone]:
        """Zone lookup into ClickHouse; map boundaries into the lake if missing (never blocks the run)."""
        zones, zones_sha = _acquire_reference_zones(self.deps, self.work)
        load_zones(
            self.deps.clickhouse, database=self.deps.clickhouse_database, zones=zones, source_sha256=zones_sha
        )
        try:
            ensure_zone_geometry(self.deps, self.work)
        except Exception as exc:
            log.warning("zone geometry not built", extra={"error": f"{type(exc).__name__}: {exc}"})
        return zones

    def _acquire(self) -> AcquiredFile:
        source = self.source
        local = self.deps.manifest.resolve_local_path(source.uri) if source.uri.startswith("file:") else None
        self.acquired = acquire(
            source.uri,
            dest_dir=self.work / "landing" / self.taxi_type,
            file_name=source.file_name,
            max_bytes=self.deps.settings.pipeline_max_source_mb * 1024 * 1024,
            expected_sha256=source.expected_sha256,
            local_path=local,
        )
        return self.acquired

    def _store_raw(
        self,
        acquired: AcquiredFile,
        schema: dict[str, Any],
        num_rows: int,
        created_by: str | None,
        unavailable: list[str],
    ) -> None:
        raw_key = lake.raw_key(self.taxi_type, self.source.period_start, self.source.file_name)
        self.deps.store.put_immutable(
            raw_key,
            acquired.local_path,
            sha256=acquired.sha256,
            metadata={"source-uri": self.source.uri, "retrieved-at": acquired.retrieved_at.isoformat()},
        )
        with self.factory() as session, session.begin():
            ds = session.get(DataSource, self.data_source_id)
            run = session.get(ProcessingRun, self.run_id)
            assert ds is not None and run is not None
            ds.file_size_bytes, ds.sha256, ds.raw_object_key = acquired.size_bytes, acquired.sha256, raw_key
            ds.source_schema = {**schema, "num_rows": num_rows, "created_by": created_by}
            ds.schema_version, ds.retrieved_at = self.schema_version, acquired.retrieved_at
            run.schema_version, run.unavailable_fields = self.schema_version, unavailable

    def _publish(
        self,
        result: TransformResult,
        verification: LoadVerification,
        curated: UploadedPrefix,
        quarantine: UploadedPrefix,
    ) -> float:
        """Make the period visible (§7.1 step 12) and record lineage and quality (step 13), atomically."""
        assert self.acquired is not None
        finished = _now()
        duration = round((finished - self.started).total_seconds(), 3)
        with self.factory() as session, session.begin():
            period = session.scalar(
                select(DatasetPeriod).where(
                    DatasetPeriod.dataset_id == self.source.dataset,
                    DatasetPeriod.data_period == self.source.period_start,
                )
            )
            if period is None:
                period = DatasetPeriod(dataset_id=self.source.dataset, data_period=self.source.period_start)
                session.add(period)
            period.run_id, period.row_count = self.run_id, verification.published_rows
            period.min_pickup_date, period.max_pickup_date = (
                verification.min_pickup_date,
                verification.max_pickup_date,
            )
            period.published_at = finished
            session.add(
                _quality_row(
                    self.run_id,
                    result,
                    schema_version=self.schema_version,
                    input_bytes=self.acquired.size_bytes,
                    output_bytes=curated.total_bytes + quarantine.total_bytes,
                    started=self.started,
                    completed=finished,
                    duration=duration,
                    status="completed",
                    error=None,
                )
            )
            run = session.get(ProcessingRun, self.run_id)
            job = session.get(IngestionJob, self.job_id)
            assert run is not None and job is not None
            run.status, run.completed_at, run.duration_seconds = JobStatus.COMPLETED, finished, duration
            run.spark_version, run.clickhouse_rows = result.spark_version, verification.published_rows
            run.curated_prefix, run.quarantine_prefix, run.current_stage = (
                curated.prefix,
                quarantine.prefix,
                None,
            )
            job.status, job.finished_at = JobStatus.COMPLETED, finished
            log.info(
                "period published", extra={"period": self.source.period, "rows": verification.published_rows}
            )
            run.log_events = list(self.logs.entries)
        return duration

    def _record_failure(self, summary: str, *, cancelled: bool) -> None:
        finished = _now()
        duration = round((finished - self.started).total_seconds(), 3)
        status = JobStatus.CANCELLED if cancelled else JobStatus.FAILED
        with self.factory() as session, session.begin():
            run = session.get(ProcessingRun, self.run_id)
            job = session.get(IngestionJob, self.job_id)
            assert run is not None and job is not None
            run.status, run.completed_at, run.error_summary = status, finished, summary
            run.duration_seconds, run.log_events = duration, list(self.logs.entries)
            job.status, job.finished_at, job.error_summary = status, finished, summary
            if (
                self.result is not None
                and self.acquired is not None
                and session.get(DataQualityMetrics, self.run_id) is None
            ):  # quality of a run that did not publish is still worth keeping
                session.add(
                    _quality_row(
                        self.run_id,
                        self.result,
                        schema_version=self.schema_version,
                        input_bytes=self.acquired.size_bytes,
                        output_bytes=None,
                        started=self.started,
                        completed=finished,
                        duration=duration,
                        status=status.value,
                        error=summary,
                    )
                )


def _quality_row(
    run_id: uuid.UUID,
    result: TransformResult,
    *,
    schema_version: str,
    input_bytes: int,
    output_bytes: int | None,
    started: datetime,
    completed: datetime,
    duration: float | None,
    status: str,
    error: str | None,
) -> DataQualityMetrics:
    return DataQualityMetrics(
        run_id=run_id,
        source_file_count=1,
        input_row_count=result.input_rows,
        accepted_row_count=result.accepted_rows,
        quarantined_row_count=result.quarantined_rows,
        duplicate_row_count=result.duplicate_rows,
        missingness_by_column=result.missingness_by_column,
        invalid_timestamp_count=result.invalid_timestamp_rows,
        invalid_distance_count=result.flag_counts["invalid_distance"],
        invalid_duration_count=result.flag_counts["invalid_duration"],
        invalid_amount_count=result.flag_counts["invalid_amount"],
        unmapped_location_count=result.unmapped_location_rows,
        quarantine_reason_counts=result.quarantine_reason_counts,
        flag_counts={**result.flag_counts, "cast_failures": result.cast_failure_counts},
        schema_version=schema_version,
        duration_seconds=duration,
        input_bytes=input_bytes,
        output_bytes=output_bytes,
        started_at=started,
        completed_at=completed,
        status=status,
        error_summary=error,
    )
