"""XLSX output (FR-09): report workbooks and direct trip extracts.

Formula-injection safety: the workbook is opened with `strings_to_formulas`, `strings_to_urls` and
`strings_to_numbers` off, and every text value goes through `write_string`, so text such as "=HYPERLINK(...)"
is stored as a plain string cell that spreadsheet software never evaluates. Numbers and dates are written as
typed values with number formats, so they stay usable for analysis.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter
from xlsxwriter.workbook import Workbook
from xlsxwriter.worksheet import Worksheet

from tripscope.reports import formatting as f
from tripscope.reports.document import ChartBlock, Column, ReportDocument, TableBlock, narrative_note

SAFE_OPTIONS: dict[str, Any] = {
    "strings_to_formulas": False,
    "strings_to_urls": False,
    "strings_to_numbers": False,
    "remove_timezone": True,  # timestamps are stored as UTC; Excel has no time zones
}

NUMBER_FORMATS: dict[str, str] = {
    "trips": "#,##0",
    "rows": "#,##0",
    "count": "#,##0",
    "bytes": "#,##0",
    "usd": '"$"#,##0.00',
    "miles": '0.00" mi"',
    "minutes": '0.0" min"',
    "seconds": '0.0" s"',
    "share": "0.00%",
    "date": "yyyy-mm-dd",
    "text": "@",
}
SERIES_COLORS = ["#2A78D6", "#EB6834", "#1BAF7A", "#7C5CC4"]
_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


class _Formats:
    def __init__(self, book: Workbook) -> None:
        self.book = book
        self.title = book.add_format({"bold": True, "font_size": 16})
        self.subtitle = book.add_format({"font_color": "#475569", "font_size": 11})
        self.label = book.add_format({"font_color": "#64748B", "valign": "top"})
        self.wrap = book.add_format({"text_wrap": True, "valign": "top"})
        self.heading = book.add_format({"bold": True, "font_size": 12})
        self.header = book.add_format(
            {"bold": True, "bg_color": "#F8F9FB", "bottom": 1, "border_color": "#CBD2DC", "valign": "bottom"}
        )
        self.header_num = book.add_format(
            {"bold": True, "bg_color": "#F8F9FB", "bottom": 1, "border_color": "#CBD2DC", "align": "right"}
        )
        self.note = book.add_format({"italic": True, "font_color": "#64748B"})
        self.datetime = book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
        self._units: dict[str, Any] = {}

    def unit(self, unit: str) -> Any:
        if unit not in self._units:
            self._units[unit] = self.book.add_format({"num_format": NUMBER_FORMATS.get(unit, "General")})
        return self._units[unit]


def write_value(sheet: Worksheet, row: int, col: int, value: Any, fmt: Any = None) -> None:
    """Typed write: text is always a string cell (never a formula, URL or number)."""
    if value is None:
        sheet.write_blank(row, col, None, fmt)
    elif isinstance(value, bool):
        sheet.write_boolean(row, col, value, fmt)
    elif isinstance(value, int | float | Decimal):
        sheet.write_number(row, col, float(value), fmt)
    elif isinstance(value, datetime | date):
        sheet.write_datetime(row, col, value, fmt)
    elif isinstance(value, list | tuple):
        sheet.write_string(row, col, ";".join(str(v) for v in value), fmt)
    else:
        sheet.write_string(row, col, str(value), fmt)


def sheet_name(title: str, used: set[str]) -> str:
    """A valid, unique sheet name (≤ 31 characters, cut at a word boundary)."""
    base = _INVALID_SHEET_CHARS.sub(" ", title).strip().strip("'") or "Sheet"
    if len(base) > 31:
        base = base[:30].rsplit(" ", 1)[0].rstrip(" -–,") + "…"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name, n = base[: 31 - len(suffix)] + suffix, n + 1
    used.add(name.lower())
    return name


def _table_sheet(book: Workbook, fx: _Formats, name: str, block: TableBlock) -> None:
    sheet = book.add_worksheet(name)
    numeric = [c.unit in f.NUMERIC_UNITS for c in block.columns]
    for col, column in enumerate(block.columns):
        sheet.write_string(0, col, column.label, fx.header_num if numeric[col] else fx.header)
        width = max(len(column.label), *(len(r[col]) for r in block.display), 6) + 2
        sheet.set_column(col, col, min(width, 60))
    for r, row in enumerate(block.rows, start=1):
        for col, (value, column) in enumerate(zip(row, block.columns, strict=True)):
            write_value(sheet, r, col, value, fx.unit(column.unit) if numeric[col] else None)
    sheet.freeze_panes(1, 0)
    if block.rows:
        sheet.autofilter(0, 0, len(block.rows), len(block.columns) - 1)
    if block.note:
        sheet.write_string(len(block.rows) + 2, 0, block.note, fx.note)


def _heatmap_sheet(book: Workbook, fx: _Formats, name: str, block: ChartBlock) -> None:
    sheet = book.add_worksheet(name)
    value_format = fx.unit(block.unit)
    sheet.write_string(0, 0, "Weekday \\ hour", fx.header)
    for c, label in enumerate(block.categories, start=1):
        sheet.write_string(0, c, label, fx.header_num)
    for r, label in enumerate(block.rows, start=1):
        sheet.write_string(r, 0, label, fx.header)
        for c, value in enumerate(block.matrix[r - 1], start=1):
            write_value(sheet, r, c, value, value_format)
    sheet.set_column(0, 0, 16)
    sheet.set_column(1, len(block.categories), 9)
    sheet.conditional_format(
        1,
        1,
        len(block.rows),
        len(block.categories),
        {"type": "2_color_scale", "min_color": "#E8F1FC", "max_color": "#0D366B"},
    )
    sheet.freeze_panes(1, 1)


def _chart_sheet(book: Workbook, fx: _Formats, name: str, block: ChartBlock) -> None:
    if block.kind == "heatmap":
        _heatmap_sheet(book, fx, name, block)
        return
    sheet = book.add_worksheet(name)
    value_format = fx.unit(block.unit)
    sheet.write_string(0, 0, "Category", fx.header)
    for s, series in enumerate(block.series, start=1):
        sheet.write_string(0, s, series.name, fx.header_num)
    for r, category in enumerate(block.categories, start=1):
        sheet.write_string(r, 0, category)
        for s, series in enumerate(block.series, start=1):
            write_value(
                sheet, r, s, series.values[r - 1] if r - 1 < len(series.values) else None, value_format
            )
    sheet.set_column(0, 0, max((len(c) for c in block.categories), default=10) + 2)
    sheet.set_column(1, len(block.series), 16)
    sheet.freeze_panes(1, 0)
    rows = len(block.categories)
    if block.markers:
        sheet.write_string(rows + 2, 0, "Markers", fx.header)
        for i, marker in enumerate(block.markers, start=1):
            sheet.write_string(rows + 2 + i, 0, f"{marker.label}: {block.categories[marker.index]}")
    if not rows or not block.series:
        return
    chart_type = {"line": "line", "bar": "bar"}.get(block.kind, "column")
    chart = book.add_chart({"type": chart_type})
    for s in range(1, len(block.series) + 1):
        color = SERIES_COLORS[(s - 1) % len(SERIES_COLORS)]
        options: dict[str, Any] = {
            "name": [name, 0, s],
            "categories": [name, 1, 0, rows, 0],
            "values": [name, 1, s, rows, s],
        }
        if chart_type == "line":
            options["line"] = {"color": color, "width": 1.5}
        else:
            options["fill"] = {"color": color}
            options["border"] = {"none": True}
            options["gap"] = 15 if block.kind == "histogram" else 60
        chart.add_series(options)
    chart.set_title({"name": block.title, "name_font": {"size": 11, "bold": True}})
    chart.set_legend({"none": True} if len(block.series) == 1 else {"position": "bottom"})
    chart.set_y_axis(
        {
            "num_format": NUMBER_FORMATS.get(block.unit, "General"),
            "major_gridlines": {"visible": True, "line": {"color": "#ECEEF2"}},
        }
    )
    if chart_type == "bar":
        chart.set_y_axis({"reverse": True})  # categories top to bottom, as in the report
    chart.set_size({"width": 760, "height": 360 if chart_type != "bar" else max(260, 22 * rows)})
    sheet.insert_chart(1, len(block.series) + 2, chart)


def _summary_sheet(book: Workbook, fx: _Formats, doc: ReportDocument, used: set[str]) -> None:
    summary = book.add_worksheet(sheet_name("Summary", used))
    summary.set_column(0, 0, 26)
    summary.set_column(1, 1, 22)
    summary.set_column(2, 5, 18)
    summary.write_string(0, 0, doc.title, fx.title)
    summary.write_string(1, 0, f"{doc.template_title} · {doc.period.label}", fx.subtitle)
    meta: list[tuple[str, str]] = [
        ("Period", doc.period.label),
        ("Period start", doc.period.start.isoformat()),
        ("Period end", doc.period.end.isoformat()),
    ]
    if doc.comparison.available and doc.comparison.label:
        meta.append(("Compared with", doc.comparison.label))
    meta += [(item.label, item.value) for item in doc.filters]
    meta += [
        ("Dataset version", doc.dataset.version_id),
        ("Prepared by", doc.prepared_by),
        ("Generated (UTC)", f.fmt_datetime(doc.generated_at)),
        ("Source", doc.dataset.attribution),
    ]
    row = 3
    for label, value in meta:
        summary.write_string(row, 0, label, fx.label)
        summary.write_string(row, 1, value)
        row += 1
    summary.write_string(row + 1, 0, "Executive summary", fx.heading)
    row += 2
    for line in doc.summary:
        summary.merge_range(row, 0, row, 5, line, fx.wrap)
        summary.set_row(row, 15 * max(1, len(line) // 110 + 1))
        row += 1
    note = narrative_note(doc.narrative)
    if note:
        summary.write_string(row, 0, note, fx.note)
        row += 1
    if doc.recommendations:
        summary.write_string(row + 1, 0, "Suggested next steps (AI-drafted, not findings)", fx.heading)
        row += 2
        for line in doc.recommendations:
            summary.merge_range(row, 0, row, 5, line, fx.wrap)
            row += 1
    if not doc.kpis:
        return
    summary.write_string(row + 1, 0, "Key figures", fx.heading)
    row += 2
    for c, header in enumerate(["Metric", "Value", "Previous period", "Change", "Excluded rows"]):
        summary.write_string(row, c, header, fx.header if c == 0 else fx.header_num)
    for kpi in doc.kpis:
        row += 1
        summary.write_string(row, 0, kpi.label)
        write_value(summary, row, 1, kpi.value, fx.unit(kpi.unit))
        write_value(summary, row, 2, kpi.previous, fx.unit(kpi.unit))
        write_value(summary, row, 3, kpi.change, fx.unit("share"))
        write_value(summary, row, 4, kpi.excluded_rows, fx.unit("rows"))
    if not doc.comparison.available and doc.comparison.reason:
        summary.write_string(row + 1, 0, f"No period comparison: {doc.comparison.reason}", fx.note)


def _appendix_sheets(book: Workbook, fx: _Formats, doc: ReportDocument, used: set[str]) -> None:
    findings = TableBlock.build(
        id="findings",
        title="Findings",
        columns=[
            Column(key="number", label="#", unit="count"),
            Column(key="section", label="Section"),
            Column(key="statement", label="Finding"),
            Column(key="kind", label="Kind"),
            Column(key="source", label="Evidence source"),
            Column(key="table", label="Source table"),
            Column(key="values", label="Values"),
            Column(key="caveat", label="Caveat"),
        ],
        rows=[
            [
                i,
                fd.section,
                fd.statement,
                fd.kind,
                fd.evidence.source,
                fd.evidence.source_table,
                "; ".join(f"{k} = {v}" for k, v in fd.evidence.values.items()),
                fd.caveat,
            ]
            for i, fd in enumerate(doc.findings, start=1)
        ],
    )
    _table_sheet(book, fx, sheet_name("Findings", used), findings)
    version = TableBlock.build(
        id="dataset-version",
        title="Dataset version",
        columns=[
            Column(key="period", label="Month"),
            Column(key="run_id", label="Processing run"),
            Column(key="rows", label="Rows", unit="rows"),
            Column(key="published", label="Published (UTC)"),
        ],
        rows=[[p.period, p.run_id, p.row_count, f.fmt_datetime(p.published_at)] for p in doc.dataset.periods],
        note=f"Dataset version {doc.dataset.version_id}.",
    )
    _table_sheet(book, fx, sheet_name("Dataset version", used), version)
    notes = book.add_worksheet(sheet_name("Methodology", used))
    notes.set_column(0, 0, 120)
    lines = [("Methodology", fx.heading), *((line, fx.wrap) for line in doc.methodology), ("", None)]
    lines += [("Limitations", fx.heading), *((line, fx.wrap) for line in doc.limitations), ("", None)]
    lines.append((f"Source: {doc.dataset.attribution}", fx.note))
    for row, (text, fmt) in enumerate(lines):
        if text:
            notes.write_string(row, 0, text, fmt)


def render_report_xlsx(doc: ReportDocument) -> bytes:
    """Summary (metadata, executive summary, key figures), one sheet per table or chart, then findings,
    dataset version and methodology. Table sheets have frozen, filterable header rows."""
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True, **SAFE_OPTIONS})
    book.set_properties(
        {
            "title": doc.title,
            "subject": f"{doc.template_title} · {doc.period.label}",
            "author": doc.prepared_by,
            "company": "TripScope",
            "comments": f"Dataset version {doc.dataset.version_id}. Source: {doc.dataset.attribution}",
        }
    )
    fx = _Formats(book)
    used: set[str] = set()
    _summary_sheet(book, fx, doc, used)
    for section in doc.sections:
        for block in section.blocks:
            name = sheet_name(block.title, used)
            if isinstance(block, TableBlock):
                _table_sheet(book, fx, name, block)
            else:
                _chart_sheet(book, fx, name, block)
    _appendix_sheets(book, fx, doc, used)
    book.close()
    return buffer.getvalue()


# ---- direct trip extracts ---------------------------------------------------------------------------------

EXTRACT_FORMATS: dict[str, str] = {
    "pickup_datetime": "datetime",
    "dropoff_datetime": "datetime",
    "trip_distance": "miles",
    "trip_duration_minutes": "minutes",
    "average_speed_mph": "0.0",
    "fare_amount": "usd",
    "tip_amount": "usd",
    "tolls_amount": "usd",
    "total_amount": "usd",
    "congestion_surcharge": "usd",
    "airport_fee": "usd",
    "cbd_congestion_fee": "usd",
}


def write_extract_xlsx(
    path: Path,
    columns: list[str],
    rows: Iterable[tuple[Any, ...]],
    *,
    about: list[tuple[str, str]],
) -> int:
    """Stream rows into an XLSX file at `path` (constant memory). Returns the number of data rows written."""
    book = xlsxwriter.Workbook(
        str(path), {"constant_memory": True, "tmpdir": str(path.parent), **SAFE_OPTIONS}
    )
    book.set_properties({"title": "TripScope trip extract", "company": "TripScope"})
    fx = _Formats(book)
    sheet = book.add_worksheet("Trips")
    formats = []
    for col, name in enumerate(columns):
        kind = EXTRACT_FORMATS.get(name)
        fmt = (
            fx.datetime
            if kind == "datetime"
            else fx.unit(kind)
            if kind in NUMBER_FORMATS
            else book.add_format({"num_format": kind})
            if kind
            else None
        )
        formats.append(fmt)
        sheet.set_column(col, col, 20 if kind == "datetime" else max(len(name) + 2, 12))
    sheet.freeze_panes(1, 0)
    for col, name in enumerate(columns):
        sheet.write_string(0, col, name, fx.header)
    count = 0
    for count, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            write_value(sheet, count, col, value, formats[col])
    sheet.autofilter(0, 0, max(count, 1), len(columns) - 1)
    info = book.add_worksheet("About")
    info.set_column(0, 0, 24)
    info.set_column(1, 1, 100)
    info.write_string(0, 0, "TripScope trip extract", fx.title)
    for r, (label, value) in enumerate(about, start=2):
        info.write_string(r, 0, label, fx.label)
        info.write_string(r, 1, value, fx.wrap)
    book.close()
    return count
