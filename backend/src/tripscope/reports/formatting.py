"""Display formatting shared by every report renderer (mirrors frontend/src/lib/format.ts, exact mode).

Renderers never format numbers themselves: the document carries display strings next to raw values, so the web
preview, PDF, spreadsheet and CSV show the same text. Missing values are an em dash, never 0.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal

Unit = Literal[
    "trips", "usd", "miles", "minutes", "rows", "count", "share", "seconds", "bytes", "date", "text"
]

MISSING = "—"
NUMERIC_UNITS = frozenset({"trips", "usd", "miles", "minutes", "rows", "count", "share", "seconds", "bytes"})


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value)))


def integer(value: float) -> str:
    return f"{round(value):,}"


def percent(ratio: float | None) -> str:
    """A share (0–1) as a percentage that never rounds a non-zero share to 0% or a partial one to 100%."""
    if _missing(ratio):
        return MISSING
    assert ratio is not None
    if 0 < ratio < 0.0001:
        return "<0.01%"
    if 0.9999 <= ratio < 1:
        digits = min(6, max(2, math.ceil(-math.log10(1 - ratio)) - 2))
        return f"{math.floor(ratio * 10 ** (digits + 2)) / 10**digits:.{digits}f}%"
    places = 3 if ratio < 0.01 else 2
    text = f"{ratio * 100:.{places}f}".rstrip("0").rstrip(".")
    return f"{text}%"


def share(part: float | None, whole: float | None) -> float | None:
    return None if _missing(part) or not whole else float(part) / float(whole)  # type: ignore[arg-type]


def change(current: float | None, previous: float | None) -> float | None:
    """Relative change as a fraction (0.042 = +4.2%); None when either side is missing or the base is 0."""
    if _missing(current) or _missing(previous) or not previous:
        return None
    return (float(current) - float(previous)) / float(previous)  # type: ignore[arg-type]


def signed_change(ratio: float | None) -> str:
    """+4.2% / −12% (one decimal under 10%), matching the dashboard's KPI deltas."""
    if ratio is None:
        return MISSING
    pct = ratio * 100
    text = f"{abs(pct):.{1 if abs(pct) < 10 else 0}f}%"
    return f"+{text}" if pct > 0 else f"−{text}" if pct < 0 else text


def fmt_date(value: date | datetime | str | None) -> str:
    if value is None:
        return MISSING
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value:%b} {value.day}, {value.year}"


def fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return MISSING
    return f"{value:%b} {value.day}, {value.year} {value:%H:%M} UTC"


def plural(count: int, noun: str, plural_noun: str | None = None) -> str:
    """'1 file', '6 files' (with thousands separators)."""
    return f"{count:,} {noun if count == 1 else plural_noun or noun + 's'}"


def span_words(start: date, end: date) -> str:
    """'on Mar 1, 2025', 'from Mar 1 to Mar 31, 2025' or 'from Dec 1, 2024 to Jan 31, 2025'."""
    if start == end:
        return f"on {fmt_date(start)}"
    if start.year == end.year:
        return f"from {start:%b} {start.day} to {fmt_date(end)}"
    return f"from {fmt_date(start)} to {fmt_date(end)}"


def date_range(start: date, end: date) -> str:
    if start == end:
        return fmt_date(start)
    if start.year == end.year:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    return f"{fmt_date(start)} – {fmt_date(end)}"


def display(value: Any, unit: Unit) -> str:
    """Exact display text for a value in a unit."""
    if _missing(value):
        return MISSING
    if unit == "text":
        return str(value)
    if unit == "date":
        return fmt_date(value)
    number = float(value)
    match unit:
        case "trips" | "rows" | "count":
            return integer(number)
        case "usd":
            return f"-${abs(number):,.2f}" if number < 0 else f"${number:,.2f}"
        case "miles":
            return f"{number:,.2f} mi"
        case "minutes":
            return f"{number:,.1f} min"
        case "seconds":
            return f"{number:,.1f} s"
        case "share":
            return percent(number)
        case "bytes":
            size, units = number, ("B", "KB", "MB", "GB")
            step = 0
            while size >= 1000 and step < len(units) - 1:
                size, step = size / 1000, step + 1
            return f"{size:.{0 if step == 0 else 1}f} {units[step]}"
    return str(value)
