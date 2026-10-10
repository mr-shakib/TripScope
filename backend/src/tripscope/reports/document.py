"""The report document (ADR-18): one JSON structure that the web preview and every file format render.

Every number in it comes from an analytics-service result; tables carry raw values (for spreadsheets and CSV)
next to display strings (for the PDF and the preview), so all outputs show identical figures.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tripscope.reports.formatting import Unit, display

Scalar = str | int | float | bool | None
Number = int | float  # ints stay ints (trip counts), so CSV and XLSX never show 38960.0


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Kpi(_Model):
    id: str
    label: str
    unit: Unit
    value: Number | None
    display: str
    previous: Number | None = None
    previous_display: str | None = None
    change: float | None = None  # fraction, e.g. 0.042
    change_display: str | None = None
    excluded_rows: int = 0
    note: str | None = None


class Column(_Model):
    key: str
    label: str
    unit: Unit = "text"


class TableBlock(_Model):
    type: Literal["table"] = "table"
    id: str
    title: str
    columns: list[Column]
    rows: list[list[Scalar]]
    display: list[list[str]]
    note: str | None = None

    @classmethod
    def build(
        cls, *, id: str, title: str, columns: list[Column], rows: list[list[Scalar]], note: str | None = None
    ) -> TableBlock:
        shown = [[display(value, col.unit) for value, col in zip(row, columns, strict=True)] for row in rows]
        return cls(id=id, title=title, columns=columns, rows=rows, display=shown, note=note)


class Series(_Model):
    name: str
    values: list[Number | None]


class Marker(_Model):
    index: int
    label: str


class ChartBlock(_Model):
    type: Literal["chart"] = "chart"
    id: str
    title: str
    kind: Literal["line", "column", "bar", "heatmap", "histogram"]
    unit: Unit
    categories: list[str]  # x axis (column/line/histogram), bars top to bottom (bar), columns (heatmap)
    series: list[Series] = Field(default_factory=list)
    rows: list[str] = Field(default_factory=list)  # heatmap row labels
    matrix: list[list[Number | None]] = Field(default_factory=list)  # heatmap values [row][column]
    markers: list[Marker] = Field(default_factory=list)
    note: str | None = None


Block = Annotated[TableBlock | ChartBlock, Field(discriminator="type")]


class Section(_Model):
    id: str
    title: str
    description: str
    blocks: list[Block]


class Evidence(_Model):
    source: str  # the analytics result the values come from, e.g. "Trips by weekday"
    source_table: str | None
    values: dict[str, Scalar]


class Finding(_Model):
    id: str
    section: str
    statement: str
    kind: Literal["descriptive", "predictive", "hypothesis"] = "descriptive"
    evidence: Evidence
    caveat: str | None = None


class DatasetPeriod(_Model):
    period: str
    run_id: str
    row_count: int
    published_at: datetime


class DatasetInfo(_Model):
    id: str
    name: str
    attribution: str
    version_id: str  # short hash of the published (period, run_id) pairs the report read
    periods: list[DatasetPeriod]


class Period(_Model):
    start: date
    end: date
    days: int
    label: str
    data_first: date | None = None
    data_last: date | None = None


class FilterItem(_Model):
    label: str
    value: str


class Comparison(_Model):
    available: bool
    label: str | None = None
    start: date | None = None
    end: date | None = None
    reason: str | None = None


class ReportDocument(_Model):
    schema_version: Literal[1] = 1
    template: str
    template_title: str
    title: str
    period: Period
    filters: list[FilterItem]
    applied_filters: dict[str, Any]
    dataset: DatasetInfo
    generated_at: datetime
    prepared_by: str
    summary: list[str]
    kpis: list[Kpi]
    comparison: Comparison
    sections: list[Section]
    findings: list[Finding]
    methodology: list[str]
    limitations: list[str]
