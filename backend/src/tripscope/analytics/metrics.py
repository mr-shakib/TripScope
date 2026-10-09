"""Metric definitions: the single source of truth for formulas, units and caveats.

Documented for humans in docs/metric-definitions.md.

Expressions are fixed strings written here, never assembled from request input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Unit = Literal["trips", "usd", "miles", "minutes"]

CASH_TIP_CAVEAT = "TLC records exclude cash tips, so amounts understate what passengers paid in cash."
FLAG_CAVEAT = "Rows flagged for this value are excluded; the excluded count is returned with every result."


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    label: str
    unit: Unit
    expression: str  # over taxi_trips
    excluded_rows_expression: str | None
    agg_expression: str  # the same metric over trips_hourly_agg (sum / count, so results are identical)
    agg_excluded_rows_expression: str | None
    description: str
    rows_included: str
    caveats: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "expression",
            "excluded_rows_expression",
            "agg_expression",
            "agg_excluded_rows_expression",
        ):
            data.pop(key)
        data["caveats"] = list(self.caveats)
        return data


METRICS: dict[str, MetricDefinition] = {
    m.id: m
    for m in (
        MetricDefinition(
            id="total_trips",
            label="Total trips",
            unit="trips",
            expression="count()",
            excluded_rows_expression=None,
            agg_expression="sum(trips)",
            agg_excluded_rows_expression=None,
            description="Number of accepted trip records.",
            rows_included="All curated trips (quarantined rows are not counted).",
        ),
        MetricDefinition(
            id="avg_daily_trips",
            label="Trips per day",
            unit="trips",
            expression="count() / nullIf(uniqExact(pickup_date), 0)",
            excluded_rows_expression=None,
            agg_expression="sum(trips) / nullIf(uniqExact(pickup_date), 0)",
            agg_excluded_rows_expression=None,
            description="Average number of accepted trips per calendar day that has data in the selection.",
            rows_included="All curated trips; days without any matching trip are not counted.",
            caveats=("With hour or weekday filters this is trips per matching day, not per calendar day.",),
        ),
        MetricDefinition(
            id="total_recorded_amount",
            label="Total recorded amount",
            unit="usd",
            expression="sumIf(total_amount, is_amount_valid)",
            excluded_rows_expression="countIf(NOT is_amount_valid)",
            agg_expression="if(sum(amount_valid_trips) > 0, sum(total_amount_valid_sum), NULL)",
            agg_excluded_rows_expression="sum(trips) - sum(amount_valid_trips)",
            description="Sum of TLC total_amount (fare, surcharges, taxes, tolls, card tips) in USD.",
            rows_included="Trips with 0 ≤ total_amount ≤ $1,000 and fare_amount ≥ 0.",
            caveats=(CASH_TIP_CAVEAT, FLAG_CAVEAT, "Negative amounts (refunds, disputes) are excluded."),
        ),
        MetricDefinition(
            id="avg_total_amount",
            label="Average total amount per trip",
            unit="usd",
            expression="avgIf(total_amount, is_amount_valid)",
            excluded_rows_expression="countIf(NOT is_amount_valid)",
            agg_expression="toFloat64(sum(total_amount_valid_sum)) / nullIf(sum(amount_valid_trips), 0)",
            agg_excluded_rows_expression="sum(trips) - sum(amount_valid_trips)",
            description="Mean TLC total_amount per trip in USD.",
            rows_included="Trips with 0 ≤ total_amount ≤ $1,000 and fare_amount ≥ 0.",
            caveats=(CASH_TIP_CAVEAT, FLAG_CAVEAT),
        ),
        MetricDefinition(
            id="avg_trip_distance",
            label="Average trip distance",
            unit="miles",
            expression="avgIf(trip_distance, is_distance_valid)",
            excluded_rows_expression="countIf(NOT is_distance_valid)",
            agg_expression="sum(trip_distance_valid_sum) / nullIf(sum(distance_valid_trips), 0)",
            agg_excluded_rows_expression="sum(trips) - sum(distance_valid_trips)",
            description="Mean taximeter distance in miles.",
            rows_included="Trips with 0 < trip_distance ≤ 200 miles.",
            caveats=(FLAG_CAVEAT,),
        ),
        MetricDefinition(
            id="avg_trip_duration_minutes",
            label="Average trip duration",
            unit="minutes",
            expression="avgIf(trip_duration_minutes, is_duration_valid)",
            excluded_rows_expression="countIf(NOT is_duration_valid)",
            agg_expression="sum(trip_duration_valid_sum) / nullIf(sum(duration_valid_trips), 0)",
            agg_excluded_rows_expression="sum(trips) - sum(duration_valid_trips)",
            description="Mean minutes between meter engaged and disengaged.",
            rows_included="Trips with 0 < duration ≤ 720 minutes.",
            caveats=(FLAG_CAVEAT,),
        ),
    )
}

OVERVIEW_METRICS = tuple(METRICS)
