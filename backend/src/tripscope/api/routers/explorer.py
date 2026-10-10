"""Data explorer (FR-08): field catalogue, bounded row preview, and bounded CSV/XLSX extracts."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from tripscope.analytics.catalogue import field_catalogue
from tripscope.analytics.export import csv_stream
from tripscope.analytics.filters import ExplorerQuery, ExportRequest
from tripscope.analytics.query_builder import EXPLORER_COLUMNS, SORTABLE
from tripscope.api.security import AuthenticatedUser
from tripscope.metadata.audit import record_audit
from tripscope.reports.render_xlsx import write_extract_xlsx

router = APIRouter(tags=["explorer"])


@router.get("/explorer/fields")
def fields(request: Request, user: AuthenticatedUser, dataset_id: str = "nyc-tlc-yellow") -> dict[str, Any]:
    report = request.app.state.analytics.schema(dataset_id)
    availability = {f["name"]: f["source_column_by_period"] for f in report["fields"]}
    return {
        "fields": field_catalogue(list(EXPLORER_COLUMNS), availability),
        "sortable": list(SORTABLE),
        "periods": [s["period"] for s in report["sources"]],
        "max_export_rows": request.app.state.settings.max_export_rows,
    }


@router.get("/explorer/rows")
def rows(
    request: Request, user: AuthenticatedUser, query: Annotated[ExplorerQuery, Query()]
) -> dict[str, Any]:
    return dict(request.app.state.analytics.rows(query))


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/exports")
def export(body: ExportRequest, request: Request, user: AuthenticatedUser) -> Response:
    """A bounded CSV (streamed) or XLSX extract. Every extract is audited with its filters and row counts."""
    analytics = request.app.state.analytics
    max_rows = request.app.state.settings.max_export_rows
    total, columns, stream = analytics.extract(body, max_rows=max_rows)
    exported = min(total, body.max_rows or max_rows, max_rows)
    with request.app.state.session_factory() as session, session.begin():
        record_audit(
            session,
            action=f"export.{body.format}",
            outcome="success",
            actor_user_id=user.id,
            actor_label=user.email,
            target_type="dataset",
            target_id=body.filters.dataset_id,
            client_ip=request.client.host if request.client else None,
            details={
                "filters": body.filters.applied(),
                "scope": body.scope.model_dump(),
                "matching_rows": total,
                "exported_rows": exported,
            },
        )
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    headers = {
        "X-Total-Rows": str(total),
        "X-Exported-Rows": str(exported),
        "X-Truncated": "true" if total > exported else "false",
    }
    if body.format == "csv":
        return StreamingResponse(
            csv_stream(columns, stream),
            media_type="text/csv; charset=utf-8",
            headers=headers | {"Content-Disposition": f'attachment; filename="tripscope-trips-{stamp}.csv"'},
        )
    about = [
        ("Generated", f"{now:%Y-%m-%d %H:%M} UTC by {user.email}"),
        ("Filters", json.dumps(body.filters.applied(), default=str)),
        (
            "Rows",
            f"quality scope {body.scope.quality}; sorted by {body.scope.sort} {body.scope.order}"
            + (f"; flag {body.scope.flag}" if body.scope.flag else ""),
        ),
        ("Matching rows", f"{total:,}"),
        ("Rows in this file", f"{exported:,}"),
        ("Truncated", "yes: narrow the filters to get every row" if total > exported else "no"),
        ("Time zone", "Pickup and drop-off times are NYC local wall-clock time as recorded by TLC."),
        ("Source", analytics.coverage(body.filters.dataset_id).source_attribution),
    ]
    # Built in a private temporary directory (XLSX cannot be streamed); removed after the response is sent.
    workdir = Path(tempfile.mkdtemp(prefix="tripscope-export-"))
    try:
        write_extract_xlsx(workdir / "extract.xlsx", columns, stream, about=about)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    return FileResponse(
        workdir / "extract.xlsx",
        media_type=XLSX_TYPE,
        filename=f"tripscope-trips-{stamp}.xlsx",
        headers=headers,
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )
