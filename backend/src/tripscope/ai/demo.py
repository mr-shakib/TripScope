"""Deterministic demo answers (spec §10.4: "a no-key development mode … with deterministic demo questions").

Each demo question has a fixed tool plan and fixed sentences filled from the tool results, so the AI page
shows the complete flow — tools, evidence, verified figures, caveats — without a language model. Demo answers
are labelled as such in the UI.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tripscope.ai.agent import AgentResult
from tripscope.ai.tools import ToolContext, ToolRun, execute, number_groups, numbers_in
from tripscope.ai.verify import verify
from tripscope.analytics.filters import AnalyticsFilters
from tripscope.reports import formatting as fmt


@dataclass(frozen=True)
class DemoQuestion:
    id: str
    text: str
    plan: Callable[[dict[str, Any]], list[tuple[str, dict[str, Any]]]]
    compose: Callable[[list[ToolRun]], tuple[str, list[str], list[str]]]


def _filters(page: AnalyticsFilters | None) -> dict[str, Any]:
    if page is None:
        return {}
    keep = ("start_date", "end_date", "pickup_zone", "dropoff_zone", "payment_type", "weekday", "hour")
    return {k: v for k, v in page.model_dump(mode="json").items() if k in keep and v not in (None, [])}


def _ok(runs: list[ToolRun], tool: str) -> dict[str, Any]:
    run = next((r for r in runs if r.tool == tool and r.output), None)
    if run is None or run.output is None:
        raise LookupError(
            next((r.error for r in runs if r.tool == tool and r.error), "the tool returned nothing")
        )
    return run.output.data


def _period(runs: list[ToolRun]) -> str:
    run = next((r for r in runs if r.output), None)
    return run.output.meta["period"] if run and run.output else "the published data"


def _months(runs: list[ToolRun]) -> tuple[str, list[str], list[str]]:
    points = [p for p in _ok(runs, "get_time_series")["points"] if p["value"] is not None]
    parts = [f"{points[0]['bucket']}: {fmt.integer(points[0]['value'])}"]
    parts += [
        f"{p['bucket']}: {fmt.integer(p['value'])} ({p['change_vs_previous'] * 100:+.2f}%)"
        for p in points[1:]
        if p["change_vs_previous"] is not None
    ]
    changes = [p for p in points if p["change_vs_previous"] is not None]
    text = f"Monthly trips for {_period(runs)}: " + "; ".join(parts) + "."
    if changes:
        up = max(changes, key=lambda p: p["change_vs_previous"])
        down = min(changes, key=lambda p: p["change_vs_previous"])
        text += (
            f" The largest rise was into {up['bucket']} ({up['change_vs_previous'] * 100:+.2f}%) "
            "and the largest "
            f"fall into {down['bucket']} ({down['change_vs_previous'] * 100:+.2f}%)."
        )
    return (
        text,
        ["Months differ in length (28 to 31 days), so part of each change is the number of days."],
        ["How did trips per day change month over month?", "Which weekdays drove the busiest month?"],
    )


def _zones(runs: list[ToolRun]) -> tuple[str, list[str], list[str]]:
    data = _ok(runs, "get_top_zones")
    zones = data["zones"]
    listed = ", ".join(
        f"{z['zone']} ({fmt.integer(z['trips'])} trips, {fmt.percent(z['share_of_trips'])})" for z in zones
    )
    text = f"The busiest pickup zones for {_period(runs)} were {listed}."
    return (
        text,
        ["Zones 264 and 265 (unknown or outside NYC) are counted as unmapped, not as places."],
        ["Which drop-off zones were busiest?", "What are the busiest pickup → drop-off pairs?"],
    )


def _distance_by_hour(runs: list[ToolRun]) -> tuple[str, list[str], list[str]]:
    groups = [g for g in _ok(runs, "get_breakdown")["groups"] if g["value"] is not None]
    high = max(groups, key=lambda g: g["value"])
    low = min(groups, key=lambda g: g["value"])
    text = (
        f"For {_period(runs)}, the average trip distance was longest at {high['label']} "
        f"({high['value']:.2f} mi over {fmt.integer(high['trips'])} trips) and shortest at {low['label']} "
        f"({low['value']:.2f} mi over {fmt.integer(low['trips'])} trips)."
    )
    return (
        text,
        [
            "Trips with an implausible distance are excluded from the average.",
            "Hours are NYC local pickup time.",
        ],
        ["Does the pattern differ on weekends?", "How does the average fare vary by hour?"],
    )


def _weekdays(runs: list[ToolRun]) -> tuple[str, list[str], list[str]]:
    split = _ok(runs, "get_breakdown")["weekday_vs_weekend"]
    wd, we = split["weekdays_mon_fri"], split["weekend_sat_sun"]
    diff = split["weekend_vs_weekday_per_day_change"]
    text = (
        f"For {_period(runs)}, weekdays (Mon–Fri) had {fmt.integer(wd['trips'])} trips "
        f"over {wd['days']} days, "
        f"{fmt.integer(wd['trips_per_day'])} per day; weekends (Sat–Sun) had {fmt.integer(we['trips'])} "
        "trips over "
        f"{we['days']} days, {fmt.integer(we['trips_per_day'])} per day. A weekend day averaged "
        f"{abs(diff) * 100:.2f}% {'more' if diff > 0 else 'fewer'} trips than a weekday."
    )
    return (
        text,
        ["Totals differ mostly because there are more weekdays; compare the per-day figures."],
        ["Which hours differ most between weekdays and weekends?", "How does this change month by month?"],
    )


def _unusual(runs: list[ToolRun]) -> tuple[str, list[str], list[str]]:
    anomalies = _ok(runs, "run_anomaly_analysis")
    dist = _ok(runs, "get_distribution")["summary"]
    days = anomalies["unusual_days"][:3]
    listed = "; ".join(
        f"{d['date']} ({d['weekday']}): average total amount ${d['value']:.2f} "
        f"against a usual ${d['baseline_median']:.2f}"
        for d in days
    )
    text = (
        f"For {_period(runs)}, {len(anomalies['unusual_days'])} of {anomalies['days_checked']} days "
        "had an unusual "
        f"average total amount for their weekday"
        + (f": {listed}." if days else ".")
        + f" Trip distances: {fmt.integer(dist['above_cap_trips'])} trips were 50 miles or longer and "
        f"{fmt.integer(dist['excluded_trips'])} had an implausible distance and were excluded."
    )
    return (
        text,
        [
            "Unusual means far from the usual level for that weekday; it is not evidence of error or fraud.",
            "Holidays, weather and events explain many unusual days; this analysis cannot show causes.",
            "Cash tips are not recorded, so amounts understate cash trips.",
        ],
        ["What happened on the most unusual day?", "How many trips were flagged for implausible amounts?"],
    )


DEMO_QUESTIONS: dict[str, DemoQuestion] = {
    q.id: q
    for q in (
        DemoQuestion(
            "month_over_month",
            "How did trip volume change month over month?",
            lambda f: [("get_time_series", {**f, "granularity": "month"})],
            _months,
        ),
        DemoQuestion(
            "top_pickup_zones",
            "Which pickup zones had the most trips in the selected period?",
            lambda f: [("get_top_zones", {**f, "zone_type": "pickup", "limit": 5})],
            _zones,
        ),
        DemoQuestion(
            "distance_by_hour",
            "Compare average trip distance by hour of day.",
            lambda f: [("get_breakdown", {**f, "dimension": "hour", "metric": "avg_trip_distance"})],
            _distance_by_hour,
        ),
        DemoQuestion(
            "weekday_vs_weekend",
            "Show the difference in trip volume between weekdays and weekends.",
            lambda f: [("get_breakdown", {**f, "dimension": "weekday"})],
            _weekdays,
        ),
        DemoQuestion(
            "unusual_patterns",
            "Find unusual fare and trip-distance patterns, then explain the limits of this analysis.",
            lambda f: [
                ("run_anomaly_analysis", {**f, "metric": "avg_total_amount"}),
                ("get_distribution", {**f, "metric": "trip_distance"}),
            ],
            _unusual,
        ),
    )
}


def run_demo(
    question: DemoQuestion,
    ctx: ToolContext,
    page_filters: AnalyticsFilters | None,
    on_tool_run: Callable[[ToolRun], None],
) -> AgentResult:
    started = time.perf_counter()
    runs = []
    for name, arguments in question.plan(_filters(page_filters)):
        run = execute(ctx, name, arguments)
        runs.append(run)
        on_tool_run(run)
    chart = next((r.output.chart for r in runs if r.output and r.output.chart), None)
    try:
        answer, caveats, follow_ups = question.compose(runs)
    except LookupError as exc:
        return AgentResult("failed", "", [], [], None, runs, verify("", []), chart, error=str(exc))
    values = [n for r in runs if r.output for n in numbers_in(r.output.data)]
    groups = [g for r in runs if r.output for g in number_groups(r.output.data).values()]
    return AgentResult(
        status="answered",
        answer=answer,
        caveats=caveats,
        follow_ups=follow_ups,
        clarification=None,
        tool_runs=runs,
        verification=verify(answer, values, groups),
        chart=chart,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )
