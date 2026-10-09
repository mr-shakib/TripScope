"""Field catalogue for the data explorer: what each column means and where it comes from."""

from __future__ import annotations

from typing import Any

from tripscope.pipeline.canonical import CANONICAL_FIELDS

UNITS = {
    "trip_distance": "miles",
    "trip_duration_minutes": "minutes",
    "average_speed_mph": "mph",
    "fare_amount": "USD",
    "tip_amount": "USD",
    "tolls_amount": "USD",
    "total_amount": "USD",
    "congestion_surcharge": "USD",
    "airport_fee": "USD",
    "cbd_congestion_fee": "USD",
}

DERIVED: dict[str, tuple[str, str]] = {
    "trip_duration_minutes": ("Float64", "Drop-off minus pickup, in minutes."),
    "average_speed_mph": (
        "Nullable(Float64)",
        "Distance ÷ duration when both are valid and ≤ 80 mph; otherwise blank.",
    ),
    "quality_flags": ("Array(String)", "Quality flags raised on this trip (see Data quality)."),
    "source_file": ("String", "Original TLC file the row came from."),
    "run_id": ("String", "Processing run that produced the row."),
}

CODES = {
    "payment_type": "0 Flex Fare, 1 Credit card, 2 Cash, 3 No charge, 4 Dispute, 5 Unknown, 6 Voided trip",
    "vendor_id": "1 Creative Mobile Technologies, 2 Curb Mobility, 6 Myle Technologies, 7 Helix",
    "rate_code_id": (
        "1 Standard, 2 JFK, 3 Newark, 4 Nassau/Westchester, 5 Negotiated, 6 Group ride, 99 Unknown"
    ),
}


def field_catalogue(
    columns: list[str], availability: dict[str, dict[str, str | None]]
) -> list[dict[str, Any]]:
    canonical = {f.name: f for f in CANONICAL_FIELDS}
    fields = []
    for name in columns:
        if name in canonical:
            spec = canonical[name]
            fields.append(
                {
                    "name": name,
                    "origin": "source",
                    "type": spec.kind,
                    "description": spec.description,
                    "source_columns": sorted({c for c in availability.get(name, {}).values() if c}),
                    "available_in": sum(1 for c in availability.get(name, {}).values() if c),
                    "periods": len(availability.get(name, {})),
                }
            )
        else:
            kind, description = DERIVED[name]
            fields.append(
                {
                    "name": name,
                    "origin": "derived",
                    "type": kind,
                    "description": description,
                    "source_columns": [],
                    "available_in": None,
                    "periods": None,
                }
            )
        fields[-1]["unit"] = UNITS.get(name)
        fields[-1]["codes"] = CODES.get(name)
    return fields
