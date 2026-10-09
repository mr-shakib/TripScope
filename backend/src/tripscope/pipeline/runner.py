"""End-to-end orchestration for one manifest source (spec §7.1 steps 1-13).

discover → acquire → inspect → raw upload → zones → Spark transform → curated/quarantine upload →
ClickHouse stage/verify/replace → publish period → record metrics. Every stage is timed and recorded on the
processing run; failures mark the job failed with a redacted, actionable summary.
"""

from __future__ import annotations

import logging
import shutil
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
    Dataset,
    DatasetPeriod,
    DataSource,
    IngestionJob,
    JobStatus,
    ProcessingRun,
)
from tripscope.pipeline import lake
from tripscope.pipeline.acquire import AcquiredFile, acquire, inspect_parquet
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

    def __init__(self, factory: sessionmaker[Session], run_id: uuid.UUID) -> None:
        self._factory = factory
        self.run_id = run_id
        self.timings: dict[str, float] = {}
        self.events: list[dict[str, Any]] = []

    def _update(self, **fields: Any) -> None:
        with self._factory() as session, session.begin():
            run = session.get(ProcessingRun, self.run_id)
            assert run is not None
            for key, value in fields.items():
                setattr(run, key, value)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        self._update(current_stage=name)
        started = time.perf_counter()
        status = "failed"
        try:
            yield
            status = "completed"
        finally:
            seconds = round(time.perf_counter() - started, 3)
            self.timings[name] = seconds
            self.events.append(
                {"stage": name, "status": status, "seconds": seconds, "at": _now().isoformat()}
            )
            self._update(stage_timings=dict(self.timings), log_events=list(self.events))
            log.info("pipeline stage", extra={"stage": name, "status": status, "seconds": seconds})


def _now() -> datetime:
    return datetime.now(UTC)


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


def _register(session: Session, manifest: Manifest, source: SourceSpec) -> DataSource:
    spec = manifest.datasets[source.dataset]
    dataset = session.get(Dataset, source.dataset)
    if dataset is None:
        dataset = Dataset(id=source.dataset)
        session.add(dataset)
    dataset.name, dataset.taxi_type = spec.name, spec.taxi_type
    dataset.description, dataset.source_attribution = spec.description, spec.source_attribution

    data_source = session.scalar(select(DataSource).where(DataSource.source_key == source.key))
    if data_source is None:
        data_source = DataSource(source_key=source.key)
        session.add(data_source)
    data_source.dataset_id = source.dataset
    data_source.taxi_type = spec.taxi_type
    data_source.data_period = source.period_start
    data_source.source_uri = source.uri
    data_source.file_name = source.file_name
    data_source.file_format = source.format
    session.flush()
    return data_source


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


def run_source(
    deps: PipelineDeps, source_key: str, *, trigger: str = "cli", requested_by: uuid.UUID | None = None
) -> RunSummary:
    source = deps.manifest.get_source(source_key)
    with _source_lock(deps.engine, source.key):
        return _SourceRun(deps, source, trigger=trigger, requested_by=requested_by).execute()


class _SourceRun:
    """State for a single run of one source; each method is one pipeline stage."""

    def __init__(
        self, deps: PipelineDeps, source: SourceSpec, *, trigger: str, requested_by: uuid.UUID | None
    ) -> None:
        self.deps, self.source = deps, source
        self.factory = deps.session_factory
        self.taxi_type = deps.manifest.datasets[source.dataset].taxi_type
        self.work = deps.settings.pipeline_work_dir
        self.started = _now()
        self.acquired: AcquiredFile | None = None
        self.result: TransformResult | None = None
        self.schema_version = ""
        with self.factory() as session, session.begin():
            data_source = _register(session, deps.manifest, source)
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
            run = ProcessingRun(
                job_id=job.id,
                status=JobStatus.RUNNING,
                started_at=self.started,
                quality_rules=deps.rules.model_dump(),
            )
            session.add(run)
            session.flush()
            self.job_id, self.run_id, self.data_source_id = job.id, run.id, data_source.id
        self.recorder = _RunRecorder(self.factory, self.run_id)
        self.run_dir = self.work / "runs" / str(self.run_id)

    def execute(self) -> RunSummary:
        try:
            with self.recorder.stage("acquire"):
                acquired = self._acquire()
            with self.recorder.stage("inspect"):
                inspection = inspect_parquet(acquired.local_path)
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
                zones, zones_sha = _acquire_reference_zones(self.deps, self.work)
                load_zones(
                    self.deps.clickhouse,
                    database=self.deps.clickhouse_database,
                    zones=zones,
                    source_sha256=zones_sha,
                )
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
                )
            with self.recorder.stage("publish"):
                duration = self._publish(result, verification, curated, quarantine)
        except Exception as exc:
            summary = redact(f"{type(exc).__name__}: {exc}", self.deps.settings.secret_values())[:2000]
            self._record_failure(summary)
            log.error("pipeline run failed", extra={"run_id": str(self.run_id), "error": summary})
            raise PipelineError(f"run {self.run_id} failed: {summary}") from exc
        finally:
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
        return duration

    def _record_failure(self, summary: str) -> None:
        finished = _now()
        duration = round((finished - self.started).total_seconds(), 3)
        with self.factory() as session, session.begin():
            run = session.get(ProcessingRun, self.run_id)
            job = session.get(IngestionJob, self.job_id)
            assert run is not None and job is not None
            run.status, run.completed_at, run.error_summary = JobStatus.FAILED, finished, summary
            run.duration_seconds = duration
            job.status, job.finished_at, job.error_summary = JobStatus.FAILED, finished, summary
            if (
                self.result is not None
                and self.acquired is not None
                and session.get(DataQualityMetrics, self.run_id) is None
            ):
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
                        status="failed",
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
