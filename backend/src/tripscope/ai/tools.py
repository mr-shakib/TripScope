"""Allowlisted analytics tools for the AI analyst (spec §10.1, ADR-20).

Each tool validates its arguments with a strict Pydantic model, calls the shared analytics service (read-only
user, server-side limits), and returns: the full bounded result (kept as evidence), a compact text view for
the model, and metadata (filters, period, metric definitions, source table, timing). Results are aggregates
only; no tool returns trip rows. Text that comes from data (zone names) reaches the model inside tool results,
which the system prompt marks as data, never instructions.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tripscope.analytics.filters import AnalyticsFilters, TimeSeriesQuery
from tripscope.analytics.labels import FLAG_LABELS, QUARANTINE_LABELS, WEEKDAYS
from tripscope.analytics.metrics import METRICS
from tripscope.analytics.service import AnalyticsService
from tripscope.core.errors import PermissionDeniedError, TripScopeError, ValidationFailedError
from tripscope.metadata.models import Role
from tripscope.reports import formatting as fmt
from tripscope.reports.service import Actor
from tripscope.reports.templates import TEMPLATES

ZoneId = Annotated[int, Field(ge=1, le=265)]
MetricId = Literal[
    "total_trips",
    "avg_daily_trips",
    "total_recorded_amount",
    "avg_total_amount",
    "avg_trip_distance",
    "avg_trip_duration_minutes",
]
SeriesMetric = Literal[
    "total_trips",
    "total_recorded_amount",
    "avg_total_amount",
    "avg_trip_distance",
    "avg_trip_duration_minutes",
]
MAX_VIEW_ROWS = 40


class ToolFilters(BaseModel):
    """Filters every analytics tool accepts (the dashboard's filters, minus distance)."""

    model_config = ConfigDict(extra="forbid")
    start_date: date | None = Field(default=None, description="first pickup date, YYYY-MM-DD")
    end_date: date | None = Field(default=None, description="last pickup date, YYYY-MM-DD")
    pickup_zone: list[ZoneId] = Field(default_factory=list, max_length=20, description="pickup zone IDs")
    dropoff_zone: list[ZoneId] = Field(default_factory=list, max_length=20, description="drop-off zone IDs")
    payment_type: list[Annotated[int, Field(ge=0, le=6)]] = Field(
        default_factory=list, max_length=7, description="1 card, 2 cash, 3 no charge, 4 dispute, 0 flex fare"
    )
    weekday: list[Annotated[int, Field(ge=1, le=7)]] = Field(
        default_factory=list, max_length=7, description="1 Monday … 7 Sunday"
    )
    hour: list[Annotated[int, Field(ge=0, le=23)]] = Field(default_factory=list, max_length=24)

    def analytics(self, dataset_id: str, **override: Any) -> AnalyticsFilters:
        data = self.model_dump(include=set(ToolFilters.model_fields)) | override
        return AnalyticsFilters(dataset_id=dataset_id, **data)


class OverviewArgs(ToolFilters):
    pass


class TimeSeriesArgs(ToolFilters):
    metric: SeriesMetric = "total_trips"
    granularity: Literal["day", "month", "hour"] = Field(default="day", description="hour only for ≤ 62 days")


class ComparePeriodsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period_a_start: date
    period_a_end: date
    period_b_start: date
    period_b_end: date
    pickup_zone: list[ZoneId] = Field(default_factory=list, max_length=20)
    dropoff_zone: list[ZoneId] = Field(default_factory=list, max_length=20)
    payment_type: list[Annotated[int, Field(ge=0, le=6)]] = Field(default_factory=list, max_length=7)
    weekday: list[Annotated[int, Field(ge=1, le=7)]] = Field(default_factory=list, max_length=7)
    hour: list[Annotated[int, Field(ge=0, le=23)]] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def _ordered(self) -> ComparePeriodsArgs:
        if self.period_a_end < self.period_a_start or self.period_b_end < self.period_b_start:
            raise ValueError("each period must end on or after its start")
        return self


class TopZonesArgs(ToolFilters):
    zone_type: Literal["pickup", "dropoff"] = "pickup"
    metric: SeriesMetric = "total_trips"
    limit: int = Field(default=10, ge=1, le=20)


class BreakdownArgs(ToolFilters):
    dimension: Literal["hour", "weekday", "payment_type", "vendor"]
    metric: SeriesMetric = "total_trips"


class DistributionArgs(ToolFilters):
    metric: Literal["trip_distance", "total_amount"] = "trip_distance"


class QualityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date | None = None
    end_date: date | None = None


class AnomalyArgs(ToolFilters):
    metric: SeriesMetric = "total_trips"
    grouping: Literal["day", "daily", "date", "weekday"] = Field(
        default="day", description="always daily values against the same weekday's usual level"
    )


class ChartArgs(ToolFilters):
    chart_type: Literal["line", "bar", "column"]
    dimension: Literal["day", "month", "hour", "weekday", "payment_type", "pickup_zone", "dropoff_zone"]
    metric: SeriesMetric = "total_trips"
    limit: int = Field(default=10, ge=1, le=20, description="zones only")

    @model_validator(mode="after")
    def _compatible(self) -> ChartArgs:
        time = self.dimension in ("day", "month")
        if self.chart_type == "line" and not time:
            raise ValueError("line charts are for day or month series; use bar or column")
        if time and self.chart_type == "bar":
            raise ValueError("use a line or column chart for a time series")
        return self


class ReportDraftArgs(ToolFilters):
    report_type: Literal[
        "executive_overview", "demand_patterns", "fares_distance", "zone_analysis", "data_quality"
    ]
    selected_sections: list[Annotated[str, Field(pattern=r"^[a-z_]{1,40}$")]] | None = Field(
        default=None, max_length=10
    )
    title: str | None = Field(default=None, max_length=200)


class FindZonesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=60, description="part of a zone or borough name")


@dataclass
class ToolContext:
    analytics: AnalyticsService
    user: Actor
    dataset_id: str
    create_report: Callable[..., dict[str, Any]] | None = None  # injected by the API (owns the DB session)


@dataclass
class ToolOutput:
    data: dict[str, Any]
    view: str
    meta: dict[str, Any]
    chart: dict[str, Any] | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[ToolContext, Any], ToolOutput]
    chartable: bool = False


# ---- helpers ---------------------------------------------------------------------------------------------


def _period_label(meta: dict[str, Any], filters: AnalyticsFilters) -> str:
    coverage = meta.get("coverage") or {}
    start = filters.start_date or coverage.get("start_date")
    end = filters.end_date or coverage.get("end_date")
    if not start or not end:
        return "all published data"
    return fmt.date_range(fmt_dateobj(start), fmt_dateobj(end))


def fmt_dateobj(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _meta(result: dict[str, Any], filters: AnalyticsFilters, metrics: list[str]) -> dict[str, Any]:
    meta = result.get("meta") or {}
    return {
        "period": _period_label(meta, filters),
        "filters": filters.applied(),
        "metrics": [METRICS[m].public() for m in metrics if m in METRICS],
        "source_table": meta.get("source_table"),
        "query_ms": meta.get("query_ms"),
    }


def _num(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit in ("trips", "rows", "count"):
        return f"{round(value):,}"
    if unit == "usd":
        return f"${value:,.2f}"
    if unit == "share":
        return f"{value * 100:.2f}%"
    return f"{value:,.2f}"


def _table(columns: list[str], rows: list[list[str]], *, total: int | None = None) -> str:
    lines = [" | ".join(columns)] + [" | ".join(r) for r in rows[:MAX_VIEW_ROWS]]
    if total is not None and total > len(rows[:MAX_VIEW_ROWS]):
        lines.append(f"… {total - MAX_VIEW_ROWS} more rows not shown")
    return "\n".join(lines)


def _header(name: str, meta: dict[str, Any], extra: str = "") -> str:
    filters = {k: v for k, v in meta["filters"].items() if k not in ("dataset_id", "start_date", "end_date")}
    applied = ", ".join(f"{k}={v}" for k, v in filters.items()) or "none"
    return f"{name}{extra} — period {meta['period']}; filters: {applied}; source {meta['source_table']}"


def _change(ratio: float | None) -> str:
    return "n/a" if ratio is None else f"{ratio * 100:+.2f}%"


def _unit(metric: str) -> str:
    return METRICS[metric].unit if metric in METRICS else "count"


def _total_trips(ctx: ToolContext, filters: AnalyticsFilters) -> int:
    overview = ctx.analytics.overview(filters)
    return int(overview["kpis"]["total_trips"]["value"]) if overview.get("kpis") else 0


# ---- tools -----------------------------------------------------------------------------------------------


def overview_tool(ctx: ToolContext, args: OverviewArgs) -> ToolOutput:
    filters = args.analytics(ctx.dataset_id)
    result = ctx.analytics.overview(filters)
    meta = _meta(result, filters, list(METRICS))
    if not result.get("kpis"):
        return ToolOutput({"kpis": None}, _header("get_overview_metrics", meta) + "\nNo trips match.", meta)
    rows = [
        [METRICS[m].label, _num(k["value"], k["unit"]), k["unit"], f"{k['excluded_rows']:,}"]
        for m, k in result["kpis"].items()
    ]
    days = (result.get("result_range") or {}).get("days_with_data")
    view = _header("get_overview_metrics", meta) + f"; days with data {days}\n"
    view += _table(["metric", "value", "unit", "excluded rows"], rows)
    return ToolOutput({"kpis": result["kpis"], "result_range": result.get("result_range")}, view, meta)


def time_series_tool(ctx: ToolContext, args: TimeSeriesArgs) -> ToolOutput:
    filters = args.analytics(ctx.dataset_id)
    query = TimeSeriesQuery(**filters.model_dump(), metric=args.metric, granularity=args.granularity)
    result = ctx.analytics.time_series(query)
    meta = _meta(result, filters, [args.metric])
    unit = _unit(args.metric)
    points = [
        {
            "bucket": str(p["bucket"])[
                : 7 if args.granularity == "month" else 16 if args.granularity == "hour" else 10
            ],
            "value": p["value"],
            "trips": p["trips"],
        }
        for p in result["points"]
    ]
    # Change from the previous bucket, so "month over month" needs no further calls.
    for previous, point in zip([None, *points[:-1]], points, strict=True):
        point["change_vs_previous"] = fmt.change(point["value"], previous["value"]) if previous else None
    view = _header("get_time_series", meta, f"({args.metric}, {args.granularity})") + "\n"
    if len(points) <= MAX_VIEW_ROWS:
        view += _table(
            ["bucket", args.metric, "trips", "change vs previous"],
            [
                [p["bucket"], _num(p["value"], unit), f"{p['trips']:,}", _change(p["change_vs_previous"])]
                for p in points
            ],
        )
    else:
        valued = [p for p in points if p["value"] is not None]
        high = max(valued, key=lambda p: p["value"])
        low = min(valued, key=lambda p: p["value"])
        mean = statistics.fmean(p["value"] for p in valued)
        view += (
            f"{len(points)} points (summary; ask for month granularity for a compact series). "
            f"highest {high['bucket']}: {_num(high['value'], unit)}; lowest {low['bucket']}: "
            f"{_num(low['value'], unit)}; "
            f"mean {_num(mean, unit)}\nfirst and last points:\n"
        )
        view += _table(
            ["bucket", args.metric, "trips"],
            [[p["bucket"], _num(p["value"], unit), f"{p['trips']:,}"] for p in points[:5] + points[-5:]],
        )
    chart = {
        "type": "line" if args.granularity != "month" or len(points) > 3 else "column",
        "title": f"{METRICS[args.metric].label} by {args.granularity}",
        "unit": unit,
        "categories": [p["bucket"] for p in points],
        "series": [{"name": METRICS[args.metric].label, "values": [p["value"] for p in points]}],
    }
    return ToolOutput({"points": points}, view, meta, chart)


def compare_periods_tool(ctx: ToolContext, args: ComparePeriodsArgs) -> ToolOutput:
    shared = args.model_dump(exclude={"period_a_start", "period_a_end", "period_b_start", "period_b_end"})
    a = AnalyticsFilters(
        dataset_id=ctx.dataset_id, start_date=args.period_a_start, end_date=args.period_a_end, **shared
    )
    b = AnalyticsFilters(
        dataset_id=ctx.dataset_id, start_date=args.period_b_start, end_date=args.period_b_end, **shared
    )
    coverage = ctx.analytics.coverage(ctx.dataset_id)
    periods = (("A", args.period_a_start, args.period_a_end), ("B", args.period_b_start, args.period_b_end))
    for name, start, end in periods:
        if coverage.start is None or coverage.end is None or end < coverage.start or start > coverage.end:
            published = (
                fmt.date_range(coverage.start, coverage.end) if coverage.start and coverage.end else "nothing"
            )
            raise ValidationFailedError(
                f"period {name} ({fmt.date_range(start, end)}) has no published data; "
                f"published data covers {published}"
            )
    ra, rb = ctx.analytics.overview(a), ctx.analytics.overview(b)
    meta = _meta(ra, a, list(METRICS))
    meta["period"] = (
        f"A {fmt.date_range(args.period_a_start, args.period_a_end)} vs B "
        f"{fmt.date_range(args.period_b_start, args.period_b_end)}"
    )
    rows, data = [], {}
    for metric in METRICS:
        va = (ra.get("kpis") or {}).get(metric, {}).get("value")
        vb = (rb.get("kpis") or {}).get(metric, {}).get("value")
        change = fmt.change(vb, va)
        data[metric] = {"period_a": va, "period_b": vb, "change_b_vs_a": change}
        unit = _unit(metric)
        rows.append(
            [
                METRICS[metric].label,
                _num(va, unit),
                _num(vb, unit),
                "n/a" if change is None else f"{change * 100:+.2f}%",
            ]
        )
    view = (
        _header("compare_periods", meta)
        + "\n"
        + _table(["metric", "period A", "period B", "change B vs A"], rows)
    )
    return ToolOutput(
        {
            "metrics": data,
            "period_a": [str(args.period_a_start), str(args.period_a_end)],
            "period_b": [str(args.period_b_start), str(args.period_b_end)],
        },
        view,
        meta,
    )


def top_zones_tool(ctx: ToolContext, args: TopZonesArgs) -> ToolOutput:
    filters = args.analytics(ctx.dataset_id)
    result = ctx.analytics.breakdown(
        filters, metric=args.metric, dimension=f"{args.zone_type}_zone", limit=args.limit
    )
    total = _total_trips(ctx, filters)
    meta = _meta(result, filters, [args.metric])
    unit = _unit(args.metric)
    zones = [
        {
            "rank": i + 1,
            "zone_id": g["key"],
            "zone": g["label"],
            "borough": g.get("borough"),
            "value": g["value"],
            "trips": g["trips"],
            "share_of_trips": fmt.share(g["trips"], total),
        }
        for i, g in enumerate(result["groups"])
    ]
    rows = [
        [
            str(z["rank"]),
            str(z["zone"]),
            str(z["borough"] or "unmapped"),
            _num(z["value"], unit),
            f"{z['trips']:,}",
            _num(z["share_of_trips"], "share"),
        ]
        for z in zones
    ]
    view = _header("get_top_zones", meta, f"({args.zone_type}, {args.metric})") + f"; total trips {total:,}\n"
    view += _table(["rank", "zone", "borough", args.metric, "trips", "share of trips"], rows)
    chart = {
        "type": "bar",
        "title": f"Top {args.zone_type} zones",
        "unit": unit,
        "categories": [str(z["zone"]) for z in zones],
        "series": [{"name": METRICS[args.metric].label, "values": [z["value"] for z in zones]}],
    }
    return ToolOutput({"zones": zones, "total_trips": total}, view, meta, chart)


def breakdown_tool(ctx: ToolContext, args: BreakdownArgs) -> ToolOutput:
    filters = args.analytics(ctx.dataset_id)
    dimension = "vendor_id" if args.dimension == "vendor" else args.dimension
    result = ctx.analytics.breakdown(filters, metric=args.metric, dimension=dimension)
    meta = _meta(result, filters, [args.metric])
    unit = _unit(args.metric)
    groups = [
        {"key": g["key"], "label": g["label"], "value": g["value"], "trips": g["trips"]}
        for g in result["groups"]
    ]
    data: dict[str, Any] = {"groups": groups}
    rows = [[str(g["label"]), _num(g["value"], unit), f"{g['trips']:,}"] for g in groups]
    columns = [args.dimension, args.metric, "trips"]
    extra = ""
    if args.dimension == "weekday":
        # Trips per day need the number of days each weekday appears; weekday vs weekend is a common question.
        query = TimeSeriesQuery(**filters.model_dump(), metric="total_trips", granularity="day")
        days: dict[int, int] = defaultdict(int)
        for point in ctx.analytics.time_series(query)["points"]:
            days[fmt_dateobj(point["bucket"]).isoweekday()] += 1
        for g, row in zip(groups, rows, strict=True):
            g["days"] = days.get(int(g["key"]), 0)
            g["trips_per_day"] = g["trips"] / g["days"] if g["days"] else None
            row += [str(g["days"]), _num(g["trips_per_day"], "trips")]
        columns += ["days", "trips per day"]
        split: dict[str, Any] = {}
        for name, members in (("weekdays_mon_fri", range(1, 6)), ("weekend_sat_sun", range(6, 8))):
            trips = sum(g["trips"] for g in groups if int(g["key"]) in members)
            n = sum(g["days"] for g in groups if int(g["key"]) in members)
            split[name] = {"trips": trips, "days": n, "trips_per_day": trips / n if n else None}
        diff = fmt.change(
            split["weekend_sat_sun"]["trips_per_day"], split["weekdays_mon_fri"]["trips_per_day"]
        )
        split["weekend_vs_weekday_per_day_change"] = diff
        data["weekday_vs_weekend"] = split
        extra = (
            f"\nweekdays Mon–Fri: {split['weekdays_mon_fri']['trips']:,} trips over "
            f"{split['weekdays_mon_fri']['days']} days"
            f" = {_num(split['weekdays_mon_fri']['trips_per_day'], 'trips')} per day; weekend Sat–Sun: "
            f"{split['weekend_sat_sun']['trips']:,} trips over {split['weekend_sat_sun']['days']} days = "
            f"{_num(split['weekend_sat_sun']['trips_per_day'], 'trips')} per day; weekend vs weekday per day "
            f"{'n/a' if diff is None else f'{diff * 100:+.2f}%'}"
        )
    view = (
        _header("get_breakdown", meta, f"({args.dimension}, {args.metric})")
        + "\n"
        + _table(columns, rows)
        + extra
    )
    chart = {
        "type": "column",
        "title": f"{METRICS[args.metric].label} by {args.dimension}",
        "unit": unit,
        "categories": [str(g["label"]) for g in groups],
        "series": [{"name": METRICS[args.metric].label, "values": [g["value"] for g in groups]}],
    }
    return ToolOutput(data, view, meta, chart)


def distribution_tool(ctx: ToolContext, args: DistributionArgs) -> ToolOutput:
    filters = args.analytics(ctx.dataset_id)
    result = ctx.analytics.distribution(filters, metric=args.metric)
    metric = "avg_trip_distance" if args.metric == "trip_distance" else "avg_total_amount"
    meta = _meta(result, filters, [metric])
    summary, buckets = result["summary"], result["buckets"]
    unit = "mi" if args.metric == "trip_distance" else "$"

    def label(b: dict[str, Any] | None) -> str:
        if not b:
            return "n/a"
        return f"{b['start']:g}+ {unit}" if b.get("end") is None else f"{b['start']:g}–{b['end']:g} {unit}"

    top = sorted(buckets, key=lambda b: -b["trips"])[:8]
    view = _header("get_distribution", meta, f"({args.metric})") + (
        f"\ncounted {summary['counted_trips']:,} valid trips; excluded (flagged) "
        f"{summary['excluded_trips']:,}; "
        f"median bucket {label(summary['median_bucket'])}; 90th percentile bucket "
        f"{label(summary['p90_bucket'])}; "
        f"open-ended bucket ≥ {summary['cap']:g} {unit}: {summary['above_cap_trips']:,} trips\nbusiest "
        f"buckets:\n"
    )
    view += _table(
        ["bucket", "trips", "share"],
        [
            [label(b), f"{b['trips']:,}", _num(fmt.share(b["trips"], summary["counted_trips"]), "share")]
            for b in top
        ],
    )
    chart = {
        "type": "column",
        "title": f"{args.metric.replace('_', ' ')} distribution",
        "unit": "trips",
        "categories": [label(b) for b in buckets],
        "series": [{"name": "Trips", "values": [b["trips"] for b in buckets]}],
    }
    return ToolOutput({"summary": summary, "buckets": buckets}, view, meta, chart)


def quality_tool(ctx: ToolContext, args: QualityArgs) -> ToolOutput:
    filters = AnalyticsFilters(dataset_id=ctx.dataset_id, start_date=args.start_date, end_date=args.end_date)
    result = ctx.analytics.quality(filters)
    coverage = ctx.analytics.coverage(ctx.dataset_id)
    start, end = args.start_date or coverage.start, args.end_date or coverage.end
    covered = {
        f"{p.data_period:%Y-%m}"
        for p in coverage.periods
        if (end is None or p.min_pickup_date <= end) and (start is None or p.max_pickup_date >= start)
    }
    periods = [p for p in result["periods"] if p["period"] in covered]
    totals: dict[str, Any] = {"input_rows": 0, "accepted_rows": 0, "quarantined_rows": 0}
    reasons: dict[str, int] = defaultdict(int)
    flags: dict[str, int] = defaultdict(int)
    for p in periods:
        for k in totals:
            totals[k] += p[k]
        for k, v in p["quarantine_reasons"].items():
            reasons[k] += int(v)
        for k, v in p["flags"].items():
            flags[k] += int(v)
    meta: dict[str, Any] = {
        "period": fmt.date_range(start, end) if start and end else "all published data",
        "filters": filters.applied(),
        "metrics": [],
        "source_table": "processing runs",
        "query_ms": None,
    }
    view = _header("get_data_quality_summary", meta) + (
        f"\nmonths {sorted(covered)}; rows read {totals['input_rows']:,}; published "
        f"{totals['accepted_rows']:,}; "
        f"quarantined {totals['quarantined_rows']:,}\n"
    )
    view += _table(
        ["quarantine reason", "rows"],
        [[QUARANTINE_LABELS.get(k, k), f"{v:,}"] for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])],
    )
    view += "\n" + _table(
        ["flag on kept trips", "trips", "share of published"],
        [
            [FLAG_LABELS.get(k, k), f"{v:,}", _num(fmt.share(v, totals["accepted_rows"]), "share")]
            for k, v in sorted(flags.items(), key=lambda kv: -kv[1])
        ],
    )
    view += "\nFlags mark suspect values on kept trips; they are not evidence of fraud."
    return ToolOutput(
        {
            "months": sorted(covered),
            "totals": totals,
            "quarantine_reasons": dict(reasons),
            "flags": dict(flags),
        },
        view,
        meta,
    )


def anomaly_tool(ctx: ToolContext, args: AnomalyArgs) -> ToolOutput:
    """Unusual days by robust z-score (median and MAD) against the same weekday's baseline."""
    filters = args.analytics(ctx.dataset_id)
    query = TimeSeriesQuery(**filters.model_dump(), metric=args.metric, granularity="day")
    result = ctx.analytics.time_series(query)
    meta = _meta(result, filters, [args.metric])
    unit = _unit(args.metric)
    points = [(fmt_dateobj(p["bucket"]), p["value"]) for p in result["points"] if p["value"] is not None]
    by_weekday: dict[int, list[float]] = defaultdict(list)
    for day, value in points:
        by_weekday[day.isoweekday()].append(float(value))
    scored = []
    for day, value in points:
        sample = (
            by_weekday[day.isoweekday()] if len(by_weekday[day.isoweekday()]) >= 4 else [v for _, v in points]
        )
        median = statistics.median(sample)
        mad = statistics.median(abs(v - median) for v in sample)
        z = 0.6745 * (float(value) - median) / mad if mad else 0.0
        scored.append(
            {
                "date": day.isoformat(),
                "weekday": WEEKDAYS[day.isoweekday() - 1],
                "value": value,
                "baseline_median": median,
                "robust_z": round(z, 2),
            }
        )
    unusual = sorted((s for s in scored if abs(s["robust_z"]) >= 3.5), key=lambda s: -abs(s["robust_z"]))[:10]
    view = _header("run_anomaly_analysis", meta, f"({args.metric}, daily vs same-weekday median)") + (
        f"\n{len(points)} days checked; {len(unusual)} unusual (|robust z| ≥ 3.5)\n"
    )
    view += _table(
        ["date", "weekday", args.metric, "same-weekday median", "robust z"],
        [
            [
                u["date"],
                u["weekday"],
                _num(u["value"], unit),
                _num(u["baseline_median"], unit),
                f"{u['robust_z']:+.2f}",
            ]
            for u in unusual
        ],
    )
    view += (
        "\nUnusual means far from the usual level for that weekday. Holidays, weather, events or data gaps "
        "can "
        "explain it; it is not evidence of error or fraud, and it does not show a cause."
    )
    return ToolOutput({"unusual_days": unusual, "days_checked": len(points), "threshold": 3.5}, view, meta)


def chart_tool(ctx: ToolContext, args: ChartArgs) -> ToolOutput:
    """An allowlisted chart spec whose data comes from a query, never from the model."""
    base = args.model_dump(include=set(ToolFilters.model_fields))
    if args.dimension in ("day", "month"):
        out = time_series_tool(ctx, TimeSeriesArgs(**base, metric=args.metric, granularity=args.dimension))
    elif args.dimension in ("pickup_zone", "dropoff_zone"):
        out = top_zones_tool(
            ctx,
            TopZonesArgs(
                **base, zone_type=args.dimension.split("_")[0], metric=args.metric, limit=args.limit
            ),
        )
    else:
        out = breakdown_tool(ctx, BreakdownArgs(**base, dimension=args.dimension, metric=args.metric))
    assert out.chart is not None
    out.chart["type"] = args.chart_type
    out.view = (
        f"Chart created ({args.chart_type} of {args.metric} by {args.dimension}); the user sees it under "
        f"the answer.\n" + out.view
    )
    return out


def report_draft_tool(ctx: ToolContext, args: ReportDraftArgs) -> ToolOutput:
    if ctx.user.role not in (Role.ADMIN, Role.ANALYST):
        raise PermissionDeniedError("only analysts and administrators can create reports")
    if ctx.create_report is None:
        raise ValidationFailedError("report drafting is not available here")
    filters = args.analytics(ctx.dataset_id)
    report = ctx.create_report(
        template=args.report_type, title=args.title, filters=filters, sections=args.selected_sections
    )
    meta: dict[str, Any] = {
        "period": _period_label({}, filters),
        "filters": filters.applied(),
        "metrics": [],
        "source_table": None,
        "query_ms": None,
    }
    view = (
        f"Report created and saved (not exported): id {report['report_id']}, title {report['title']!r}, "
        f"template {TEMPLATES[args.report_type].title}. The user can open it at "
        f"/reports/{report['report_id']} to "
        "preview, edit and export. Nothing has been exported or sent."
    )
    return ToolOutput(
        {
            "report_id": report["report_id"],
            "title": report["title"],
            "url": f"/reports/{report['report_id']}",
        },
        view,
        meta,
    )


def find_zones_tool(ctx: ToolContext, args: FindZonesArgs) -> ToolOutput:
    needle = args.query.strip().lower()
    matches = [
        {"zone_id": zid, "zone": z["zone"], "borough": z["borough"]}
        for zid, z in sorted(ctx.analytics.zones().items())
        if needle in str(z["zone"]).lower() or needle in str(z["borough"]).lower()
    ][:10]
    meta: dict[str, Any] = {
        "period": "",
        "filters": {},
        "metrics": [],
        "source_table": "taxi_zones",
        "query_ms": None,
    }
    view = f"find_zones({args.query!r}) — {len(matches)} match(es)\n" + _table(
        ["zone_id", "zone", "borough"],
        [[str(m["zone_id"]), str(m["zone"]), str(m["borough"])] for m in matches],
    )
    return ToolOutput({"zones": matches}, view, meta)


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "get_overview_metrics",
            "Headline KPIs (trips, trips per day, amounts, distance, duration) for a period and filters.",
            OverviewArgs,
            overview_tool,
        ),
        Tool(
            "get_time_series",
            "A metric over time by day, month or hour.",
            TimeSeriesArgs,
            time_series_tool,
            chartable=True,
        ),
        Tool(
            "compare_periods",
            "Every KPI for two periods side by side with the change from A to B.",
            ComparePeriodsArgs,
            compare_periods_tool,
        ),
        Tool(
            "get_top_zones",
            "Busiest pickup or drop-off zones with trips and share.",
            TopZonesArgs,
            top_zones_tool,
            chartable=True,
        ),
        Tool(
            "get_breakdown",
            "A metric by hour, weekday (with weekday vs weekend per day), payment type or vendor.",
            BreakdownArgs,
            breakdown_tool,
            chartable=True,
        ),
        Tool(
            "get_distribution",
            "Trip distance or total amount distribution: median and 90th percentile buckets, outliers.",
            DistributionArgs,
            distribution_tool,
            chartable=True,
        ),
        Tool(
            "get_data_quality_summary",
            "Rows read, published and quarantined, quarantine reasons and quality flags.",
            QualityArgs,
            quality_tool,
        ),
        Tool(
            "run_anomaly_analysis",
            "Unusual days for a metric against same-weekday baselines (robust z-score).",
            AnomalyArgs,
            anomaly_tool,
        ),
        Tool(
            "create_chart_spec",
            "Show the user a chart (line for day/month; bar or column otherwise).",
            ChartArgs,
            chart_tool,
            chartable=True,
        ),
        Tool(
            "create_report_draft",
            "Save a report (template, filters, sections) in the report center; it is not exported.",
            ReportDraftArgs,
            report_draft_tool,
        ),
        Tool(
            "find_zones",
            "Look up taxi zone IDs by name, e.g. 'JFK' or 'Midtown'.",
            FindZonesArgs,
            find_zones_tool,
        ),
    )
}


# ---- schemas for the model -------------------------------------------------------------------------------


# Filter fields repeat in most tools; their meaning is explained once in the system prompt (agent.py).
SHARED_FIELDS = frozenset(ToolFilters.model_fields)
KEEP = ("type", "enum", "const", "description", "format")


def _compact(schema: dict[str, Any], defs: dict[str, Any], *, describe: bool = True) -> dict[str, Any]:
    if "$ref" in schema:
        return _compact(defs[schema["$ref"].split("/")[-1]], defs, describe=describe)
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        merged = (
            _compact(options[0], defs, describe=describe)
            if len(options) == 1
            else {"anyOf": [_compact(s, defs, describe=describe) for s in options]}
        )
        if describe and "description" in schema:
            merged["description"] = schema["description"]
        return merged
    out = {k: v for k, v in schema.items() if k in KEEP and (describe or k != "description")}
    if "items" in schema:
        out["items"] = {
            k: v for k, v in _compact(schema["items"], defs).items() if k in ("type", "enum", "pattern")
        }
    if schema.get("type") == "object" and "properties" in schema:
        out["properties"] = {
            k: _compact(v, defs, describe=k not in SHARED_FIELDS) for k, v in schema["properties"].items()
        }
        if schema.get("required"):
            out["required"] = schema["required"]
    return out


def openai_tools() -> list[dict[str, Any]]:
    """Tool definitions in the OpenAI function-calling format, compacted for small context windows."""
    tools = []
    for tool in TOOLS.values():
        schema = tool.args.model_json_schema()
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": _compact(schema, schema.get("$defs", {})),
                },
            }
        )
    return tools


# ---- execution -------------------------------------------------------------------------------------------


@dataclass
class ToolRun:
    """One executed (or rejected) tool call, as logged in ai_tool_runs and shown as evidence."""

    tool: str
    arguments: dict[str, Any]
    status: Literal["ok", "error"]
    duration_ms: float
    output: ToolOutput | None = None
    error: str | None = None
    raw_arguments: str = ""
    call_id: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def model_message(self) -> str:
        if self.status == "error":
            return f"ERROR from {self.tool}: {self.error}"
        assert self.output is not None
        return self.output.view


def execute(
    ctx: ToolContext, name: str, raw_arguments: str | dict[str, Any], *, call_id: str = ""
) -> ToolRun:
    """Validate and run one tool call. Failures come back as an error run (never an exception)."""
    import json
    import time

    started = time.perf_counter()
    raw = raw_arguments if isinstance(raw_arguments, str) else json.dumps(raw_arguments)

    def failed(message: str, arguments: dict[str, Any] | None = None) -> ToolRun:
        return ToolRun(
            name,
            arguments or {},
            "error",
            round((time.perf_counter() - started) * 1000, 1),
            error=message,
            raw_arguments=raw[:2000],
            call_id=call_id,
        )

    tool = TOOLS.get(name)
    if tool is None:
        return failed(f"there is no tool named {name!r}; use one of: {', '.join(TOOLS)}")
    try:
        parsed = json.loads(raw or "{}") if isinstance(raw_arguments, str) else raw_arguments
        if not isinstance(parsed, dict):
            return failed("arguments must be a JSON object")
        args = tool.args.model_validate(parsed)
    except json.JSONDecodeError:
        return failed("the arguments are not valid JSON")
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()[:5]
        )
        return failed(f"invalid arguments ({problems})")
    arguments = args.model_dump(mode="json", exclude_defaults=True)
    try:
        output = tool.run(ctx, args)
    except TripScopeError as exc:
        return failed(exc.message, arguments)
    duration = round((time.perf_counter() - started) * 1000, 1)
    return ToolRun(name, arguments, "ok", duration, output=output, raw_arguments=raw[:2000], call_id=call_id)


def numbers_in(value: Any) -> list[float]:
    """Every finite number in a tool result (booleans excluded), for the numeric-claim verifier."""
    return [v for group in number_groups(value).values() for v in group]


def number_groups(
    value: Any, key: str = "", groups: dict[str, list[float]] | None = None
) -> dict[str, list[float]]:
    """Numbers grouped by the field they sit under (`trips`, `value`, `period_a` …): like quantities, which
    the verifier may compare with each other."""
    groups = {} if groups is None else groups
    if isinstance(value, bool) or value is None:
        return groups
    if isinstance(value, int | float):
        if math.isfinite(float(value)):
            groups.setdefault(key, []).append(float(value))
    elif isinstance(value, dict):
        for k, v in value.items():
            # Change/share fields compare with nothing; period values pair across the A and B columns.
            number_groups(v, "period" if k in ("period_a", "period_b") else k, groups)
    elif isinstance(value, list | tuple):
        for v in value:
            number_groups(v, key, groups)
    return groups


def default_period(coverage_end: date | None, days: int = 30) -> tuple[date | None, date | None]:
    if coverage_end is None:
        return None, None
    return coverage_end - timedelta(days=days - 1), coverage_end
