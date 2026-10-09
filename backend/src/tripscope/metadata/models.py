"""PostgreSQL metadata schema (spec §8.1). Large datasets never live here — only lineage, jobs and users."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Role(enum.StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="user_role", values_callable=lambda e: [m.value for m in e])
    )
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "nyc-tlc-yellow"
    name: Mapped[str] = mapped_column(String(200))
    taxi_type: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")
    source_attribution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    periods: Mapped[list[DatasetPeriod]] = relationship(
        back_populates="dataset", order_by="DatasetPeriod.data_period"
    )


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_key: Mapped[str] = mapped_column(String(100), unique=True)  # manifest key, e.g. "yellow-2025-01"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    taxi_type: Mapped[str] = mapped_column(String(32))
    data_period: Mapped[date] = mapped_column(Date)  # first day of the source month
    source_uri: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(16))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    raw_object_key: Mapped[str | None] = mapped_column(Text)
    source_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schema_version: Mapped[str | None] = mapped_column(String(64))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    data_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_sources.id"), index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e])
    )
    trigger: Mapped[str] = mapped_column(String(32), default="cli")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)

    data_source: Mapped[DataSource] = relationship()
    runs: Mapped[list[ProcessingRun]] = relationship(back_populates="job")

    __table_args__ = (Index("ix_ingestion_jobs_created_at", "created_at"),)


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)  # run_id
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_jobs.id"), index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e], create_type=False)
    )
    current_stage: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    stage_timings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    schema_version: Mapped[str | None] = mapped_column(String(64))
    unavailable_fields: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    quality_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    spark_version: Mapped[str | None] = mapped_column(String(32))
    curated_prefix: Mapped[str | None] = mapped_column(Text)
    quarantine_prefix: Mapped[str | None] = mapped_column(Text)
    clickhouse_rows: Mapped[int | None] = mapped_column(BigInteger)
    error_summary: Mapped[str | None] = mapped_column(Text)
    log_events: Mapped[list[Any]] = mapped_column(JSONB, default=list)

    job: Mapped[IngestionJob] = relationship(back_populates="runs")
    quality: Mapped[DataQualityMetrics | None] = relationship(back_populates="run")


class DataQualityMetrics(Base):
    """One row per processing run; column names follow spec §7.3."""

    __tablename__ = "data_quality_metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_runs.id"), primary_key=True)
    source_file_count: Mapped[int] = mapped_column(Integer)
    input_row_count: Mapped[int] = mapped_column(BigInteger)
    accepted_row_count: Mapped[int] = mapped_column(BigInteger)
    quarantined_row_count: Mapped[int] = mapped_column(BigInteger)
    duplicate_row_count: Mapped[int] = mapped_column(BigInteger)
    missingness_by_column: Mapped[dict[str, Any]] = mapped_column(JSONB)
    invalid_timestamp_count: Mapped[int] = mapped_column(BigInteger)
    invalid_distance_count: Mapped[int] = mapped_column(BigInteger)
    invalid_duration_count: Mapped[int] = mapped_column(BigInteger)
    invalid_amount_count: Mapped[int] = mapped_column(BigInteger)
    unmapped_location_count: Mapped[int] = mapped_column(BigInteger)
    quarantine_reason_counts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    flag_counts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_version: Mapped[str] = mapped_column(String(64))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    input_bytes: Mapped[int] = mapped_column(BigInteger)
    output_bytes: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    error_summary: Mapped[str | None] = mapped_column(Text)

    run: Mapped[ProcessingRun] = relationship(back_populates="quality")


class DatasetPeriod(Base):
    """A month of a dataset that passed verification and is visible to the dashboard (§7.1 step 12)."""

    __tablename__ = "dataset_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    data_period: Mapped[date] = mapped_column(Date)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_runs.id"), index=True)
    row_count: Mapped[int] = mapped_column(BigInteger)
    min_pickup_date: Mapped[date] = mapped_column(Date)
    max_pickup_date: Mapped[date] = mapped_column(Date)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dataset: Mapped[Dataset] = relationship(back_populates="periods")

    __table_args__ = (UniqueConstraint("dataset_id", "data_period", name="uq_dataset_period"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(16))
    request_id: Mapped[str | None] = mapped_column(String(64))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_action", "action"),
    )
