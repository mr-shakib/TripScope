"""CSV extracts: documented column names, standard quoting, and spreadsheet formula-injection protection.

Text cells that a spreadsheet would evaluate (starting with = + - @, tab or carriage return) are prefixed with
an apostrophe. Numbers, dates and booleans are written as values, so negative amounts stay numeric.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value: str) -> str:
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(sep=" ")  # NYC local wall-clock, as stored
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return safe_text(";".join(str(v) for v in value))
    return safe_text(str(value))


def csv_stream(
    columns: list[str], rows: Iterable[tuple[Any, ...]], *, chunk_rows: int = 5000
) -> Iterator[bytes]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(columns)
    pending = 1
    for row in rows:
        writer.writerow([cell(v) for v in row])
        pending += 1
        if pending >= chunk_rows:
            yield buffer.getvalue().encode()
            buffer.seek(0)
            buffer.truncate()
            pending = 0
    if buffer.tell():
        yield buffer.getvalue().encode()
