"""CSV output of a report (FR-09): one tidy table, so every value sits in a documented column.

Columns:
  section    report | summary | <section id> | findings
  block      metadata | kpis | <table or chart id> | findings
  row        1-based row within the block (the category for chart series)
  field      column key, KPI field, series name or metadata key
  label      human label of the row (metric, category, weekday …)
  value      raw value (numbers unformatted, shares as fractions, dates ISO 8601)
  unit       trips | usd | miles | minutes | rows | count | share | seconds | date | text, or number for
             evidence values

Text cells that a spreadsheet would evaluate are prefixed with an apostrophe (analytics/export.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import chain
from typing import Any

from tripscope.analytics.export import csv_stream
from tripscope.reports.document import ChartBlock, ReportDocument, TableBlock, narrative_note

COLUMNS = ["section", "block", "row", "field", "label", "value", "unit"]


def _frame_rows(doc: ReportDocument) -> Iterator[tuple[Any, ...]]:
    metadata: list[tuple[str, Any, str]] = [
        ("title", doc.title, "text"),
        ("template", doc.template_title, "text"),
        ("period_start", doc.period.start, "date"),
        ("period_end", doc.period.end, "date"),
        *((item.label, item.value, "text") for item in doc.filters),
        ("dataset_version", doc.dataset.version_id, "text"),
        ("prepared_by", doc.prepared_by, "text"),
        (
            "generated_at_utc",
            doc.generated_at.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds"),
            "text",
        ),
        ("source", doc.dataset.attribution, "text"),
    ]
    for n, (key, value, unit) in enumerate(metadata, start=1):
        yield ("report", "metadata", n, key, key, value, unit)
    for n, period in enumerate(doc.dataset.periods, start=1):
        yield ("report", "dataset_version", n, "run_id", period.period, period.run_id, "text")
        yield ("report", "dataset_version", n, "row_count", period.period, period.row_count, "rows")
    note = narrative_note(doc.narrative)
    if note:
        yield ("summary", "narrative", 1, "note", "", note, "text")
    for n, line in enumerate(doc.recommendations, start=1):
        yield ("summary", "recommendations", n, "text", "", line, "text")
    for n, line in enumerate(doc.summary, start=1):
        yield ("summary", "executive_summary", n, "text", "", line, "text")
    for n, kpi in enumerate(doc.kpis, start=1):
        yield ("summary", "kpis", n, "value", kpi.label, kpi.value, kpi.unit)
        if kpi.previous is not None:
            yield ("summary", "kpis", n, "previous_period", kpi.label, kpi.previous, kpi.unit)
        if kpi.change is not None:
            yield ("summary", "kpis", n, "change", kpi.label, kpi.change, "share")
        yield ("summary", "kpis", n, "excluded_rows", kpi.label, kpi.excluded_rows, "rows")


def _body_rows(doc: ReportDocument) -> Iterator[tuple[Any, ...]]:
    for section in doc.sections:
        for block in section.blocks:
            if isinstance(block, TableBlock):
                for n, row in enumerate(block.rows, start=1):
                    label = str(row[0]) if row else ""
                    for value, column in zip(row, block.columns, strict=True):
                        yield (section.id, block.id, n, column.key, label, value, column.unit)
            else:
                yield from _chart_rows(section.id, block)
    for n, finding in enumerate(doc.findings, start=1):
        yield ("findings", "findings", n, "statement", finding.section, finding.statement, "text")
        yield ("findings", "findings", n, "kind", finding.section, finding.kind, "text")
        yield ("findings", "findings", n, "evidence_source", finding.section, finding.evidence.source, "text")
        for key, value in finding.evidence.values.items():
            unit = "text" if isinstance(value, str) or value is None else "number"
            yield ("findings", "findings", n, f"evidence.{key}", finding.section, value, unit)


def _chart_rows(section: str, block: ChartBlock) -> Iterator[tuple[Any, ...]]:
    if block.kind == "heatmap":
        for r, row_label in enumerate(block.rows):
            for c, column in enumerate(block.categories):
                yield (
                    section,
                    block.id,
                    r * len(block.categories) + c + 1,
                    column,
                    row_label,
                    block.matrix[r][c],
                    block.unit,
                )
        return
    for n, category in enumerate(block.categories, start=1):
        for series in block.series:
            yield (section, block.id, n, series.name, category, series.values[n - 1], block.unit)


def render_report_csv(doc: ReportDocument) -> bytes:
    return b"".join(csv_stream(COLUMNS, chain(_frame_rows(doc), _body_rows(doc))))
