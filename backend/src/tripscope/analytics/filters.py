"""Shared analytics filters (spec §9). One model validates filters for the dashboard, exports, reports and AI
tools, so every path applies identical semantics. Unknown fields are rejected, not ignored."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ZoneId = Annotated[int, Field(ge=1, le=265)]
PaymentType = Annotated[int, Field(ge=0, le=6)]
VendorId = Annotated[int, Field(ge=1, le=99)]
Hour = Annotated[int, Field(ge=0, le=23)]

DEFAULT_DATASET = "nyc-tlc-yellow"
MAX_LIST_ITEMS = 50


class AnalyticsFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = Field(default=DEFAULT_DATASET, pattern=r"^[a-z0-9-]{1,64}$")
    start_date: date | None = Field(default=None, description="Inclusive pickup date (NYC local)")
    end_date: date | None = Field(default=None, description="Inclusive pickup date (NYC local)")
    pickup_zone: list[ZoneId] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    dropoff_zone: list[ZoneId] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    payment_type: list[PaymentType] = Field(default_factory=list, max_length=7)
    vendor_id: list[VendorId] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    hour: list[Hour] = Field(default_factory=list, max_length=24)
    min_distance: float | None = Field(default=None, ge=0, le=1000)
    max_distance: float | None = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _ranges(self) -> AnalyticsFilters:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if (
            self.min_distance is not None
            and self.max_distance is not None
            and self.max_distance < self.min_distance
        ):
            raise ValueError("max_distance must not be less than min_distance")
        return self

    def applied(self) -> dict[str, object]:
        """Only the filters the user actually set, for display and audit."""
        return self.model_dump(mode="json", exclude_defaults=True) | {"dataset_id": self.dataset_id}


Granularity = Literal["hour", "day", "month"]
TimeSeriesMetric = Literal[
    "total_trips",
    "total_recorded_amount",
    "avg_total_amount",
    "avg_trip_distance",
    "avg_trip_duration_minutes",
]


class TimeSeriesQuery(AnalyticsFilters):
    metric: TimeSeriesMetric = "total_trips"
    granularity: Granularity = "day"


class BreakdownQuery(AnalyticsFilters):
    metric: TimeSeriesMetric = "total_trips"


class TopZonesQuery(AnalyticsFilters):
    metric: TimeSeriesMetric = "total_trips"
    limit: int = Field(default=10, ge=1, le=50)
