"""Schema registry and drift across source files (FR-04 "schema differences across source files", FR-08).

Built from the schema each pipeline run recorded on `data_sources.source_schema`, so it reflects the files
actually received, not assumptions about TLC's publishing.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from tripscope.pipeline.canonical import CANONICAL_FIELDS


@dataclass(frozen=True)
class SourceSchema:
    period: str  # YYYY-MM
    source_key: str
    file_format: str
    schema_version: str | None
    columns: dict[str, str]  # source column -> physical type
    mapping: dict[str, str]  # canonical -> source column
    num_rows: int | None


def _diff(previous: SourceSchema, current: SourceSchema) -> dict[str, Any]:
    prev_lower = {c.lower(): c for c in previous.columns}
    curr_lower = {c.lower(): c for c in current.columns}
    added = sorted(curr_lower[k] for k in curr_lower.keys() - prev_lower.keys())
    removed = sorted(prev_lower[k] for k in prev_lower.keys() - curr_lower.keys())
    renamed_case, type_changed = [], []
    for key in sorted(prev_lower.keys() & curr_lower.keys()):
        before, after = prev_lower[key], curr_lower[key]
        if before != after:
            renamed_case.append({"from": before, "to": after})
        if previous.columns[before] != current.columns[after]:
            type_changed.append(
                {"column": after, "from": previous.columns[before], "to": current.columns[after]}
            )
    return {
        "from_period": previous.period,
        "to_period": current.period,
        "added": added,
        "removed": removed,
        "renamed_case": renamed_case,
        "type_changed": type_changed,
        "changed": bool(added or removed or renamed_case or type_changed),
    }


def build_schema_report(schemas: list[SourceSchema]) -> dict[str, Any]:
    ordered = sorted(schemas, key=lambda s: (s.period, s.source_key))
    fields = []
    for canonical in CANONICAL_FIELDS:
        availability = {s.period: s.mapping.get(canonical.name) for s in ordered}
        fields.append(
            {
                "name": canonical.name,
                "kind": canonical.kind,
                "required": canonical.required,
                "description": canonical.description,
                "source_column_by_period": availability,
                "available_in": sum(1 for v in availability.values() if v),
                "periods": len(ordered),
            }
        )
    versions: dict[str, dict[str, Any]] = {}
    for s in ordered:
        key = s.schema_version or "unknown"
        entry = versions.setdefault(
            key,
            {
                "schema_version": key,
                "file_format": s.file_format,
                "column_count": len(s.columns),
                "periods": [],
            },
        )
        entry["periods"].append(s.period)
    drift = [_diff(a, b) for a, b in pairwise(ordered)]
    return {
        "fields": fields,
        "schema_versions": list(versions.values()),
        "drift": drift,
        "sources": [
            {
                "period": s.period,
                "source_key": s.source_key,
                "file_format": s.file_format,
                "schema_version": s.schema_version,
                "column_count": len(s.columns),
                "num_rows": s.num_rows,
            }
            for s in ordered
        ],
    }
