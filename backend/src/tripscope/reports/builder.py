"""Build a report document from the shared analytics service (FR-09, ADR-18).

Numbers come only from analytics results. The executive summary and findings are fixed sentence rules filled
with those values, and each finding keeps the values and source it was built from (its evidence). Nothing here
estimates, extrapolates or writes a figure by hand.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from tripscope.analytics.filters import AnalyticsFilters, TimeSeriesQuery
from tripscope.analytics.labels import (
    FLAG_LABELS,
    PAYMENT_TYPES,
    QUARANTINE_LABELS,
    VENDORS,
    WEEKDAYS,
    code_label,
)
from tripscope.analytics.metrics import CASH_TIP_CAVEAT, METRICS
from tripscope.analytics.service import AnalyticsService, Coverage
from tripscope.core.errors import ValidationFailedError
from tripscope.reports import formatting as f
from tripscope.reports.document import (
    ChartBlock,
    Column,
    Comparison,
    DatasetInfo,
    DatasetPeriod,
    Evidence,
    FilterItem,
    Finding,
    Kpi,
    Marker,
    Period,
    ReportDocument,
    Section,
    Series,
    TableBlock,
)
from tripscope.reports.templates import TEMPLATES, SectionSpec, TemplateSpec

DAILY_TREND_MAX_DAYS = 400
TOP_ZONES = 10
TOP_PAIRS = 15
MIN_GROUP_TRIPS = 1000  # comparisons of averages ignore groups smaller than this (stated in the finding)


WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _hour(value: int) -> str:
    return f"{int(value):02d}:00"


def _day(value: Any) -> date:
    return (
        value
        if isinstance(value, date) and not isinstance(value, datetime)
        else date.fromisoformat(str(value)[:10])
    )


def _evidence(value: Any) -> Any:
    """Evidence values as stored: dates as ISO text, floats to 4 decimals."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 4)
    return value


@dataclass
class _Context:
    template: TemplateSpec
    filters: AnalyticsFilters
    coverage: Coverage
    start: date
    end: date
    overview: dict[str, Any]
    total_trips: int
    zones: dict[int, dict[str, Any]]
    cache: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def day_label(self, day: date) -> str:
        """Axis label for a day: 'Mar 7' when the report sits within one year, else 'Mar 7, 2025'."""
        return f"{day:%b} {day.day}" if self.start.year == self.end.year else f.fmt_date(day)

    def finding(
        self,
        section: str,
        statement: str,
        *,
        source: str,
        source_table: str | None,
        values: dict[str, Any],
        caveat: str | None = None,
    ) -> None:
        self.findings.append(
            Finding(
                id=f"{section}-{sum(1 for x in self.findings if x.section == section) + 1}",
                section=section,
                statement=statement,
                evidence=Evidence(
                    source=source,
                    source_table=source_table,
                    values={k: _evidence(v) for k, v in values.items()},
                ),
                caveat=caveat,
            )
        )


class ReportBuilder:
    def __init__(self, analytics: AnalyticsService) -> None:
        self.analytics = analytics

    # ---- entry point ---------------------------------------------------------------------------------------

    def build(
        self,
        *,
        template: str,
        title: str,
        filters: AnalyticsFilters,
        sections: list[str],
        prepared_by: str,
        generated_at: datetime | None = None,
    ) -> ReportDocument:
        spec = TEMPLATES.get(template)
        if spec is None:
            raise ValidationFailedError(f"unknown report template {template!r}")
        coverage = self.analytics.coverage(filters.dataset_id)
        if not coverage.periods or coverage.start is None or coverage.end is None:
            raise ValidationFailedError("this dataset has no published data yet; run the pipeline first")
        start, end = filters.start_date or coverage.start, filters.end_date or coverage.end
        if start > end:
            raise ValidationFailedError("the start date is after the end date")
        compare = "previous" if filters.start_date and filters.end_date else "none"
        overview = self.analytics.overview(filters, compare=compare)
        if overview["data_state"] != "ok":
            raise ValidationFailedError(
                "no trips match this report's period and filters; widen the dates or remove filters"
            )
        ctx = _Context(
            template=spec,
            filters=filters,
            coverage=coverage,
            start=start,
            end=end,
            overview=overview,
            total_trips=int(overview["kpis"]["total_trips"]["value"]),
            zones=self.analytics.zones(),
        )
        enabled = [s for s in spec.sections if s.id in set(sections)]
        built = [self._section(ctx, s) for s in enabled]
        kpis = self._quality_kpis(ctx) if spec.id == "data_quality" else self._kpis(ctx)
        comparison = self._comparison(ctx)
        result_range = overview.get("result_range") or {}
        period = Period(
            start=start,
            end=end,
            days=(end - start).days + 1,
            label=f.date_range(start, end),
            data_first=result_range.get("first_date"),
            data_last=result_range.get("last_date"),
        )
        dataset = self._dataset(ctx)
        return ReportDocument(
            template=spec.id,
            template_title=spec.title,
            title=title,
            period=period,
            filters=self._filter_items(ctx),
            applied_filters=filters.applied(),
            dataset=dataset,
            generated_at=generated_at or datetime.now(UTC),
            prepared_by=prepared_by,
            summary=self._summary(ctx, period),
            kpis=kpis,
            comparison=comparison,
            sections=[s for s in built if s.blocks],
            findings=ctx.findings,
            methodology=self._methodology(ctx, kpis),
            limitations=self._limitations(ctx),
        )

    # ---- frame ---------------------------------------------------------------------------------------------

    def _dataset(self, ctx: _Context) -> DatasetInfo:
        periods = [
            p for p in ctx.coverage.periods if p.max_pickup_date >= ctx.start and p.min_pickup_date <= ctx.end
        ]
        fingerprint = ";".join(f"{p.data_period:%Y-%m}:{p.run_id}" for p in periods)
        return DatasetInfo(
            id=ctx.coverage.dataset_id,
            name=ctx.coverage.dataset_name,
            attribution=ctx.coverage.source_attribution,
            version_id=hashlib.sha256(fingerprint.encode()).hexdigest()[:12],
            periods=[
                DatasetPeriod(
                    period=f"{p.data_period:%Y-%m}",
                    run_id=str(p.run_id),
                    row_count=p.row_count,
                    published_at=p.published_at,
                )
                for p in periods
            ],
        )

    def _filter_items(self, ctx: _Context) -> list[FilterItem]:
        flt = ctx.filters
        all_data = not (flt.start_date or flt.end_date)
        items = [
            FilterItem(label="Dataset", value=ctx.coverage.dataset_name),
            FilterItem(
                label="Pickup dates",
                value=f"All published data ({f.date_range(ctx.start, ctx.end)})"
                if all_data
                else f.date_range(ctx.start, ctx.end),
            ),
        ]

        def zones(ids: list[int]) -> str:
            names = [self._zone_name(ctx, z) for z in ids[:5]]
            return ", ".join(names) + (f" and {len(ids) - 5} more" if len(ids) > 5 else "")

        if flt.pickup_zone:
            items.append(FilterItem(label="Pickup zone", value=zones(flt.pickup_zone)))
        if flt.dropoff_zone:
            items.append(FilterItem(label="Drop-off zone", value=zones(flt.dropoff_zone)))
        if flt.payment_type:
            labels = [code_label(PAYMENT_TYPES, p, "Payment type") for p in flt.payment_type]
            items.append(FilterItem(label="Payment type", value=", ".join(labels)))
        if flt.vendor_id:
            labels = [code_label(VENDORS, v, "Vendor") for v in flt.vendor_id]
            items.append(FilterItem(label="Vendor", value=", ".join(labels)))
        if flt.weekday:
            items.append(
                FilterItem(label="Weekday", value=", ".join(WEEKDAYS[d - 1] for d in sorted(flt.weekday)))
            )
        if flt.hour:
            items.append(FilterItem(label="Pickup hour", value=", ".join(_hour(h) for h in sorted(flt.hour))))
        if flt.min_distance is not None or flt.max_distance is not None:
            low, high = flt.min_distance, flt.max_distance
            text = (
                f"{low:g}–{high:g} mi"
                if low is not None and high is not None
                else f"at least {low:g} mi"
                if low is not None
                else f"up to {high:g} mi"
            )
            items.append(FilterItem(label="Trip distance", value=text))
        return items

    def _zone_name(self, ctx: _Context, zone_id: int | None) -> str:
        zone = ctx.zones.get(int(zone_id)) if zone_id is not None else None
        if zone and zone["is_geographic"]:
            return f"{zone['zone']} ({zone['borough']})"
        return f"Unmapped zone {zone_id}"

    def _kpis(self, ctx: _Context) -> list[Kpi]:
        kpis = ctx.overview["kpis"]
        previous = (ctx.overview.get("comparison") or {}).get("kpis") or {}
        result = []
        for metric_id in ctx.template.kpis:
            definition, current = METRICS[metric_id], kpis[metric_id]
            before = (previous.get(metric_id) or {}).get("value") if previous else None
            ratio = f.change(current["value"], before)
            result.append(
                Kpi(
                    id=metric_id,
                    label=definition.label,
                    unit=definition.unit,
                    value=current["value"],
                    display=f.display(current["value"], definition.unit),
                    previous=before,
                    previous_display=f.display(before, definition.unit) if previous else None,
                    change=ratio,
                    change_display=f.signed_change(ratio) if ratio is not None else None,
                    excluded_rows=int(current["excluded_rows"]),
                    note=f"Excludes {f.integer(current['excluded_rows'])} flagged trips"
                    if current["excluded_rows"]
                    else None,
                )
            )
        if previous and ctx.template.kpis:
            current_trips = kpis["total_trips"]["value"]
            before_trips = previous["total_trips"]["value"]
            ratio = f.change(current_trips, before_trips)
            if ratio is not None:
                comparison = ctx.overview["comparison"]
                direction = "rose" if ratio > 0 else "fell" if ratio < 0 else "were unchanged"
                amount = f" {f.signed_change(abs(ratio)).lstrip('+')}" if ratio else ""
                ctx.findings.insert(
                    0,
                    Finding(
                        id="kpis-1",
                        section="kpis",
                        statement=(
                            f"Trips {direction}{amount} versus the previous {comparison['days']} days "
                            f"({f.integer(before_trips)} → {f.integer(current_trips)})."
                        ),
                        evidence=Evidence(
                            source="Overview KPIs with previous-period comparison",
                            source_table=ctx.overview["meta"].get("source_table"),
                            values={
                                "trips": current_trips,
                                "previous_trips": before_trips,
                                "change": round(ratio, 6),
                                "previous_start": str(comparison["start_date"]),
                                "previous_end": str(comparison["end_date"]),
                            },
                        ),
                        caveat=(
                            "The previous period uses the same filters; a change is descriptive, not a cause."
                        ),
                    ),
                )
        return result

    def _comparison(self, ctx: _Context) -> Comparison:
        if not ctx.template.kpis:  # the data-quality report has no trip KPIs to compare
            return Comparison(available=False)
        comparison = ctx.overview.get("comparison")
        if not comparison:
            return Comparison(available=False, reason="Choose a start and end date to compare periods.")
        if not comparison.get("available"):
            return Comparison(available=False, reason=comparison.get("reason"))
        start, end = comparison["start_date"], comparison["end_date"]
        return Comparison(
            available=True,
            label=f"Previous {comparison['days']} days ({f.date_range(start, end)})",
            start=start,
            end=end,
        )

    def _summary(self, ctx: _Context, period: Period) -> list[str]:
        if ctx.template.id == "data_quality":
            quality = self._quality(ctx)
            totals = quality["totals"]
            rate = f.percent(f.share(totals["quarantined_rows"], totals["input_rows"]))
            lines = [
                f"The pipeline read {f.integer(totals['input_rows'])} rows from "
                f"{f.plural(len(quality['periods']), 'monthly file')} covering {period.label} and published "
                f"{f.integer(totals['accepted_rows'])}; {f.integer(totals['quarantined_rows'])} ({rate}) "
                "were quarantined."
            ]
        else:
            kpis = ctx.overview["kpis"]
            lines = [
                f"{f.integer(ctx.total_trips)} trips were recorded {f.span_words(ctx.start, ctx.end)}, "
                f"{f.integer(kpis['avg_daily_trips']['value'])} per day on average."
            ]
        seen: set[str] = set()
        for finding in ctx.findings:
            if finding.section not in seen and len(lines) < 5:
                seen.add(finding.section)
                lines.append(finding.statement)
        return lines

    def _methodology(self, ctx: _Context, kpis: list[Kpi]) -> list[str]:
        lines = [
            f"Figures come from TripScope's published {ctx.coverage.dataset_name} data — the monthly runs "
            f"listed "
            "under Dataset version — through the same analytics service, filters and metric definitions as "
            "the "
            "dashboard. Re-running this report on the same version gives the same numbers.",
            "The executive summary and findings are fixed sentence rules filled with the returned values; no "
            "figure is estimated or written by hand. Each finding lists the values it rests on.",
            "Pickup dates and hours are NYC local wall-clock time as recorded by TLC.",
        ]
        for kpi in kpis:
            definition = METRICS.get(kpi.id)
            if definition:
                lines.append(f"{definition.label}: {definition.description} {definition.rows_included}")
        sections = {s.id for s in ctx.template.sections}
        if sections & {"distance_distribution", "amount_distribution"}:
            lines.append(
                "Distributions use 1-mile and $5 buckets; trips above 50 miles or $200 are grouped in the "
                "last "
                "bucket. The median and 90th percentile are reported as the bucket that contains them."
            )
        if ctx.template.id == "data_quality":
            lines.append(
                "Run-level counts come from the processing run that published each month; daily flag rates "
                "come "
                "from the published fact table."
            )
        return lines

    def _limitations(self, ctx: _Context) -> list[str]:
        lines = [
            "TLC trip records are operational data: they contain anomalies and are sometimes revised. An "
            "unusual "
            "record is not evidence of fraud.",
            "Trips with an implausible distance, duration or amount stay in the data but are excluded from "
            "the "
            "metrics that depend on that value; the excluded counts are shown with each metric.",
            "Zones 264 and 265 (unknown or outside NYC) have no geography and are shown as unmapped.",
        ]
        if ctx.template.id != "data_quality":
            lines.insert(1, CASH_TIP_CAVEAT)
        else:
            lines.append(
                "Run-level quality figures cover whole months and ignore zone, payment, vendor, time and "
                "distance "
                "filters."
            )
        if ctx.overview.get("comparison", {}).get("available"):
            lines.append("Period comparisons describe differences; they do not establish causes.")
        return lines

    # ---- sections ------------------------------------------------------------------------------------------

    def _section(self, ctx: _Context, spec: SectionSpec) -> Section:
        handler = getattr(self, f"_s_{spec.id}")
        blocks = handler(ctx, spec)
        return Section(id=spec.id, title=spec.title, description=spec.description, blocks=blocks)

    def _daily(self, ctx: _Context) -> dict[str, Any]:
        if "daily" not in ctx.cache:
            query = TimeSeriesQuery(**ctx.filters.model_dump(), metric="total_trips", granularity="day")
            ctx.cache["daily"] = self.analytics.time_series(query)
        return dict(ctx.cache["daily"])

    def _breakdown(self, ctx: _Context, dimension: str, metric: str = "total_trips", limit: int = 300) -> Any:
        key = f"breakdown:{dimension}:{metric}:{limit}"
        if key not in ctx.cache:
            ctx.cache[key] = self.analytics.breakdown(
                ctx.filters, metric=metric, dimension=dimension, limit=limit
            )
        return ctx.cache[key]

    def _quality(self, ctx: _Context) -> dict[str, Any]:
        """Quality metrics for the months the report covers (the service returns every published month)."""
        if "quality" not in ctx.cache:
            result = self.analytics.quality(ctx.filters)
            covered = {p.period for p in self._dataset(ctx).periods}
            periods = [p for p in result["periods"] if p["period"] in covered]
            totals: dict[str, Any] = {
                "input_rows": 0,
                "accepted_rows": 0,
                "quarantined_rows": 0,
                "duplicate_rows": 0,
                "quarantine_reasons": defaultdict(int),
                "flags": defaultdict(int),
            }
            for p in periods:
                for k in ("input_rows", "accepted_rows", "quarantined_rows", "duplicate_rows"):
                    totals[k] += p[k]
                for name, count in p["quarantine_reasons"].items():
                    totals["quarantine_reasons"][name] += int(count)
                for name, count in p["flags"].items():
                    totals["flags"][name] += int(count)
            ctx.cache["quality"] = {**result, "periods": periods, "totals": totals}
        return dict(ctx.cache["quality"])

    def _s_trend(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        days = (ctx.end - ctx.start).days + 1
        daily = self._daily(ctx)
        points = daily["points"]
        if days <= DAILY_TREND_MAX_DAYS:
            categories = [ctx.day_label(_day(p["bucket"])) for p in points]
            values: list[float | None] = [p["trips"] for p in points]
            title = "Trips per day"
        else:
            query = TimeSeriesQuery(**ctx.filters.model_dump(), metric="total_trips", granularity="month")
            monthly = self.analytics.time_series(query)["points"]
            categories = [str(p["bucket"])[:7] for p in monthly]
            values = [p["trips"] for p in monthly]
            title = "Trips per month"
        blocks: list[Any] = [
            ChartBlock(
                id="trend",
                title=title,
                kind="line",
                unit="trips",
                categories=categories,
                series=[Series(name="Trips", values=values)],
            )
        ]
        if points:
            busiest = max(points, key=lambda p: p["trips"])
            quietest = min(points, key=lambda p: p["trips"])
            ctx.finding(
                spec.id,
                f"The busiest day was {f.fmt_date(_day(busiest['bucket']))} with "
                f"{f.integer(busiest['trips'])} "
                f"trips; the quietest was {f.fmt_date(_day(quietest['bucket']))} with "
                f"{f.integer(quietest['trips'])}.",
                source="Trips per day",
                source_table=daily["meta"].get("source_table"),
                values={
                    "busiest_date": _day(busiest["bucket"]),
                    "busiest_trips": busiest["trips"],
                    "quietest_date": _day(quietest["bucket"]),
                    "quietest_trips": quietest["trips"],
                },
                caveat="Partial days at the edges of the published data count as days.",
            )
        return blocks

    def _s_weekday(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        daily = self._daily(ctx)
        trips_by_day: dict[int, int] = defaultdict(int)
        days_by_day: dict[int, int] = defaultdict(int)
        for point in daily["points"]:
            weekday = _day(point["bucket"]).isoweekday()
            trips_by_day[weekday] += point["trips"]
            days_by_day[weekday] += 1
        rows: list[list[Any]] = []
        averages: dict[int, float] = {}
        for weekday in range(1, 8):
            if not days_by_day[weekday]:
                continue
            averages[weekday] = trips_by_day[weekday] / days_by_day[weekday]
            rows.append(
                [
                    WEEKDAY_NAMES[weekday - 1],
                    trips_by_day[weekday],
                    f.share(trips_by_day[weekday], ctx.total_trips),
                    days_by_day[weekday],
                    round(averages[weekday], 1),
                ]
            )
        blocks: list[Any] = [
            ChartBlock(
                id="weekday-chart",
                title="Average trips per day by weekday",
                kind="column",
                unit="trips",
                categories=[r[0] for r in rows],
                series=[Series(name="Trips per day", values=[r[4] for r in rows])],
            ),
            TableBlock.build(
                id="weekday-table",
                title="Trips by weekday",
                columns=[
                    Column(key="weekday", label="Weekday"),
                    Column(key="trips", label="Trips", unit="trips"),
                    Column(key="share", label="Share of trips", unit="share"),
                    Column(key="days", label="Days with data", unit="count"),
                    Column(key="per_day", label="Trips per day", unit="trips"),
                ],
                rows=rows,
            ),
        ]
        if averages:
            top = max(averages, key=lambda d: averages[d])
            low = min(averages, key=lambda d: averages[d])
            values: dict[str, Any] = {
                "busiest_weekday": WEEKDAY_NAMES[top - 1],
                "busiest_per_day": round(averages[top], 1),
                "quietest_weekday": WEEKDAY_NAMES[low - 1],
                "quietest_per_day": round(averages[low], 1),
            }
            statement = (
                f"{WEEKDAY_NAMES[top - 1]} was the busiest weekday at {f.integer(averages[top])} trips per "
                f"day; {WEEKDAY_NAMES[low - 1]} was the quietest at {f.integer(averages[low])}."
            )
            weekend = [d for d in averages if d >= 6]
            weekdays = [d for d in averages if d <= 5]
            if weekend and weekdays:
                we = sum(trips_by_day[d] for d in weekend) / sum(days_by_day[d] for d in weekend)
                wd = sum(trips_by_day[d] for d in weekdays) / sum(days_by_day[d] for d in weekdays)
                ratio = f.change(we, wd)
                if ratio is not None:
                    statement += (
                        f" Weekend days averaged {f.integer(we)} trips, "
                        f"{f.percent(abs(ratio))} {'more' if ratio > 0 else 'fewer'} than weekdays "
                        f"({f.integer(wd)})."
                    )
                    values |= {"weekend_per_day": round(we, 1), "weekday_per_day": round(wd, 1)}
            ctx.finding(
                spec.id,
                statement,
                source="Trips per day, grouped by weekday",
                source_table=daily["meta"].get("source_table"),
                values=values,
                caveat="Averages are over days that have data in the selection.",
            )
        return blocks

    def _zone_table(self, ctx: _Context, spec: SectionSpec, side: str) -> list[Any]:
        result = self._breakdown(ctx, f"{side}_zone", limit=TOP_ZONES)
        groups = result["groups"]
        rows = [
            [i + 1, g["label"], g.get("borough") or "—", g["trips"], f.share(g["trips"], ctx.total_trips)]
            for i, g in enumerate(groups)
        ]
        noun = "pickup" if side == "pickup" else "drop-off"
        blocks: list[Any] = [
            ChartBlock(
                id=f"{side}-zones-chart",
                title=f"Top {len(groups)} {noun} zones",
                kind="bar",
                unit="trips",
                categories=[g["label"] for g in groups],
                series=[Series(name="Trips", values=[g["trips"] for g in groups])],
            ),
            TableBlock.build(
                id=f"{side}-zones-table",
                title=f"Top {noun} zones",
                columns=[
                    Column(key="rank", label="#", unit="count"),
                    Column(key="zone", label="Zone"),
                    Column(key="borough", label="Borough"),
                    Column(key="trips", label="Trips", unit="trips"),
                    Column(key="share", label="Share of trips", unit="share"),
                ],
                rows=rows,
            ),
        ]
        if groups:
            top = groups[0]
            combined = sum(g["trips"] for g in groups)
            ctx.finding(
                spec.id,
                f"{top['label']} was the top {noun} zone with {f.integer(top['trips'])} trips "
                f"({f.percent(f.share(top['trips'], ctx.total_trips))}); the top {len(groups)} zones "
                f"together "
                f"account for {f.percent(f.share(combined, ctx.total_trips))} of trips.",
                source=f"Top {noun} zones",
                source_table=result["meta"].get("source_table"),
                values={
                    "zone_id": top["key"],
                    "zone": top["label"],
                    "trips": top["trips"],
                    "share": round(f.share(top["trips"], ctx.total_trips) or 0, 6),
                    f"top_{len(groups)}_trips": combined,
                },
            )
        return blocks

    def _s_top_pickup_zones(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        return self._zone_table(ctx, spec, "pickup")

    def _s_top_dropoff_zones(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        return self._zone_table(ctx, spec, "dropoff")

    def _payment_rows(self, ctx: _Context) -> tuple[list[list[Any]], dict[str, Any]]:
        trips = self._breakdown(ctx, "payment_type")
        amount = {
            g["key"]: g["value"] for g in self._breakdown(ctx, "payment_type", "avg_total_amount")["groups"]
        }
        distance = {
            g["key"]: g["value"] for g in self._breakdown(ctx, "payment_type", "avg_trip_distance")["groups"]
        }
        rows = [
            [
                g["label"],
                g["trips"],
                f.share(g["trips"], ctx.total_trips),
                amount.get(g["key"]),
                distance.get(g["key"]),
            ]
            for g in trips["groups"]
        ]
        return rows, trips

    def _payment_blocks(self, ctx: _Context, rows: list[list[Any]]) -> list[Any]:
        return [
            ChartBlock(
                id="payment-chart",
                title="Trips by payment type",
                kind="bar",
                unit="trips",
                categories=[r[0] for r in rows],
                series=[Series(name="Trips", values=[r[1] for r in rows])],
            ),
            TableBlock.build(
                id="payment-table",
                title="Payment types",
                columns=[
                    Column(key="payment_type", label="Payment type"),
                    Column(key="trips", label="Trips", unit="trips"),
                    Column(key="share", label="Share of trips", unit="share"),
                    Column(key="avg_total_amount", label="Avg total amount", unit="usd"),
                    Column(key="avg_trip_distance", label="Avg distance", unit="miles"),
                ],
                rows=rows,
                note=CASH_TIP_CAVEAT,
            ),
        ]

    def _s_payment_mix(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        rows, trips = self._payment_rows(ctx)
        if rows:
            top = rows[0]
            ctx.finding(
                spec.id,
                f"{top[0]} paid for {f.percent(top[2])} of trips ({f.integer(top[1])}), with an average "
                f"total "
                f"amount of {f.display(top[3], 'usd')}.",
                source="Trips and average total amount by payment type",
                source_table=trips["meta"].get("source_table"),
                values={
                    "payment_type": top[0],
                    "trips": top[1],
                    "share": round(top[2] or 0, 6),
                    "avg_total_amount": top[3],
                },
                caveat=CASH_TIP_CAVEAT,
            )
        return self._payment_blocks(ctx, rows)

    def _s_payment_types(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        rows, trips = self._payment_rows(ctx)
        eligible = [r for r in rows if r[1] >= MIN_GROUP_TRIPS and r[3] is not None]
        if eligible:
            top = max(eligible, key=lambda r: r[3])
            ctx.finding(
                spec.id,
                f"Among payment types with at least {f.integer(MIN_GROUP_TRIPS)} trips, {top[0]} had the "
                f"highest "
                f"average total amount at {f.display(top[3], 'usd')} ({f.integer(top[1])} trips).",
                source="Average total amount by payment type",
                source_table=trips["meta"].get("source_table"),
                values={"payment_type": top[0], "avg_total_amount": top[3], "trips": top[1]},
                caveat=CASH_TIP_CAVEAT,
            )
        return self._payment_blocks(ctx, rows)

    def _s_quality_snapshot(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        quality = self._quality(ctx)
        totals = quality["totals"]
        rows: list[list[Any]] = [
            ["Rows read", totals["input_rows"], None],
            [
                "Rows published",
                totals["accepted_rows"],
                f.share(totals["accepted_rows"], totals["input_rows"]),
            ],
            [
                "Rows quarantined",
                totals["quarantined_rows"],
                f.share(totals["quarantined_rows"], totals["input_rows"]),
            ],
        ]
        flags = sorted(totals["flags"].items(), key=lambda kv: -kv[1])[:3]
        for name, count in flags:
            rows.append(
                [f"Flag: {FLAG_LABELS.get(name, name)}", count, f.share(count, totals["accepted_rows"])]
            )
        blocks: list[Any] = [
            TableBlock.build(
                id="quality-snapshot",
                title="Data quality for the months covered",
                columns=[
                    Column(key="measure", label="Measure"),
                    Column(key="rows", label="Rows", unit="rows"),
                    Column(key="share", label="Share", unit="share"),
                ],
                rows=rows,
                note=(
                    "Whole published months that overlap the period; flags mark kept trips with a suspect "
                    "value."
                ),
            )
        ]
        if totals["input_rows"]:
            statement = (
                f"{f.integer(totals['quarantined_rows'])} of {f.integer(totals['input_rows'])} rows read "
                f"({f.percent(f.share(totals['quarantined_rows'], totals['input_rows']))}) were quarantined"
            )
            values: dict[str, Any] = {
                "rows_read": totals["input_rows"],
                "rows_quarantined": totals["quarantined_rows"],
            }
            if flags:
                statement += (
                    f"; the commonest flag, {FLAG_LABELS.get(flags[0][0], flags[0][0]).lower()}, marks "
                    f"{f.integer(flags[0][1])} kept trips"
                )
                values |= {"top_flag": flags[0][0], "top_flag_trips": flags[0][1]}
            ctx.finding(
                spec.id,
                statement + ".",
                source="Processing-run quality metrics",
                source_table=None,
                values=values,
            )
        return blocks

    def _s_hour(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        result = self._breakdown(ctx, "hour")
        groups = result["groups"]
        blocks: list[Any] = [
            ChartBlock(
                id="hour-chart",
                title="Trips by pickup hour",
                kind="column",
                unit="trips",
                categories=[_hour(g["key"]) for g in groups],
                series=[Series(name="Trips", values=[g["trips"] for g in groups])],
            )
        ]
        if groups:
            peak = max(groups, key=lambda g: g["trips"])
            low = min(groups, key=lambda g: g["trips"])
            ctx.finding(
                spec.id,
                f"Demand peaked in the {_hour(peak['key'])} hour with {f.integer(peak['trips'])} trips "
                f"({f.percent(f.share(peak['trips'], ctx.total_trips))}); the quietest hour was "
                f"{_hour(low['key'])} with {f.integer(low['trips'])}.",
                source="Trips by pickup hour",
                source_table=result["meta"].get("source_table"),
                values={
                    "peak_hour": peak["key"],
                    "peak_trips": peak["trips"],
                    "quiet_hour": low["key"],
                    "quiet_trips": low["trips"],
                },
            )
        return blocks

    def _matrix(self, ctx: _Context) -> dict[str, Any]:
        if "matrix" not in ctx.cache:
            ctx.cache["matrix"] = self.analytics.hour_weekday_matrix(ctx.filters, metric="total_trips")
        return dict(ctx.cache["matrix"])

    def _s_heatmap(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        result = self._matrix(ctx)
        grid: list[list[float | None]] = [[None] * 24 for _ in range(7)]
        for cell in result["cells"]:
            grid[cell["weekday"] - 1][cell["hour"]] = cell["trips"]
        blocks: list[Any] = [
            ChartBlock(
                id="heatmap",
                title="Trips by weekday and pickup hour",
                kind="heatmap",
                unit="trips",
                categories=[f"{h:02d}" for h in range(24)],
                rows=list(WEEKDAYS),
                matrix=grid,
            )
        ]
        if result["cells"]:
            top = max(result["cells"], key=lambda c: c["trips"])
            ctx.finding(
                spec.id,
                f"The busiest slot was {WEEKDAY_NAMES[top['weekday'] - 1]} at {_hour(top['hour'])} with "
                f"{f.integer(top['trips'])} trips.",
                source="Trips by weekday and pickup hour",
                source_table=result["meta"].get("source_table"),
                values={
                    "weekday": WEEKDAY_NAMES[top["weekday"] - 1],
                    "hour": top["hour"],
                    "trips": top["trips"],
                },
            )
        return blocks

    def _s_peak_slots(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        result = self._matrix(ctx)
        top = sorted(result["cells"], key=lambda c: -c["trips"])[:10]
        rows = [
            [
                i + 1,
                WEEKDAY_NAMES[c["weekday"] - 1],
                _hour(c["hour"]),
                c["trips"],
                f.share(c["trips"], ctx.total_trips),
            ]
            for i, c in enumerate(top)
        ]
        blocks: list[Any] = [
            TableBlock.build(
                id="peak-slots",
                title="Ten busiest weekday-and-hour slots",
                columns=[
                    Column(key="rank", label="#", unit="count"),
                    Column(key="weekday", label="Weekday"),
                    Column(key="hour", label="Pickup hour"),
                    Column(key="trips", label="Trips", unit="trips"),
                    Column(key="share", label="Share of trips", unit="share"),
                ],
                rows=rows,
            )
        ]
        if top:
            combined = sum(c["trips"] for c in top)
            ctx.finding(
                spec.id,
                f"The ten busiest of the 168 weekday-and-hour slots hold "
                f"{f.percent(f.share(combined, ctx.total_trips))} of trips ({f.integer(combined)}).",
                source="Trips by weekday and pickup hour",
                source_table=result["meta"].get("source_table"),
                values={"top_10_trips": combined, "total_trips": ctx.total_trips},
            )
        return blocks

    def _distribution(self, ctx: _Context, spec: SectionSpec, metric: str) -> list[Any]:
        result = self.analytics.distribution(ctx.filters, metric=metric)
        unit = "mi" if metric == "trip_distance" else "usd"
        buckets, summary = result["buckets"], result["summary"]

        def label(start: float, end: float | None) -> str:
            def fmt(value: float) -> str:
                return f"{value:g}" if unit == "mi" else f"${value:g}"

            suffix = " mi" if unit == "mi" else ""
            return f"{fmt(start)}+{suffix}" if end is None else f"{fmt(start)}–{fmt(end)}{suffix}"

        cap = summary["cap"]
        cap_words = f"{cap:g} miles or longer" if unit == "mi" else f"${cap:g} or more"
        index = {b["start"]: i for i, b in enumerate(buckets)}
        markers = []
        for name, key in (("median", "median_bucket"), ("p90", "p90_bucket")):
            bucket = summary.get(key)
            if bucket and bucket["start"] in index:
                markers.append(Marker(index=index[bucket["start"]], label=name))
        noun = "Trip distance" if metric == "trip_distance" else "Total amount"
        blocks: list[Any] = [
            ChartBlock(
                id=f"{metric}-histogram",
                title=f"{noun} distribution",
                kind="histogram",
                unit="trips",
                categories=[label(b["start"], b["end"]) for b in buckets],
                series=[Series(name="Trips", values=[b["trips"] for b in buckets])],
                markers=markers,
            ),
            TableBlock.build(
                id=f"{metric}-summary",
                title=f"{noun} summary",
                columns=[
                    Column(key="measure", label="Measure"),
                    Column(key="trips", label="Trips", unit="trips"),
                ],
                rows=[
                    ["Counted (valid values)", summary["counted_trips"]],
                    [f"In the open-ended bucket ({label(summary['cap'], None)})", summary["above_cap_trips"]],
                    ["Excluded (flagged)", summary["excluded_trips"]],
                ],
                note=(
                    f"Median bucket: "
                    f"{label(**summary['median_bucket']) if summary['median_bucket'] else '—'}; "
                    f"90th percentile bucket: "
                    f"{label(**summary['p90_bucket']) if summary['p90_bucket'] else '—'}."
                ),
            ),
        ]
        if summary["median_bucket"] and summary["p90_bucket"]:
            ctx.finding(
                spec.id,
                f"The median {noun.lower()} falls in the {label(**summary['median_bucket'])} bucket and the "
                f"90th percentile in {label(**summary['p90_bucket'])}; "
                f"{f.plural(summary['above_cap_trips'], 'trip')} "
                f"{'was' if summary['above_cap_trips'] == 1 else 'were'} {cap_words}.",
                source=f"{noun} distribution",
                source_table=result["meta"].get("source_table"),
                values={
                    "median_bucket_start": summary["median_bucket"]["start"],
                    "p90_bucket_start": summary["p90_bucket"]["start"],
                    "above_cap_trips": summary["above_cap_trips"],
                    "counted_trips": summary["counted_trips"],
                    "excluded_trips": summary["excluded_trips"],
                },
                caveat=f"{f.integer(summary['excluded_trips'])} flagged trips are excluded.",
            )
        return blocks

    def _s_distance_distribution(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        return self._distribution(ctx, spec, "trip_distance")

    def _s_amount_distribution(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        return self._distribution(ctx, spec, "total_amount")

    def _s_amount_by_hour(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        result = self._breakdown(ctx, "hour", "avg_total_amount")
        groups = result["groups"]
        blocks: list[Any] = [
            ChartBlock(
                id="amount-by-hour",
                title="Average total amount by pickup hour",
                kind="column",
                unit="usd",
                categories=[_hour(g["key"]) for g in groups],
                series=[Series(name="Avg total amount", values=[g["value"] for g in groups])],
            )
        ]
        valued = [g for g in groups if g["value"] is not None and g["trips"] >= MIN_GROUP_TRIPS]
        if valued:
            high = max(valued, key=lambda g: g["value"])
            low = min(valued, key=lambda g: g["value"])
            ctx.finding(
                spec.id,
                f"The average total amount was highest at {_hour(high['key'])} "
                f"({f.display(high['value'], 'usd')}) and lowest at {_hour(low['key'])} "
                f"({f.display(low['value'], 'usd')}).",
                source="Average total amount by pickup hour",
                source_table=result["meta"].get("source_table"),
                values={
                    "high_hour": high["key"],
                    "high_value": high["value"],
                    "low_hour": low["key"],
                    "low_value": low["value"],
                },
                caveat=CASH_TIP_CAVEAT,
            )
        return blocks

    def _s_exclusions(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        kpis = ctx.overview["kpis"]
        reasons = {
            "total_recorded_amount": "Implausible amount",
            "avg_total_amount": "Implausible amount",
            "avg_trip_distance": "Implausible distance",
            "avg_trip_duration_minutes": "Implausible duration",
        }
        rows = [
            [
                METRICS[m].label,
                reasons[m],
                kpis[m]["excluded_rows"],
                f.share(kpis[m]["excluded_rows"], ctx.total_trips),
            ]
            for m in reasons
        ]
        blocks: list[Any] = [
            TableBlock.build(
                id="exclusions",
                title="Trips excluded from each metric",
                columns=[
                    Column(key="metric", label="Metric"),
                    Column(key="flag", label="Excluded because of"),
                    Column(key="trips", label="Trips excluded", unit="trips"),
                    Column(key="share", label="Share of trips", unit="share"),
                ],
                rows=rows,
            )
        ]
        amount, distance = (
            kpis["avg_total_amount"]["excluded_rows"],
            kpis["avg_trip_distance"]["excluded_rows"],
        )
        ctx.finding(
            spec.id,
            f"Amount metrics exclude {f.integer(amount)} trips "
            f"({f.percent(f.share(amount, ctx.total_trips))}) with "
            f"an implausible amount; distance metrics exclude {f.integer(distance)} "
            f"({f.percent(f.share(distance, ctx.total_trips))}).",
            source="Overview KPIs (excluded-row counts)",
            source_table=ctx.overview["meta"].get("source_table"),
            values={"amount_excluded": amount, "distance_excluded": distance, "total_trips": ctx.total_trips},
        )
        return blocks

    def _s_boroughs(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        result = self._breakdown(ctx, "pickup_zone")
        by_borough: dict[str, int] = defaultdict(int)
        for group in result["groups"]:
            by_borough[group.get("borough") or "Unmapped"] += group["trips"]
        ordered = sorted(by_borough.items(), key=lambda kv: -kv[1])
        rows = [[name, trips, f.share(trips, ctx.total_trips)] for name, trips in ordered]
        blocks: list[Any] = [
            ChartBlock(
                id="boroughs-chart",
                title="Pickups by borough",
                kind="bar",
                unit="trips",
                categories=[r[0] for r in rows],
                series=[Series(name="Trips", values=[r[1] for r in rows])],
            ),
            TableBlock.build(
                id="boroughs-table",
                title="Pickups by borough",
                columns=[
                    Column(key="borough", label="Borough"),
                    Column(key="trips", label="Trips", unit="trips"),
                    Column(key="share", label="Share of trips", unit="share"),
                ],
                rows=rows,
                note="Unmapped covers zones 264/265 and IDs missing from the TLC lookup.",
            ),
        ]
        if ordered:
            name, trips = ordered[0]
            ctx.finding(
                spec.id,
                f"{name} accounted for {f.percent(f.share(trips, ctx.total_trips))} of pickups "
                f"({f.integer(trips)} trips).",
                source="Trips by pickup zone, summed by borough",
                source_table=result["meta"].get("source_table"),
                values={"borough": name, "trips": trips, "total_trips": ctx.total_trips},
            )
        return blocks

    def _s_top_flows(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        result = self.analytics.flows(ctx.filters, metric="total_trips", limit=TOP_PAIRS)
        flows = result["flows"]
        rows = [
            [
                i + 1,
                fl["pickup_label"],
                "same zone" if fl["same_zone"] else fl["dropoff_label"],
                fl["trips"],
                f.share(fl["trips"], ctx.total_trips),
            ]
            for i, fl in enumerate(flows)
        ]
        blocks: list[Any] = [
            TableBlock.build(
                id="flows",
                title=f"Top {len(flows)} pickup → drop-off pairs",
                columns=[
                    Column(key="rank", label="#", unit="count"),
                    Column(key="pickup", label="Pickup zone"),
                    Column(key="dropoff", label="Drop-off zone"),
                    Column(key="trips", label="Trips", unit="trips"),
                    Column(key="share", label="Share of trips", unit="share"),
                ],
                rows=rows,
            )
        ]
        if flows:
            top = flows[0]
            pair = (
                f"trips starting and ending in {top['pickup_label']}"
                if top["same_zone"]
                else f"{top['pickup_label']} → {top['dropoff_label']}"
            )
            ctx.finding(
                spec.id,
                f"The busiest pair was {pair}, with {f.integer(top['trips'])} trips "
                f"({f.percent(f.share(top['trips'], ctx.total_trips))}).",
                source="Busiest pickup → drop-off pairs",
                source_table=result["meta"].get("source_table"),
                values={
                    "pickup_zone": top["pickup_zone"],
                    "dropoff_zone": top["dropoff_zone"],
                    "trips": top["trips"],
                },
            )
        return blocks

    # ---- data quality template -----------------------------------------------------------------------------

    def _quality_kpis(self, ctx: _Context) -> list[Kpi]:
        quality = self._quality(ctx)
        totals = quality["totals"]
        rate = f.share(totals["quarantined_rows"], totals["input_rows"])
        values: list[tuple[str, str, Any, Any]] = [
            ("rows_read", "Rows read", totals["input_rows"], "rows"),
            ("rows_published", "Rows published", totals["accepted_rows"], "rows"),
            ("rows_quarantined", "Rows quarantined", totals["quarantined_rows"], "rows"),
            ("quarantine_rate", "Quarantine rate", rate, "share"),
            ("months", "Months covered", len(quality["periods"]), "count"),
        ]
        return [
            Kpi(id=i, label=label, unit=unit, value=v, display=f.display(v, unit))
            for i, label, v, unit in values
        ]

    def _s_processing(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        periods = self._quality(ctx)["periods"]
        rows = [
            [
                p["period"],
                p["run_id"][:8],
                p["input_rows"],
                p["accepted_rows"],
                p["quarantined_rows"],
                p["duration_seconds"],
                p["schema_version"][:12] if p["schema_version"] else None,
            ]
            for p in periods
        ]
        blocks: list[Any] = [
            TableBlock.build(
                id="processing",
                title="Published months",
                columns=[
                    Column(key="period", label="Month"),
                    Column(key="run_id", label="Run"),
                    Column(key="input_rows", label="Rows read", unit="rows"),
                    Column(key="accepted_rows", label="Published", unit="rows"),
                    Column(key="quarantined_rows", label="Quarantined", unit="rows"),
                    Column(key="duration", label="Run time", unit="seconds"),
                    Column(key="schema", label="Schema version"),
                ],
                rows=rows,
            )
        ]
        if periods:
            durations = [p["duration_seconds"] for p in periods if p["duration_seconds"] is not None]
            ctx.finding(
                spec.id,
                (
                    f"{f.plural(len(periods), 'monthly file')} "
                    f"{'was' if len(periods) == 1 else 'were'} published"
                )
                + (
                    ""
                    if not durations
                    else f"; the run took {f.display(durations[0], 'seconds')}."
                    if len(durations) == 1
                    else f"; runs took {f.display(min(durations), 'seconds')} to "
                    f"{f.display(max(durations), 'seconds')}."
                )
                + ("" if durations else "."),
                source="Processing runs",
                source_table=None,
                values={
                    "months": len(periods),
                    "min_seconds": min(durations, default=None),
                    "max_seconds": max(durations, default=None),
                },
            )
        return blocks

    def _s_quarantine(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        totals = self._quality(ctx)["totals"]
        reasons = sorted(totals["quarantine_reasons"].items(), key=lambda kv: -kv[1])
        quarantined = totals["quarantined_rows"]
        rows = [[QUARANTINE_LABELS.get(k, k), v, f.share(v, quarantined)] for k, v in reasons]
        blocks: list[Any] = [
            TableBlock.build(
                id="quarantine",
                title="Quarantine reasons",
                columns=[
                    Column(key="reason", label="Reason"),
                    Column(key="rows", label="Rows", unit="rows"),
                    Column(key="share", label="Share of quarantined", unit="share"),
                ],
                rows=rows,
                note="A row can have more than one reason, so reasons may add up to more than the total.",
            )
        ]
        if reasons:
            name, count = reasons[0]
            ctx.finding(
                spec.id,
                f"The commonest quarantine reason was {QUARANTINE_LABELS.get(name, name).lower()} "
                f"({f.integer(count)} rows, {f.percent(f.share(count, quarantined))} of quarantined rows).",
                source="Processing-run quality metrics",
                source_table=None,
                values={"reason": name, "rows": count, "quarantined_rows": quarantined},
            )
        return blocks

    def _s_flags(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        totals = self._quality(ctx)["totals"]
        flags = sorted(totals["flags"].items(), key=lambda kv: -kv[1])
        accepted = totals["accepted_rows"]
        rows = [[FLAG_LABELS.get(k, k), v, f.share(v, accepted)] for k, v in flags]
        blocks: list[Any] = [
            ChartBlock(
                id="flags-chart",
                title="Flagged trips by flag",
                kind="bar",
                unit="trips",
                categories=[r[0] for r in rows],
                series=[Series(name="Trips", values=[r[1] for r in rows])],
            ),
            TableBlock.build(
                id="flags",
                title="Quality flags on published trips",
                columns=[
                    Column(key="flag", label="Flag"),
                    Column(key="trips", label="Trips", unit="trips"),
                    Column(key="share", label="Share of published", unit="share"),
                ],
                rows=rows,
            ),
        ]
        if flags:
            name, count = flags[0]
            ctx.finding(
                spec.id,
                f"{FLAG_LABELS.get(name, name)} was the commonest flag, on {f.integer(count)} published "
                f"trips "
                f"({f.percent(f.share(count, accepted))}).",
                source="Processing-run quality metrics",
                source_table=None,
                values={"flag": name, "trips": count, "published_rows": accepted},
            )
        return blocks

    def _s_daily_flags(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        daily = self._quality(ctx)["daily_flags"]
        if not daily:
            return []
        totals: dict[str, int] = defaultdict(int)
        trips_by_day: dict[date, int] = {}
        flagged: dict[tuple[str, date], int] = {}
        for row in daily:
            day = _day(row["date"])
            totals[row["flag"]] += row["flagged_trips"]
            trips_by_day[day] = row["trips"]
            flagged[(row["flag"], day)] = row["flagged_trips"]
        top = [name for name, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:4]]
        days = sorted(trips_by_day)
        series = [
            Series(
                name=FLAG_LABELS.get(name, name),
                values=[f.share(flagged.get((name, d), 0), trips_by_day[d]) for d in days],
            )
            for name in top
        ]
        blocks: list[Any] = [
            ChartBlock(
                id="daily-flags",
                title="Share of trips flagged per day",
                kind="line",
                unit="share",
                categories=[ctx.day_label(d) for d in days],
                series=series,
                note="The four commonest flags in the period.",
            )
        ]
        if series and series[0].values:
            values = [v for v in series[0].values if v is not None]
            ctx.finding(
                spec.id,
                f"The daily share of {series[0].name.lower()} ranged from {f.percent(min(values))} to "
                f"{f.percent(max(values))}.",
                source="Daily flag counts",
                source_table="data_quality_daily",
                values={
                    "flag": top[0],
                    "min_share": round(min(values), 6),
                    "max_share": round(max(values), 6),
                },
            )
        return blocks

    def _s_schema(self, ctx: _Context, spec: SectionSpec) -> list[Any]:
        report = self.analytics.schema(ctx.filters.dataset_id)
        covered = {p.period for p in self._dataset(ctx).periods}
        versions = [
            [v["schema_version"][:12], v["file_format"], v["column_count"], ", ".join(v["periods"])]
            for v in report["schema_versions"]
            if covered & set(v["periods"])
        ]
        changes = [
            d for d in report["drift"] if d["changed"] and {d["from_period"], d["to_period"]} <= covered
        ]
        blocks: list[Any] = [
            TableBlock.build(
                id="schema-versions",
                title="Schema versions",
                columns=[
                    Column(key="version", label="Version"),
                    Column(key="format", label="Format"),
                    Column(key="columns", label="Columns", unit="count"),
                    Column(key="periods", label="Months"),
                ],
                rows=versions,
            )
        ]
        if changes:
            blocks.append(
                TableBlock.build(
                    id="schema-drift",
                    title="Column changes between consecutive files",
                    columns=[
                        Column(key="from", label="From"),
                        Column(key="to", label="To"),
                        Column(key="added", label="Added"),
                        Column(key="removed", label="Removed"),
                    ],
                    rows=[
                        [
                            d["from_period"],
                            d["to_period"],
                            ", ".join(d["added"]) or "—",
                            ", ".join(d["removed"]) or "—",
                        ]
                        for d in changes
                    ],
                )
            )
        ctx.finding(
            spec.id,
            (
                f"The file uses {f.plural(len(versions), 'schema version')}."
                if len(covered) == 1
                else f"The {len(covered)} files use {f.plural(len(versions), 'schema version')}"
                + (
                    "; no columns changed between consecutive files."
                    if not changes
                    else f"; columns changed between {f.plural(len(changes), 'pair')} of consecutive files."
                )
            ),
            source="Schema registry",
            source_table=None,
            values={"files": len(covered), "versions": len(versions), "changes": len(changes)},
        )
        return blocks
