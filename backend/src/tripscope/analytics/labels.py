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
