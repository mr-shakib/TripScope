"""Code labels from the TLC Yellow Taxi data dictionary (18 March 2025)."""

from __future__ import annotations

PAYMENT_TYPES: dict[int, str] = {
    0: "Flex Fare",
    1: "Credit card",
    2: "Cash",
    3: "No charge",
    4: "Dispute",
    5: "Unknown",
    6: "Voided trip",
}

VENDORS: dict[int, str] = {
    1: "Creative Mobile Technologies",
    2: "Curb Mobility",
    6: "Myle Technologies",
    7: "Helix",
}

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def code_label(labels: dict[int, str], key: object, kind: str) -> str:
    if key is None:
        return "Not recorded"
    return labels.get(int(str(key)), f"{kind} {key} (undocumented)")


# Human names for quality outcomes (keys mirror pipeline/rules.py and frontend/src/lib/quality-labels.ts).
QUARANTINE_LABELS: dict[str, str] = {
    "missing_required_timestamp": "Missing or unreadable timestamp",
    "dropoff_before_pickup": "Drop-off before pickup",
    "pickup_outside_source_period": "Outside the file’s month",  # noqa: RUF001 - typographic apostrophe
    "duplicate_record": "Exact duplicate",
}

FLAG_LABELS: dict[str, str] = {
    "invalid_distance": "Implausible distance",
    "invalid_duration": "Implausible duration",
    "invalid_amount": "Implausible amount",
    "implausible_speed": "Implausible speed",
    "passenger_count_missing_or_zero": "No passenger count",
    "pickup_zone_unmapped": "Pickup zone unknown",
    "dropoff_zone_unmapped": "Drop-off zone unknown",
}
