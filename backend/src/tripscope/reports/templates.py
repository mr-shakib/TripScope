"""Report templates 1–5 (FR-09). Template 6 (custom AI-assisted) arrives with the AI analyst in Phase 5.

Every report has the same frame — title and period, filters and dataset version, executive summary, KPI table,
key findings, methodology and limitations. Templates choose the KPIs and the optional sections in between.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    description: str


@dataclass(frozen=True)
class TemplateSpec:
    id: str
    number: int
    title: str
    description: str
    kpis: tuple[str, ...]
    sections: tuple[SectionSpec, ...]

    def section_ids(self) -> list[str]:
        return [s.id for s in self.sections]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "description": self.description,
            "kpis": list(self.kpis),
            "sections": [{"id": s.id, "title": s.title, "description": s.description} for s in self.sections],
        }


TREND = SectionSpec("trend", "Trips over time", "Daily trips across the period (monthly for long ranges).")
WEEKDAY = SectionSpec("weekday", "Demand by weekday", "Trips and average trips per day for each weekday.")
TOP_PICKUP = SectionSpec("top_pickup_zones", "Top pickup zones", "The busiest pickup zones and their share.")
TOP_DROPOFF = SectionSpec(
    "top_dropoff_zones", "Top drop-off zones", "The busiest drop-off zones and their share."
)
QUALITY_SNAPSHOT = SectionSpec(
    "quality_snapshot",
    "Data quality snapshot",
    "Rows read, published and quarantined, and the commonest flags.",
)
PAYMENT_MIX = SectionSpec("payment_mix", "Payment mix", "Trips and average total amount by payment type.")

TEMPLATES: dict[str, TemplateSpec] = {
    t.id: t
    for t in (
        TemplateSpec(
            id="executive_overview",
            number=1,
            title="Executive overview",
            description="Headline KPIs with a period comparison, the trend, weekly rhythm, top zones and "
            "payment mix, and a data-quality snapshot.",
            kpis=(
                "total_trips",
                "avg_daily_trips",
                "total_recorded_amount",
                "avg_total_amount",
                "avg_trip_distance",
                "avg_trip_duration_minutes",
            ),
            sections=(TREND, WEEKDAY, TOP_PICKUP, PAYMENT_MIX, QUALITY_SNAPSHOT),
        ),
        TemplateSpec(
            id="demand_patterns",
            number=2,
            title="Trip demand and time patterns",
            description="When people ride: daily trend, hour of day, weekday, the hour × weekday grid and "
            "the busiest time slots.",
            kpis=("total_trips", "avg_daily_trips", "avg_trip_duration_minutes"),
            sections=(
                TREND,
                SectionSpec("hour", "Demand by hour of day", "Trips by pickup hour (NYC local time)."),
                WEEKDAY,
                SectionSpec("heatmap", "Hour × weekday", "Trips for every weekday and pickup hour."),
                SectionSpec("peak_slots", "Busiest time slots", "The ten busiest weekday-and-hour slots."),
            ),
        ),
        TemplateSpec(
            id="fares_distance",
            number=3,
            title="Fare and distance analysis",
            description="Distance and amount distributions, payment types, fares by hour and the rows each "
            "metric excludes.",
            kpis=(
                "total_recorded_amount",
                "avg_total_amount",
                "avg_trip_distance",
                "avg_trip_duration_minutes",
            ),
            sections=(
                SectionSpec(
                    "distance_distribution",
                    "Trip distance distribution",
                    "Valid distances in 1-mile buckets.",
                ),
                SectionSpec(
                    "amount_distribution", "Total amount distribution", "Valid total amounts in $5 buckets."
                ),
                SectionSpec(
                    "payment_types",
                    "Payment types",
                    "Trips, average total amount and distance by payment type.",
                ),
                SectionSpec(
                    "amount_by_hour",
                    "Average total amount by hour",
                    "Average valid total amount by pickup hour.",
                ),
                SectionSpec(
                    "exclusions", "Excluded values", "Rows each metric leaves out because of a flag."
                ),
            ),
        ),
        TemplateSpec(
            id="zone_analysis",
            number=4,
            title="Pickup and drop-off zone analysis",
            description="Where trips start and end: boroughs, top pickup and drop-off zones, and the busiest "
            "pickup → drop-off pairs.",
            kpis=("total_trips", "avg_trip_distance", "avg_total_amount"),
            sections=(
                SectionSpec("boroughs", "Pickups by borough", "Trips by the borough of the pickup zone."),
                TOP_PICKUP,
                TOP_DROPOFF,
                SectionSpec("top_flows", "Busiest pickup → drop-off pairs", "The most frequent zone pairs."),
            ),
        ),
        TemplateSpec(
            id="data_quality",
            number=5,
            title="Data quality and processing",
            description="What the pipeline read, published and quarantined, quality flags, daily flag trends "
            "and schema consistency.",
            kpis=(),
            sections=(
                SectionSpec(
                    "processing", "Processing runs", "Each published month and the run that produced it."
                ),
                SectionSpec(
                    "quarantine",
                    "Quarantined rows",
                    "Rows removed from curated data, by reason (whole months).",
                ),
                SectionSpec(
                    "flags", "Quality flags", "Kept trips with a suspect value, by flag (whole months)."
                ),
                SectionSpec("daily_flags", "Daily flag rates", "Share of trips flagged each day, per flag."),
                SectionSpec(
                    "schema", "Schema consistency", "Schema versions and column changes between files."
                ),
            ),
        ),
    )
}


def _library() -> tuple[SectionSpec, ...]:
    """Sections an AI-assisted report may combine: trip sections of templates 1–4 and the quality snapshot."""
    seen: dict[str, SectionSpec] = {}
    for template in TEMPLATES.values():
        if template.id == "data_quality":
            continue
        for section in template.sections:
            seen.setdefault(section.id, section)
    when = ["trend", "hour", "weekday", "heatmap", "peak_slots"]
    where = ["boroughs", "top_pickup_zones", "top_dropoff_zones", "top_flows"]
    how_much = ["payment_mix", "distance_distribution", "amount_distribution", "amount_by_hour", "exclusions"]
    order = [*when, *where, *how_much, "quality_snapshot"]
    return tuple(seen[s] for s in order)  # payment_types is left out: payment_mix covers the same table


TEMPLATES["custom_ai"] = TemplateSpec(
    id="custom_ai",
    number=6,
    title="Custom AI-assisted report",
    description="An outline proposed by the AI analyst from the section library, with an executive summary "
    "and findings drafted from the report's own results; every figure is checked against them.",
    kpis=(
        "total_trips",
        "avg_daily_trips",
        "total_recorded_amount",
        "avg_total_amount",
        "avg_trip_distance",
        "avg_trip_duration_minutes",
    ),
    sections=_library(),
)
