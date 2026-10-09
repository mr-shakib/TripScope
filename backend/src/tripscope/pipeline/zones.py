"""TLC Taxi Zone lookup parsing and validation."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from tripscope.core.errors import SourceValidationError
from tripscope.pipeline.rules import NON_GEOGRAPHIC_ZONE_IDS

EXPECTED_HEADER = ["LocationID", "Borough", "Zone", "service_zone"]
MAX_LOOKUP_BYTES = 1_000_000


@dataclass(frozen=True)
class Zone:
    location_id: int
    borough: str
    zone: str
    service_zone: str

    @property
    def is_geographic(self) -> bool:
        return self.location_id not in NON_GEOGRAPHIC_ZONE_IDS


def parse_zone_lookup(path: Path) -> list[Zone]:
    if path.stat().st_size > MAX_LOOKUP_BYTES:
        raise SourceValidationError("zone lookup file is unexpectedly large")
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header != EXPECTED_HEADER:
        raise SourceValidationError(f"unexpected zone lookup header: {header}")
    zones: list[Zone] = []
    seen: set[int] = set()
    for line_no, row in enumerate(reader, start=2):
        if not row:
            continue
        if len(row) != 4:
            raise SourceValidationError(f"zone lookup line {line_no} has {len(row)} fields")
        try:
            location_id = int(row[0])
        except ValueError as exc:
            raise SourceValidationError(f"zone lookup line {line_no}: LocationID is not an integer") from exc
        if not 1 <= location_id <= 10_000 or location_id in seen:
            raise SourceValidationError(f"zone lookup line {line_no}: invalid or duplicate LocationID")
        seen.add(location_id)
        zones.append(Zone(location_id, row[1].strip(), row[2].strip(), row[3].strip()))
    if not zones:
        raise SourceValidationError("zone lookup is empty")
    return zones


def mapped_zone_ids(zones: list[Zone]) -> list[int]:
    return sorted(z.location_id for z in zones if z.is_geographic)
