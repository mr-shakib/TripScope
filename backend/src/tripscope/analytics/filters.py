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
Weekday = Annotated[int, Field(ge=1, le=7)]  # ISO: 1 = Monday … 7 = Sunday

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
    weekday: list[Weekday] = Field(default_factory=list, max_length=7)
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


class OverviewQuery(AnalyticsFilters):
    compare: Literal["none", "previous"] = "none"


class ZoneQuery(AnalyticsFilters):
    metric: TimeSeriesMetric = "total_trips"
    side: Literal["pickup", "dropoff"] = "pickup"
    limit: int = Field(default=10, ge=1, le=300)


class FlowQuery(AnalyticsFilters):
    metric: TimeSeriesMetric = "total_trips"
    limit: int = Field(default=15, ge=1, le=50)


class DistributionQuery(AnalyticsFilters):
    metric: Literal["trip_distance", "total_amount"] = "trip_distance"


# Kept in sync with pipeline.rules.FLAGS and query_builder.SORTABLE / EXPLORER_COLUMNS (unit-tested).
FlagName = Literal[
    "invalid_distance",
    "invalid_duration",
    "invalid_amount",
    "implausible_speed",
    "passenger_count_missing_or_zero",
    "pickup_zone_unmapped",
    "dropoff_zone_unmapped",
]
SortColumn = Literal[
    "pickup_datetime",
    "trip_distance",
    "trip_duration_minutes",
    "total_amount",
    "fare_amount",
    "tip_amount",
    "passenger_count",
    "average_speed_mph",
]
ExplorerColumn = Literal[
    "pickup_datetime",
    "dropoff_datetime",
    "pickup_location_id",
    "dropoff_location_id",
    "trip_distance",
    "trip_duration_minutes",
    "passenger_count",
    "payment_type",
    "vendor_id",
    "rate_code_id",
    "fare_amount",
    "tip_amount",
    "tolls_amount",
    "total_amount",
    "congestion_surcharge",
    "airport_fee",
    "cbd_congestion_fee",
    "average_speed_mph",
    "quality_flags",
    "source_file",
    "run_id",
]
MAX_PREVIEW_OFFSET = 10_000
PAGE_SIZES = (25, 50, 100)


class RowScope(BaseModel):
    """Row-level options shared by the explorer preview and extracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sort: SortColumn = "pickup_datetime"
    order: Literal["asc", "desc"] = "desc"
    quality: Literal["all", "clean", "flagged"] = "all"
    flag: FlagName | None = None


class ExplorerQuery(AnalyticsFilters):
    sort: SortColumn = "pickup_datetime"
    order: Literal["asc", "desc"] = "desc"
    quality: Literal["all", "clean", "flagged"] = "all"
    flag: FlagName | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = 50

    @model_validator(mode="after")
    def _bounded(self) -> ExplorerQuery:
        if self.page_size not in PAGE_SIZES:
            raise ValueError(f"page_size must be one of {PAGE_SIZES}")
        if self.page * self.page_size > MAX_PREVIEW_OFFSET:
            raise ValueError(
                f"preview is limited to the first {MAX_PREVIEW_OFFSET:,} rows; "
                "narrow the filters or download an extract"
            )
        return self

    def scope(self) -> RowScope:
        return RowScope(sort=self.sort, order=self.order, quality=self.quality, flag=self.flag)

    def filters(self) -> AnalyticsFilters:
        return AnalyticsFilters.model_validate(self.model_dump(include=set(AnalyticsFilters.model_fields)))


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["csv", "xlsx"] = "csv"
    filters: AnalyticsFilters = Field(default_factory=AnalyticsFilters)
    scope: RowScope = Field(default_factory=RowScope)
    columns: list[ExplorerColumn] | None = Field(default=None, min_length=1, max_length=30)
    max_rows: int | None = Field(default=None, ge=1)
