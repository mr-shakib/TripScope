"""PDF rendering of a report document (FR-09): title block, sections, tables, vector charts, page numbers,
source attribution, methodology and limitations. Fonts are embedded (Geist, as in the web app).

All text comes from the document; dynamic strings are XML-escaped before they reach ReportLab's markup parser.
"""

from __future__ import annotations

import io
import math
from functools import cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from tripscope.reports import formatting as f
from tripscope.reports.document import ChartBlock, Column, ReportDocument, Section, TableBlock, narrative_note

FONT_DIR = Path(__file__).parent / "fonts"

INK = colors.HexColor("#0f172a")
INK_2 = colors.HexColor("#475569")
MUTED = colors.HexColor("#64748b")
FAINT = colors.HexColor("#94a3b8")
LINE = colors.HexColor("#e5e8ee")
SURFACE_2 = colors.HexColor("#f8f9fb")
ACCENT = colors.HexColor("#2a78d6")
ACCENT_SOFT = colors.HexColor("#e8f1fc")
GRID = colors.HexColor("#eceef2")
AXIS = colors.HexColor("#cbd2dc")
GOOD = colors.HexColor("#067306")
CRITICAL = colors.HexColor("#a32323")
SERIES = [colors.HexColor(c) for c in ("#2a78d6", "#eb6834", "#1baf7a", "#7c5cc4")]
SEQUENTIAL = [
    colors.HexColor(c) for c in ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
]
OPEN_BUCKET = colors.HexColor("#184f95")

PAGE_W, PAGE_H = A4
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 18 * mm, 20 * mm, 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X


@cache
def register_fonts() -> None:
    for name, file in (
        ("Geist", "Geist-Regular.ttf"),
        ("Geist-Medium", "Geist-Medium.ttf"),
        ("Geist-SemiBold", "Geist-SemiBold.ttf"),
        ("Geist-Bold", "Geist-Bold.ttf"),
    ):
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
    pdfmetrics.registerFontFamily(
        "Geist", normal="Geist", bold="Geist-SemiBold", italic="Geist", boldItalic="Geist-SemiBold"
    )


def _style(name: str, **kw: Any) -> ParagraphStyle:
    base: dict[str, Any] = {"fontName": "Geist", "fontSize": 9.5, "leading": 13.5, "textColor": INK}
    return ParagraphStyle(name, **(base | kw))


STYLES = {
    "eyebrow": _style("eyebrow", fontName="Geist-Medium", fontSize=8, leading=11, textColor=ACCENT),
    "title": _style("title", fontName="Geist-SemiBold", fontSize=22, leading=27, spaceBefore=4, spaceAfter=4),
    "period": _style("period", fontName="Geist-Medium", fontSize=11.5, leading=15, textColor=INK_2),
    "h2": _style("h2", fontName="Geist-SemiBold", fontSize=13.5, leading=18, spaceBefore=14, spaceAfter=2),
    "h3": _style("h3", fontName="Geist-SemiBold", fontSize=10, leading=13, spaceBefore=8, spaceAfter=4),
    "body": _style("body"),
    "muted": _style("muted", fontSize=8.5, leading=12, textColor=MUTED),
    "small": _style("small", fontSize=7.5, leading=10, textColor=MUTED),
    "cell": _style("cell", fontSize=8, leading=10.5),
    "cell_r": _style("cell_r", fontSize=8, leading=10.5, alignment=TA_RIGHT),
    "head": _style("head", fontName="Geist-Medium", fontSize=7.5, leading=10, textColor=INK_2),
    "head_r": _style(
        "head_r", fontName="Geist-Medium", fontSize=7.5, leading=10, textColor=INK_2, alignment=TA_RIGHT
    ),
    "label": _style(
        "label", fontName="Geist-Medium", fontSize=8, leading=11, textColor=MUTED, alignment=TA_LEFT
    ),
}


def _p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(escape(text), STYLES[style])


# ---- charts ----------------------------------------------------------------------------------------------


def _nice_max(value: float) -> tuple[float, float]:
    """A rounded axis maximum and tick step covering `value` with 4–6 ticks."""
    if value <= 0:
        return 1.0, 0.25
    exponent = math.floor(math.log10(value))
    for step_base in (1, 2, 2.5, 5, 10):
        step = step_base * 10 ** (exponent - 1)
        if value / step <= 6:
            return math.ceil(value / step) * step, step
    step = 10 ** (exponent + 1)
    return math.ceil(value / step) * step, step


def _axis_label(value: float, unit: str) -> str:
    if unit == "share":
        return f"{value * 100:g}%"
    prefix = "$" if unit == "usd" else ""
    if abs(value) >= 1_000_000:
        return f"{prefix}{value / 1_000_000:g}M"
    if abs(value) >= 1_000:
        return f"{prefix}{value / 1_000:g}K"
    return f"{prefix}{value:g}"


def _text(
    x: float,
    y: float,
    text: str,
    *,
    size: float = 6.5,
    color: Any = MUTED,
    anchor: str = "start",
    font: str = "Geist",
) -> String:
    return String(x, y, text, fontName=font, fontSize=size, fillColor=color, textAnchor=anchor)


def _fit(text: str, width: float, size: float) -> str:
    if pdfmetrics.stringWidth(text, "Geist", size) <= width:
        return text
    while text and pdfmetrics.stringWidth(text + "…", "Geist", size) > width:
        text = text[:-1]
    return text + "…"


def _value_axis(
    d: Drawing, x0: float, y0: float, w: float, h: float, top: float, step: float, unit: str
) -> None:
    ticks = round(top / step)
    for i in range(ticks + 1):
        y = y0 + h * i / ticks
        d.add(Line(x0, y, x0 + w, y, strokeColor=GRID if i else AXIS, strokeWidth=0.5))
        d.add(_text(x0 - 4, y - 2.2, _axis_label(step * i, unit), anchor="end"))


def _category_labels(d: Drawing, x0: float, y0: float, slot: float, labels: list[str]) -> None:
    every = max(1, math.ceil(len(labels) / 12))
    for i, label in enumerate(labels):
        if i % every == 0:
            d.add(_text(x0 + slot * (i + 0.5), y0 - 9, _fit(label, slot * every - 2, 6.5), anchor="middle"))


def _columns(block: ChartBlock, height: float) -> Drawing:
    d = Drawing(CONTENT_W, height)
    values = [v or 0 for v in block.series[0].values] if block.series else []
    x0, y0, w, h = 38, 16, CONTENT_W - 54, height - 30
    top, step = _nice_max(max(values, default=0))
    _value_axis(d, x0, y0, w, h, top, step, block.unit)
    slot = w / max(len(values), 1)
    gap = slot * (0.12 if block.kind == "histogram" else 0.28)
    open_index = (
        len(values) - 1
        if block.kind == "histogram" and block.categories[-1:] and "+" in block.categories[-1]
        else -1
    )
    for i, value in enumerate(values):
        bar_h = h * value / top if top else 0
        color = OPEN_BUCKET if i == open_index else SERIES[0]
        d.add(Rect(x0 + slot * i + gap / 2, y0, slot - gap, bar_h, fillColor=color, strokeColor=None))
    for marker in block.markers:
        x = x0 + slot * (marker.index + 0.5)
        d.add(Line(x, y0, x, y0 + h + 4, strokeColor=INK_2, strokeWidth=0.6))
        d.add(_text(x, y0 + h + 6, marker.label, color=INK_2, anchor="middle"))
    _category_labels(d, x0, y0, slot, block.categories)
    return d


def _lines(block: ChartBlock, height: float) -> Drawing:
    d = Drawing(CONTENT_W, height)
    legend = len(block.series) > 1
    x0, y0, w = 38, 16, CONTENT_W - 54
    h = height - (40 if legend else 26)
    peak = max((v for s in block.series for v in s.values if v is not None), default=0)
    top, step = _nice_max(peak)
    _value_axis(d, x0, y0, w, h, top, step, block.unit)
    n = max(len(block.categories), 1)
    slot = w / n
    for s_index, series in enumerate(block.series):
        points: list[float] = []
        for i, value in enumerate(series.values):
            if value is None:
                continue
            points += [x0 + slot * (i + 0.5), y0 + h * value / top]
        if len(points) >= 4:
            d.add(PolyLine(points, strokeColor=SERIES[s_index % len(SERIES)], strokeWidth=1.2))
    _category_labels(d, x0, y0, slot, block.categories)
    if legend:
        x = x0
        for s_index, series in enumerate(block.series):
            color = SERIES[s_index % len(SERIES)]
            d.add(Rect(x, height - 10, 8, 3, fillColor=color, strokeColor=None))
            d.add(_text(x + 11, height - 11, series.name, color=INK_2))
            x += 18 + pdfmetrics.stringWidth(series.name, "Geist", 6.5)
    return d


def _bars(block: ChartBlock) -> Drawing:
    values = [v or 0 for v in block.series[0].values] if block.series else []
    row_h = 13
    height = row_h * len(values) + 6
    d = Drawing(CONTENT_W, height)
    label_w = 150
    x0, w = label_w + 6, CONTENT_W - label_w - 70
    top = max(values, default=0) or 1
    for i, (label, value) in enumerate(zip(block.categories, values, strict=False)):
        y = height - row_h * (i + 1)
        d.add(_text(label_w, y + 3.5, _fit(label, label_w - 4, 7), size=7, color=INK, anchor="end"))
        d.add(Rect(x0, y + 1.5, max(w * value / top, 0.5), row_h - 4, fillColor=SERIES[0], strokeColor=None))
        d.add(_text(x0 + w * value / top + 4, y + 3.5, f.display(value, block.unit), size=7, color=INK_2))
    return d


def _heatmap(block: ChartBlock) -> Drawing:
    rows, cols = len(block.rows), len(block.categories)
    cell_h, label_w = 15, 30
    height = cell_h * rows + 34
    d = Drawing(CONTENT_W, height)
    cell_w = (CONTENT_W - label_w - 4) / max(cols, 1)
    values = [v for row in block.matrix for v in row if v is not None]
    low, high = min(values, default=0), max(values, default=1)
    for r, label in enumerate(block.rows):
        y = height - 16 - cell_h * (r + 1)
        d.add(_text(label_w - 4, y + 5, label, size=7, color=INK_2, anchor="end"))
        for c in range(cols):
            value = block.matrix[r][c] if r < len(block.matrix) and c < len(block.matrix[r]) else None
            if value is None:
                color = SURFACE_2
            else:
                ratio = (value - low) / (high - low) if high > low else 1
                color = SEQUENTIAL[min(len(SEQUENTIAL) - 1, int(ratio * len(SEQUENTIAL)))]
            d.add(
                Rect(
                    label_w + c * cell_w + 0.6,
                    y + 0.6,
                    cell_w - 1.2,
                    cell_h - 1.2,
                    fillColor=color,
                    strokeColor=None,
                )
            )
    for c, label in enumerate(block.categories):
        if c % 3 == 0:
            d.add(_text(label_w + cell_w * (c + 0.5), height - 12, label, anchor="middle"))
    # Legend: low → high on the sequential ramp.
    x = label_w
    d.add(_text(x, 2, f"{f.display(low, block.unit)}", color=INK_2))
    x += pdfmetrics.stringWidth(f.display(low, block.unit), "Geist", 6.5) + 4
    for color in SEQUENTIAL:
        d.add(Rect(x, 1, 14, 6, fillColor=color, strokeColor=None))
        x += 14
    d.add(_text(x + 4, 2, f"{f.display(high, block.unit)} trips", color=INK_2))
    return d


def _chart(block: ChartBlock) -> Drawing:
    if block.kind == "bar":
        return _bars(block)
    if block.kind == "heatmap":
        return _heatmap(block)
    if block.kind == "line":
        return _lines(block, 170)
    return _columns(block, 160)


# ---- tables ----------------------------------------------------------------------------------------------


def _evidence_value(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def _table(block: TableBlock, weights: list[float] | None = None) -> Table:
    numeric = [c.unit in f.NUMERIC_UNITS for c in block.columns]
    header = [
        Paragraph(escape(c.label), STYLES["head_r" if numeric[i] else "head"])
        for i, c in enumerate(block.columns)
    ]
    body = [
        [Paragraph(escape(text), STYLES["cell_r" if numeric[i] else "cell"]) for i, text in enumerate(row)]
        for row in block.display
    ]
    if weights is None:
        weights = [1.0 if numeric[i] else 2.4 for i in range(len(block.columns))]
        if block.columns and block.columns[0].key == "rank":
            weights[0] = 0.35
    total = sum(weights)
    table = Table([header, *body], colWidths=[CONTENT_W * w / total for w in weights], repeatRows=1)
    style = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, AXIS),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    style += [("BACKGROUND", (0, r), (-1, r), SURFACE_2) for r in range(2, len(body) + 1, 2)]
    table.setStyle(TableStyle(style))
    return table


def _kpi_table(doc: ReportDocument) -> Table:
    compare = doc.comparison.available
    header = ["Metric", "Value", *(["Previous period", "Change"] if compare else []), "Notes"]
    rows: list[list[Any]] = [
        [
            Paragraph(escape(h), STYLES["head_r" if 0 < i < len(header) - 1 else "head"])
            for i, h in enumerate(header)
        ]
    ]
    for kpi in doc.kpis:
        change_color = INK_2
        if kpi.change is not None and kpi.change != 0:
            change_color = GOOD if kpi.change > 0 else CRITICAL
        row: list[Any] = [
            Paragraph(escape(kpi.label), STYLES["cell"]),
            Paragraph(f'<font name="Geist-SemiBold">{escape(kpi.display)}</font>', STYLES["cell_r"]),
        ]
        if compare:
            row += [
                Paragraph(escape(kpi.previous_display or f.MISSING), STYLES["cell_r"]),
                Paragraph(
                    f'<font color="{change_color.hexval().replace("0x", "#")}">'
                    f"{escape(kpi.change_display or f.MISSING)}</font>",
                    STYLES["cell_r"],
                ),
            ]
        row.append(Paragraph(escape(kpi.note or ""), STYLES["small"]))
        rows.append(row)
    weights = [2.2, 1.4, *([1.4, 0.9] if compare else []), 2.0]
    total = sum(weights)
    table = Table(rows, colWidths=[CONTENT_W * w / total for w in weights], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, AXIS),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


# ---- document --------------------------------------------------------------------------------------------


class _NumberedCanvas(Canvas):  # type: ignore[misc]  # ReportLab ships no type hints
    """Draws 'Page n of N' once the total is known (two-pass page footer)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._report: ReportDocument = kwargs.pop("report")
        self._page_count: list[int] = kwargs.pop("page_count")
        super().__init__(*args, **kwargs)
        self._pages: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 - ReportLab API
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._pages)
        self._page_count.append(total)
        for state in self._pages:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        report = self._report
        number = self.getPageNumber()
        self.setStrokeColor(LINE)
        self.setLineWidth(0.5)
        self.line(MARGIN_X, MARGIN_BOTTOM - 6 * mm, PAGE_W - MARGIN_X, MARGIN_BOTTOM - 6 * mm)
        self.setFont("Geist", 7)
        self.setFillColor(MUTED)
        footer = f"TripScope · {report.dataset.name} · dataset version {report.dataset.version_id}"
        self.drawString(MARGIN_X, MARGIN_BOTTOM - 10 * mm, _fit(footer, CONTENT_W - 70, 7))
        self.drawRightString(PAGE_W - MARGIN_X, MARGIN_BOTTOM - 10 * mm, f"Page {number} of {total}")
        if number > 1:
            self.setFont("Geist-Medium", 7.5)
            self.drawString(MARGIN_X, PAGE_H - 12 * mm, _fit(report.title, CONTENT_W - 140, 7.5))
            self.setFont("Geist", 7.5)
            self.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 12 * mm, f"TripScope · {report.period.label}")


def _meta_table(doc: ReportDocument) -> Table:
    filters = "; ".join(f"{item.label}: {item.value}" for item in doc.filters[1:]) or "None"
    rows = [
        ("Period", doc.period.label),
        (
            "Dataset",
            f"{doc.dataset.name} · version {doc.dataset.version_id} "
            f"({f.plural(len(doc.dataset.periods), 'month')})",
        ),
        ("Filters", filters),
        ("Prepared by", doc.prepared_by),
        ("Generated", f.fmt_datetime(doc.generated_at)),
        ("Source", doc.dataset.attribution),
    ]
    if doc.comparison.available and doc.comparison.label:
        rows.insert(1, ("Compared with", doc.comparison.label))
    table = Table(
        [[Paragraph(escape(k), STYLES["label"]), Paragraph(escape(v), STYLES["cell"])] for k, v in rows],
        colWidths=[30 * mm, CONTENT_W - 30 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SURFACE_2),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _bullets(lines: list[str], style: str = "body") -> ListFlowable:
    return ListFlowable(
        [ListItem(_p(line, style), leftIndent=10, value="•") for line in lines],
        bulletType="bullet",
        bulletFontName="Geist",
        bulletFontSize=8,
        bulletColor=FAINT,
        leftIndent=10,
        spaceBefore=2,
    )


LONG_TABLE_ROWS = 25  # longer tables may split across pages (the header row repeats)


def _section(section: Section) -> list[Flowable]:
    heading: list[Flowable] = [_p(section.title, "h2"), _p(section.description, "muted")]
    parts: list[Flowable] = []
    for index, block in enumerate(section.blocks):
        body: Flowable = _chart(block) if isinstance(block, ChartBlock) else _table(block)
        group: list[Flowable] = [_p(block.title, "h3"), body]
        if block.note:
            group += [Spacer(1, 3), _p(block.note, "small")]
        if index == 0:
            group = heading + group  # a section heading never ends a page on its own
        long_table = isinstance(block, TableBlock) and len(block.rows) >= LONG_TABLE_ROWS
        parts += group if long_table else [KeepTogether(group)]
    return parts or heading


def render_pdf(doc: ReportDocument) -> tuple[bytes, int]:
    """Render the document; returns the PDF bytes and the page count."""
    register_fonts()
    buffer = io.BytesIO()
    template = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title=doc.title,
        author=doc.prepared_by,
        subject=f"{doc.template_title} · {doc.period.label}",
        creator="TripScope",
        producer="TripScope (ReportLab)",
    )
    frame = Frame(MARGIN_X, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="body")
    template.addPageTemplates([PageTemplate(id="page", frames=[frame])])

    story: list[Flowable] = [
        _p(f"TripScope report · {doc.template_title}", "eyebrow"),
        _p(doc.title, "title"),
        _p(doc.period.label, "period"),
        Spacer(1, 8),
        _meta_table(doc),
        _p("Executive summary", "h2"),
        _bullets(doc.summary),
    ]
    note = narrative_note(doc.narrative)
    if note:
        story += [Spacer(1, 3), _p(note, "small")]
    if doc.kpis:
        story += [_p("Key figures", "h2"), Spacer(1, 2), _kpi_table(doc)]
        if not doc.comparison.available and doc.comparison.reason:
            story += [Spacer(1, 3), _p(f"No period comparison: {doc.comparison.reason}", "small")]
    for section in doc.sections:
        story += _section(section)
    if doc.findings:
        story += [
            CondPageBreak(50 * mm),
            _p("Key findings", "h2"),
            _p("Each finding lists the values it rests on.", "muted"),
        ]
        for number, finding in enumerate(doc.findings, start=1):
            values = ", ".join(f"{k} = {_evidence_value(v)}" for k, v in finding.evidence.values.items())
            source = finding.evidence.source + (
                f" ({finding.evidence.source_table})" if finding.evidence.source_table else ""
            )
            kind = " <i>(hypothesis)</i>" if finding.kind == "hypothesis" else ""
            items: list[Flowable] = [
                Paragraph(
                    f'<font name="Geist-SemiBold">{number}.</font> {escape(finding.statement)}{kind}',
                    STYLES["body"],
                ),
                _p(f"Evidence: {source}: {values}", "small"),
            ]
            if finding.caveat:
                items.append(_p(f"Caveat: {finding.caveat}", "small"))
            story.append(KeepTogether([Spacer(1, 4), *items]))
    if doc.recommendations:
        story += [_p("Suggested next steps", "h2"), _p("Drafted by the AI analyst; not findings.", "muted")]
        story += [_bullets(doc.recommendations)]
    story += [
        CondPageBreak(40 * mm),
        _p("Dataset version", "h2"),
        _p(f"Version {doc.dataset.version_id}: the published monthly runs this report read.", "muted"),
        Spacer(1, 4),
        _table(
            TableBlock.build(
                id="dataset-version",
                title="Dataset version",
                columns=[
                    Column(key="period", label="Month"),
                    Column(key="run_id", label="Processing run"),
                    Column(key="rows", label="Rows", unit="rows"),
                    Column(key="published", label="Published"),
                ],
                rows=[
                    [p.period, p.run_id, p.row_count, f.fmt_datetime(p.published_at)]
                    for p in doc.dataset.periods
                ],
            ),
            weights=[0.7, 2.6, 0.9, 1.5],
        ),
        _p("Methodology", "h2"),
        _bullets(doc.methodology, "muted"),
        _p("Limitations", "h2"),
        _bullets(doc.limitations, "muted"),
        Spacer(1, 10),
        _p(f"Source: {doc.dataset.attribution}", "small"),
    ]
    page_count: list[int] = []
    template.build(
        story, canvasmaker=lambda *a, **k: _NumberedCanvas(*a, report=doc, page_count=page_count, **k)
    )
    return buffer.getvalue(), page_count[-1]
