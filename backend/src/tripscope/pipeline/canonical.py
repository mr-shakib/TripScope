"""Canonical trip schema and per-file source-column mapping (FR-05).

The mapping is computed from each file's actual schema. Column names are matched case-insensitively through
an alias list (TLC has changed casing over time, e.g. `Airport_fee` vs `airport_fee`). A canonical field that
the source does not provide is reported as unavailable and written as NULL — never fabricated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from tripscope.core.errors import SourceValidationError


@dataclass(frozen=True)
class CanonicalField:
    name: str
    kind: str  # int | long | double | money | string | timestamp
    aliases: tuple[str, ...]
    required: bool = False
    description: str = ""


CANONICAL_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField("vendor_id", "int", ("vendorid",), description="TPEP provider code"),
    CanonicalField(
        "pickup_datetime",
        "timestamp",
        ("tpep_pickup_datetime",),
        required=True,
        description="Meter engaged (NYC local wall-clock)",
    ),
    CanonicalField(
        "dropoff_datetime",
        "timestamp",
        ("tpep_dropoff_datetime",),
        required=True,
        description="Meter disengaged (NYC local wall-clock)",
    ),
    CanonicalField("passenger_count", "int", ("passenger_count",), description="Driver-entered passengers"),
    CanonicalField("trip_distance", "double", ("trip_distance",), description="Taximeter miles"),
    CanonicalField("pickup_location_id", "int", ("pulocationid",), description="TLC Taxi Zone of pickup"),
    CanonicalField("dropoff_location_id", "int", ("dolocationid",), description="TLC Taxi Zone of drop-off"),
    CanonicalField("rate_code_id", "int", ("ratecodeid",), description="Final rate code"),
    CanonicalField(
        "store_and_fwd_flag", "string", ("store_and_fwd_flag",), description="Y/N store and forward"
    ),
    CanonicalField("payment_type", "int", ("payment_type",), description="Payment code"),
    CanonicalField("fare_amount", "money", ("fare_amount",), description="Time-and-distance fare, USD"),
    CanonicalField("extra", "money", ("extra",), description="Misc extras and surcharges, USD"),
    CanonicalField("mta_tax", "money", ("mta_tax",), description="MTA tax, USD"),
    CanonicalField("tip_amount", "money", ("tip_amount",), description="Card tips only, USD"),
    CanonicalField("tolls_amount", "money", ("tolls_amount",), description="Tolls, USD"),
    CanonicalField("improvement_surcharge", "money", ("improvement_surcharge",), description="USD"),
    CanonicalField(
        "total_amount", "money", ("total_amount",), description="Total charged excl. cash tips, USD"
    ),
    CanonicalField(
        "congestion_surcharge", "money", ("congestion_surcharge",), description="NYS congestion, USD"
    ),
    CanonicalField("airport_fee", "money", ("airport_fee",), description="LGA/JFK pickup fee, USD"),
    CanonicalField(
        "cbd_congestion_fee", "money", ("cbd_congestion_fee",), description="MTA CRZ fee from 2025-01-05, USD"
    ),
)

CANONICAL_NAMES: tuple[str, ...] = tuple(f.name for f in CANONICAL_FIELDS)


@dataclass(frozen=True)
class SchemaMapping:
    """Result of matching one source file's columns to the canonical schema."""

    source_columns: dict[str, str]  # source column name -> physical type (as reported by the reader)
    mapping: dict[str, str]  # canonical name -> source column name
    unavailable: tuple[str, ...]  # canonical fields absent from this source
    unexpected: tuple[str, ...]  # source columns with no canonical meaning (recorded, ignored)
    schema_version: str = field(default="")

    def as_dict(self) -> dict[str, object]:
        return {
            "source_columns": self.source_columns,
            "mapping": self.mapping,
            "unavailable": list(self.unavailable),
            "unexpected": list(self.unexpected),
            "schema_version": self.schema_version,
        }


def schema_fingerprint(taxi_type: str, source_columns: dict[str, str]) -> str:
    """Stable identifier of a source schema: same column names (case-sensitive) and types → same version."""
    canonical = ";".join(f"{name}:{dtype}" for name, dtype in sorted(source_columns.items()))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return f"{taxi_type}-{digest}"


def map_source_schema(taxi_type: str, source_columns: dict[str, str]) -> SchemaMapping:
    by_lower: dict[str, str] = {}
    for column in source_columns:
        lower = column.lower()
        if lower in by_lower:
            raise SourceValidationError(
                f"ambiguous source columns differing only by case: {by_lower[lower]!r} and {column!r}"
            )
        by_lower[lower] = column

    mapping: dict[str, str] = {}
    unavailable: list[str] = []
    for canonical in CANONICAL_FIELDS:
        match = next((by_lower[a] for a in canonical.aliases if a in by_lower), None)
        if match is None:
            if canonical.required:
                raise SourceValidationError(
                    f"required field {canonical.name} not found (looked for {', '.join(canonical.aliases)})"
                )
            unavailable.append(canonical.name)
        else:
            mapping[canonical.name] = match

    used = set(mapping.values())
    unexpected = tuple(sorted(c for c in source_columns if c not in used))
    return SchemaMapping(
        source_columns=dict(source_columns),
        mapping=mapping,
        unavailable=tuple(unavailable),
        unexpected=unexpected,
        schema_version=schema_fingerprint(taxi_type, source_columns),
    )
