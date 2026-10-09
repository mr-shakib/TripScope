"""Data explorer (FR-08): field catalogue, bounded row preview, and bounded CSV extracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from tripscope.analytics.catalogue import field_catalogue
from tripscope.analytics.export import csv_stream
from tripscope.analytics.filters import ExplorerQuery, ExportRequest
from tripscope.analytics.query_builder import EXPLORER_COLUMNS, SORTABLE
from tripscope.api.security import AuthenticatedUser
from tripscope.metadata.audit import record_audit

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


@router.post("/exports")
def export(body: ExportRequest, request: Request, user: AuthenticatedUser) -> StreamingResponse:
    """Stream a bounded CSV extract. Every extract is audited with its filters and row counts."""
    max_rows = request.app.state.settings.max_export_rows
    total, columns, stream = request.app.state.analytics.extract(body, max_rows=max_rows)
    exported = min(total, body.max_rows or max_rows, max_rows)
    with request.app.state.session_factory() as session, session.begin():
        record_audit(
            session,
            action="export.csv",
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
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        csv_stream(columns, stream),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="tripscope-trips-{stamp}.csv"',
            "X-Total-Rows": str(total),
            "X-Exported-Rows": str(exported),
            "X-Truncated": "true" if total > exported else "false",
        },
    )
