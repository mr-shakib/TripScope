"""Report definitions, permissions and the report-run queue (FR-09, ADR-19).

Permissions: administrators see and manage every report. Analysts create reports, see their own and shared
ones, edit their own, and generate files for any report they can see. Viewers see shared reports and download
their files. A report the caller may not see is reported as not found, so private reports are not disclosed.

Runs: queued → running → completed | failed. A partial unique index allows one queued-or-running run per
report and format. The run snapshots the definition, so editing a report never changes a file in progress.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import ColumnElement, or_, select, text, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tripscope.analytics.filters import AnalyticsFilters
from tripscope.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationFailedError
from tripscope.metadata.models import JobStatus, Report, ReportFormat, ReportRun, ReportVisibility, Role
from tripscope.reports import formatting as f
from tripscope.reports.templates import TEMPLATES, TemplateSpec

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


class Actor(Protocol):
    """The signed-in user as the API sees it (api.security.Actor satisfies this)."""

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def role(self) -> Role: ...


def _now() -> datetime:
    return datetime.now(UTC)


# ---- permissions -----------------------------------------------------------------------------------------


def can_view(report: Report, user: Actor) -> bool:
    return (
        user.role == Role.ADMIN
        or report.created_by == user.id
        or report.visibility == ReportVisibility.SHARED
    )


def can_edit(report: Report, user: Actor) -> bool:
    return user.role == Role.ADMIN or (user.role == Role.ANALYST and report.created_by == user.id)


def can_generate(report: Report, user: Actor) -> bool:
    return user.role in (Role.ADMIN, Role.ANALYST) and can_view(report, user)


def visible_to(user: Actor) -> ColumnElement[bool]:
    if user.role == Role.ADMIN:
        return true()
    return or_(Report.created_by == user.id, Report.visibility == ReportVisibility.SHARED)


def get_report(session: Session, report_id: uuid.UUID, user: Actor, *, with_runs: bool = False) -> Report:
    stmt = select(Report).where(Report.id == report_id).options(selectinload(Report.creator))
    if with_runs:
        stmt = stmt.options(selectinload(Report.runs).selectinload(ReportRun.requester))
    report = session.scalar(stmt)
    if report is None or not can_view(report, user):
        raise NotFoundError("report not found")
    return report


def require(allowed: bool, action: str) -> None:
    if not allowed:
        raise PermissionDeniedError(f"your role does not permit you to {action} this report")


# ---- definitions -----------------------------------------------------------------------------------------


def template_spec(template: str) -> TemplateSpec:
    spec = TEMPLATES.get(template)
    if spec is None:
        raise ValidationFailedError(f"unknown report template {template!r}")
    return spec


def normalize_sections(spec: TemplateSpec, sections: list[str] | None) -> list[str]:
    """Known sections in template order; None means every section."""
    if sections is None:
        return spec.section_ids()
    unknown = sorted(set(sections) - set(spec.section_ids()))
    if unknown:
        raise ValidationFailedError(
            f"unknown section(s) for {spec.title}: {', '.join(unknown)}",
            details={"allowed": spec.section_ids()},
        )
    return [s for s in spec.section_ids() if s in set(sections)]


def default_title(spec: TemplateSpec, filters: AnalyticsFilters) -> str:
    if filters.start_date and filters.end_date:
        return f"{spec.title} · {f.date_range(filters.start_date, filters.end_date)}"
    if filters.start_date:
        return f"{spec.title} · from {f.fmt_date(filters.start_date)}"
    if filters.end_date:
        return f"{spec.title} · to {f.fmt_date(filters.end_date)}"
    return f"{spec.title} · all published data"


def create_report(
    session: Session,
    *,
    template: str,
    title: str | None,
    filters: AnalyticsFilters,
    sections: list[str] | None,
    visibility: str,
    user: Actor,
) -> Report:
    spec = template_spec(template)
    report = Report(
        template=spec.id,
        title=(title or "").strip() or default_title(spec, filters),
        dataset_id=filters.dataset_id,
        filters=filters.applied(),
        sections=normalize_sections(spec, sections),
        visibility=ReportVisibility(visibility),
        created_by=user.id,
    )
    session.add(report)
    session.flush()
    return report


def update_report(
    report: Report,
    *,
    title: str | None = None,
    filters: AnalyticsFilters | None = None,
    sections: list[str] | None = None,
    visibility: str | None = None,
) -> list[str]:
    """Apply the given changes; returns the names of the fields that changed."""
    changed = []
    spec = template_spec(report.template)
    if title is not None and title.strip() and title.strip() != report.title:
        report.title = title.strip()
        changed.append("title")
    if filters is not None and filters.applied() != report.filters:
        report.filters, report.dataset_id = filters.applied(), filters.dataset_id
        changed.append("filters")
    if sections is not None and normalize_sections(spec, sections) != report.sections:
        report.sections = normalize_sections(spec, sections)
        changed.append("sections")
    if visibility is not None and ReportVisibility(visibility) != report.visibility:
        report.visibility = ReportVisibility(visibility)
        changed.append("visibility")
    return changed


def report_filters(report: Report | dict[str, Any]) -> AnalyticsFilters:
    raw = report.filters if isinstance(report, Report) else report["filters"]
    return AnalyticsFilters.model_validate(raw)


# ---- runs ------------------------------------------------------------------------------------------------


def enqueue_run(session: Session, report: Report, fmt: str, user: Actor) -> ReportRun:
    report_format = ReportFormat(fmt)
    active = session.scalar(
        select(ReportRun).where(
            ReportRun.report_id == report.id, ReportRun.format == report_format, ReportRun.status.in_(ACTIVE)
        )
    )
    if active is not None:
        raise ConflictError(
            f"a {report_format.value.upper()} for this report is already {active.status.value}",
            details={"run_id": str(active.id), "status": active.status.value},
        )
    run = ReportRun(
        report_id=report.id,
        format=report_format,
        status=JobStatus.QUEUED,
        requested_by=user.id,
        definition={
            "template": report.template,
            "title": report.title,
            "filters": report.filters,
            "sections": report.sections,
            "narrative": report.narrative,  # template 6: the AI text as it was when the file was requested
        },
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError as exc:  # lost a race with an identical request
        raise ConflictError(f"a {report_format.value.upper()} for this report is already queued") from exc
    return run


def claim_next(session: Session, worker_id: str) -> uuid.UUID | None:
    """Atomically move the oldest queued run to running for this worker (safe with several workers)."""
    row = session.execute(
        text(
            """
            UPDATE report_runs
            SET status = 'running', worker_id = :worker, started_at = now(), heartbeat_at = now()
            WHERE id = (
                SELECT id FROM report_runs WHERE status = 'queued'
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING id
            """
        ),
        {"worker": worker_id},
    ).first()
    return row[0] if row else None


def heartbeat(session: Session, run_id: uuid.UUID) -> None:
    session.execute(update(ReportRun).where(ReportRun.id == run_id).values(heartbeat_at=_now()))


def fail_stale(session: Session, *, stale_after: timedelta) -> int:
    """Fail running runs whose worker stopped heartbeating (crash or restart); they can be generated again."""
    runs = session.scalars(
        select(ReportRun).where(
            ReportRun.status == JobStatus.RUNNING, ReportRun.heartbeat_at < _now() - stale_after
        )
    ).all()
    for run in runs:
        run.status, run.finished_at = JobStatus.FAILED, _now()
        run.error_summary = (
            f"report worker {run.worker_id or 'unknown'} stopped responding; generate it again"
        )
    return len(runs)


def complete_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    object_key: str,
    file_name: str,
    size_bytes: int,
    sha256: str,
    dataset_version: dict[str, Any],
    page_count: int | None,
) -> None:
    run = session.get(ReportRun, run_id)
    if run is None:
        raise NotFoundError("report run not found")
    run.status, run.finished_at = JobStatus.COMPLETED, _now()
    run.object_key, run.file_name, run.size_bytes, run.sha256 = object_key, file_name, size_bytes, sha256
    run.dataset_version, run.page_count = dataset_version, page_count


def fail_run(session: Session, run_id: uuid.UUID, message: str) -> None:
    run = session.get(ReportRun, run_id)
    if run is not None:
        run.status, run.finished_at, run.error_summary = JobStatus.FAILED, _now(), message[:2000]


def file_stem(definition: dict[str, Any], start: date, end: date) -> str:
    return f"tripscope-{definition['template'].replace('_', '-')}-{start:%Y%m%d}-{end:%Y%m%d}"
