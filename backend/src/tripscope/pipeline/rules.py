"""Configurable cleaning and quality rules (spec §7.2). Every run stores the exact rule values it used."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Quarantine reasons: the row is removed from curated data and written to the quarantine area.
QUARANTINE_REASONS = (
    "missing_required_timestamp",
    "dropoff_before_pickup",
    "pickup_outside_source_period",
    "duplicate_record",
)

# Flags: the row stays in curated data; metrics that depend on the flagged value exclude it.
FLAGS = (
    "invalid_distance",
    "invalid_duration",
    "invalid_amount",
    "implausible_speed",
    "passenger_count_missing_or_zero",
    "pickup_zone_unmapped",
    "dropoff_zone_unmapped",
)

# Zone IDs present in the TLC lookup that do not identify a real zone ("Unknown", "Outside of NYC").
NON_GEOGRAPHIC_ZONE_IDS = frozenset({264, 265})


class QualityRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_trip_distance_miles: float = Field(default=200.0, gt=0)
    max_trip_duration_minutes: float = Field(default=720.0, gt=0)
    max_total_amount_usd: float = Field(default=1000.0, gt=0)
    max_average_speed_mph: float = Field(default=80.0, gt=0)
