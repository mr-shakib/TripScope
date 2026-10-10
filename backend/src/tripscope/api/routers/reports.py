"""Report center (FR-09): templates, saved reports, live previews, background file generation and downloads.

Analysts and administrators create reports and generate files; everyone signed in can open shared reports and
download their files (spec §3.1: viewers "download permitted exports"). Report creation, changes, generation
requests, deletions and every download are audited.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tripscope.analytics.filters import AnalyticsFilters
from tripscope.api.security import AuthenticatedUser, CurrentUser, require_roles
from tripscope.core.errors import ConflictError, NotFoundError
from tripscope.metadata.audit import record_audit
from tripscope.metadata.models import JobStatus, Report, ReportFormat, ReportRun, Role, User
from tripscope.reports import service
from tripscope.reports.builder import ReportBuilder
from tripscope.reports.templates import TEMPLATES
from tripscope.reports.worker import CONTENT_TYPES

log = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])

Author = Annotated[CurrentUser, Depends(require_roles(Role.ADMIN, Role.ANALYST))]
TemplateId = Literal[
    "executive_overview", "demand_patterns", "fares_distance", "zone_analysis", "data_quality"
]
SectionId = Annotated[str, Field(pattern=r"^[a-z_]{1,40}$")]
FormatId = Literal["pdf", "xlsx", "csv"]
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: TemplateId
    title: str | None = Field(default=None, max_length=200)
    filters: AnalyticsFilters = Field(default_factory=AnalyticsFilters)
    sections: list[SectionId] | None = Field(default=None, max_length=20)
    visibility: Literal["private", "shared"] = "private"


class ReportUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    filters: AnalyticsFilters | None = None
    sections: list[SectionId] | None = Field(default=None, min_length=1, max_length=20)
    visibility: Literal["private", "shared"] | None = None


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: FormatId


class ReportListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["all", "mine", "shared"] = "all"
    limit: int = Field(default=100, ge=1, le=200)


class DownloadQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: uuid.UUID | None = None
    format: FormatId | None = None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _person(user: User | None) -> dict[str, Any] | None:
    return {"id": str(user.id), "name": user.display_name} if user else None


def _run_dict(run: ReportRun) -> dict[str, Any]:
    duration = (
        (run.finished_at - run.started_at).total_seconds() if run.finished_at and run.started_at else None
    )
    version = run.dataset_version or {}
    return {
        "run_id": str(run.id),
        "format": run.format.value,
        "status": run.status.value,
        "requested_by": _person(run.requester),
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": duration,
        "error_summary": run.error_summary,
        "file_name": run.file_name,
        "size_bytes": run.size_bytes,
        "sha256": run.sha256,
        "page_count": run.page_count,
        "dataset_version_id": version.get("version_id"),
        "title": run.definition.get("title"),
    }


def _report_dict(report: Report, user: CurrentUser, *, with_runs: bool) -> dict[str, Any]:
    spec = TEMPLATES[report.template]
    latest: dict[str, Any] = {}
    for run in report.runs:  # newest first
        latest.setdefault(run.format.value, _run_dict(run))
    data: dict[str, Any] = {
        "report_id": str(report.id),
        "template": report.template,
        "template_title": spec.title,
        "title": report.title,
        "dataset_id": report.dataset_id,
        "filters": report.filters,
        "sections": report.sections,
        "visibility": report.visibility.value,
        "created_by": _person(report.creator),
        "created_at": report.created_at,
        "updated_at": report.updated_at,
        "permissions": {
            "edit": service.can_edit(report, user),
            "generate": service.can_generate(report, user),
        },
        "latest_runs": latest,
    }
    if with_runs:
        data["runs"] = [_run_dict(run) for run in report.runs[:100]]
    return data


def _audit(request: Request, user: CurrentUser, action: str, report_id: uuid.UUID, **details: Any) -> None:
    with request.app.state.session_factory() as session, session.begin():
        record_audit(
            session,
            action=action,
            outcome="success",
            actor_user_id=user.id,
            actor_label=user.email,
            target_type="report",
            target_id=str(report_id),
            client_ip=_client_ip(request),
            details=details,
        )


@router.get("/templates")
def templates(user: AuthenticatedUser) -> dict[str, Any]:
    return {"templates": [t.public() for t in TEMPLATES.values()], "formats": [f.value for f in ReportFormat]}


@router.get("")
def list_reports(
    request: Request, user: AuthenticatedUser, query: Annotated[ReportListQuery, Query()]
) -> dict[str, Any]:
    stmt = (
        select(Report)
        .where(service.visible_to(user))
        .options(selectinload(Report.creator), selectinload(Report.runs).selectinload(ReportRun.requester))
        .order_by(Report.updated_at.desc())
        .limit(query.limit)
    )
    if query.scope == "mine":
        stmt = stmt.where(Report.created_by == user.id)
    elif query.scope == "shared":
        stmt = stmt.where(Report.visibility == "shared")
    with request.app.state.session_factory() as session:
        return {"reports": [_report_dict(r, user, with_runs=False) for r in session.scalars(stmt).all()]}


@router.post("", status_code=201)
def create_report(body: ReportCreate, request: Request, user: Author) -> dict[str, Any]:
    with request.app.state.session_factory() as session, session.begin():
        report = service.create_report(
            session,
            template=body.template,
            title=body.title,
            filters=body.filters,
            sections=body.sections,
            visibility=body.visibility,
            user=user,
        )
        record_audit(
            session,
            action="report.created",
            outcome="success",
            actor_user_id=user.id,
            actor_label=user.email,
            target_type="report",
            target_id=str(report.id),
            client_ip=_client_ip(request),
            details={"template": report.template, "visibility": report.visibility.value},
        )
        report_id = report.id
    return get_report(report_id, request, user)


@router.get("/{report_id}")
def get_report(report_id: uuid.UUID, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    with request.app.state.session_factory() as session:
        return _report_dict(
            service.get_report(session, report_id, user, with_runs=True), user, with_runs=True
        )


@router.patch("/{report_id}")
def update_report(report_id: uuid.UUID, body: ReportUpdate, request: Request, user: Author) -> dict[str, Any]:
    with request.app.state.session_factory() as session, session.begin():
        report = service.get_report(session, report_id, user)
        service.require(service.can_edit(report, user), "edit")
        changed = service.update_report(
            report, title=body.title, filters=body.filters, sections=body.sections, visibility=body.visibility
        )
    if changed:
        _audit(request, user, "report.updated", report_id, fields=changed)
    return get_report(report_id, request, user)


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: uuid.UUID, request: Request, user: Author) -> Response:
    with request.app.state.session_factory() as session, session.begin():
        report = service.get_report(session, report_id, user, with_runs=True)
        service.require(service.can_edit(report, user), "delete")
        if any(run.status in service.ACTIVE for run in report.runs):
            raise ConflictError("files for this report are still being generated; delete it when they finish")
        title = report.title
        session.delete(report)
    removed = request.app.state.object_store().delete_prefix(f"reports/{report_id}/")
    _audit(request, user, "report.deleted", report_id, title=title, objects_removed=removed)
    return Response(status_code=204)


@router.get("/{report_id}/preview")
def preview(report_id: uuid.UUID, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    """The report document built now from published data: what a generated file would contain."""
    with request.app.state.session_factory() as session:
        report = service.get_report(session, report_id, user)
        template, title, sections = report.template, report.title, list(report.sections)
        filters, prepared_by = service.report_filters(report), report.creator.display_name
    doc = ReportBuilder(request.app.state.analytics).build(
        template=template, title=title, filters=filters, sections=sections, prepared_by=prepared_by
    )
    return doc.model_dump(mode="json")


@router.post("/{report_id}/generate", status_code=202)
def generate(report_id: uuid.UUID, body: GenerateRequest, request: Request, user: Author) -> dict[str, Any]:
    with request.app.state.session_factory() as session, session.begin():
        report = service.get_report(session, report_id, user)
        service.require(service.can_generate(report, user), "generate files for")
        run = service.enqueue_run(session, report, body.format, user)
        session.flush()
        record_audit(
            session,
            action="report.generate_requested",
            outcome="success",
            actor_user_id=user.id,
            actor_label=user.email,
            target_type="report",
            target_id=str(report.id),
            client_ip=_client_ip(request),
            details={"run_id": str(run.id), "format": body.format, "filters": report.filters},
        )
        run_id = run.id
    with request.app.state.session_factory() as session:
        loaded = session.scalar(
            select(ReportRun).where(ReportRun.id == run_id).options(selectinload(ReportRun.requester))
        )
        assert loaded is not None
        return _run_dict(loaded)


@router.get("/{report_id}/download")
def download(
    report_id: uuid.UUID, request: Request, user: AuthenticatedUser, query: Annotated[DownloadQuery, Query()]
) -> Response:
    """The file of a completed run (`run_id`), or the newest completed file (optionally of one `format`)."""
    with request.app.state.session_factory() as session:
        report = service.get_report(session, report_id, user, with_runs=True)
        runs = report.runs
        if query.run_id:
            runs = [r for r in runs if r.id == query.run_id]
        if query.format:
            runs = [r for r in runs if r.format.value == query.format]
        if query.run_id and runs and runs[0].status != JobStatus.COMPLETED:
            raise ConflictError(f"this file is not ready (status: {runs[0].status.value})")
        run = next((r for r in runs if r.status == JobStatus.COMPLETED), None)
        if run is None or not run.object_key or not run.file_name:
            raise NotFoundError("no completed file for this report; generate one first")
        key, file_name, fmt, expected = run.object_key, run.file_name, run.format, run.sha256
        run_id = run.id
    data = request.app.state.object_store().get_bytes(key, max_bytes=MAX_DOWNLOAD_BYTES)
    if data is None:
        raise NotFoundError("the stored file is missing; generate the report again")
    if expected and hashlib.sha256(data).hexdigest() != expected:
        log.error("report file failed its integrity check", extra={"run_id": str(run_id)})
        raise ConflictError("the stored file failed its integrity check; generate the report again")
    _audit(
        request, user, "report.downloaded", report_id, run_id=str(run_id), format=fmt.value, bytes=len(data)
    )
    return Response(
        data,
        media_type=CONTENT_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "X-Report-SHA256": expected or "",
        },
    )
