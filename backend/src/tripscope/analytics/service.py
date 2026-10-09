"""Analytics service: the only path from callers (API today; reports and AI tools later) to ClickHouse.

Every result carries the metadata needed to cite it — applied filters, coverage, metric definitions, lineage
(run IDs per period), the table that answered and query timing — so narratives can be grounded in exactly
what was returned.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from clickhouse_connect.driver.client import Client
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tripscope.analytics.filters import (
    MAX_PREVIEW_OFFSET,
    AnalyticsFilters,
    ExplorerQuery,
    ExportRequest,
    TimeSeriesQuery,
)
from tripscope.analytics.labels import PAYMENT_TYPES, VENDORS, WEEKDAYS, code_label
from tripscope.analytics.metrics import METRICS
from tripscope.analytics.query_builder import (
    EXPLORER_COLUMNS,
    MAX_SERIES_POINTS,
    RAW,
    FactSource,
    QualityScope,
    Query,
    active_filters,
    choose_source,
    count_query,
    daily_quality_query,
    distribution_query,
    grouped_query,
    overview_query,
    rows_query,
    time_series_query,
    where_clause,
    zones_query,
)
from tripscope.analytics.schema_report import SourceSchema, build_schema_report
from tripscope.core.errors import NotFoundError, QueryFailedError, ValidationFailedError
from tripscope.metadata.models import (
    DataQualityMetrics,
    Dataset,
    DatasetPeriod,
    DataSource,
    ProcessingRun,
)

log = logging.getLogger(__name__)

MAX_HOURLY_RANGE_DAYS = 62


@dataclass(frozen=True)
class Coverage:
    dataset_id: str
    dataset_name: str
    taxi_type: str
    source_attribution: str
    periods: list[DatasetPeriod]

    @property
    def start(self) -> date | None:
        return min((p.min_pickup_date for p in self.periods), default=None)

    @property
    def end(self) -> date | None:
        return max((p.max_pickup_date for p in self.periods), default=None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "start_date": self.start,
            "end_date": self.end,
            "source_attribution": self.source_attribution,
            "total_rows": sum(p.row_count for p in self.periods),
            "periods": [
                {
                    "period": p.data_period.strftime("%Y-%m"),
                    "row_count": p.row_count,
                    "run_id": str(p.run_id),
                    "published_at": p.published_at,
                }
                for p in self.periods
            ],
        }


def _quantile_bucket(buckets: list[dict[str, Any]], q: float) -> dict[str, Any] | None:
    """The histogram bucket containing quantile `q` (an interval, not a fabricated point estimate)."""
    total = sum(b["trips"] for b in buckets)
    if not total:
        return None
    running = 0
    for bucket in buckets:
        running += bucket["trips"]
        if running >= q * total:
            return {"start": bucket["start"], "end": bucket["end"]}
    return None


def _number(value: Any) -> float | None:
    """ClickHouse returns NaN for avg over zero rows (NULL for sum/nullIf); both mean 'no data', never 0."""
    if value is None:
        return None
    result = float(value)
    return None if math.isnan(result) or math.isinf(result) else result


class AnalyticsService:
    def __init__(
        self,
        *,
        client_factory: Any,
        session_factory: sessionmaker[Session],
        database: str,
        max_range_days: int,
    ) -> None:
        self._client_factory = client_factory
        self._client: Client | None = None
        self._sessions = session_factory
        self._database = database
        self._max_range_days = max_range_days
        self._zones: dict[int, dict[str, Any]] | None = None
        self._zones_lock = threading.Lock()

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    # ---- coverage & shared helpers -------------------------------------------------------------------------

    def coverage(self, dataset_id: str) -> Coverage:
        with self._sessions() as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise NotFoundError(f"dataset {dataset_id!r} not found")
            periods = list(
                session.scalars(
                    select(DatasetPeriod)
                    .where(DatasetPeriod.dataset_id == dataset_id)
                    .order_by(DatasetPeriod.data_period)
                )
            )
            return Coverage(dataset.id, dataset.name, dataset.taxi_type, dataset.source_attribution, periods)

    def _check_range(self, filters: AnalyticsFilters, coverage: Coverage, *, max_days: int) -> None:
        start = filters.start_date or coverage.start
        end = filters.end_date or coverage.end
        if start and end and (end - start).days + 1 > max_days:
            raise ValidationFailedError(
                f"date range spans {(end - start).days + 1} days; the limit for this query is {max_days}",
                details={"max_days": max_days},
            )

    def _run(self, query: Query, label: str) -> tuple[list[tuple[Any, ...]], float]:
        started = time.perf_counter()
        try:
            result = self.client.query(query.sql, parameters=query.parameters)
        except Exception as exc:
            log.exception("analytics query failed", extra={"query": label})
            raise QueryFailedError(
                "The analytics query failed or exceeded a server limit. Narrow the filters and retry."
            ) from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        log.info("analytics query", extra={"query": label, "ms": elapsed_ms, "rows": len(result.result_rows)})
        return [tuple(row) for row in result.result_rows], elapsed_ms

    def _meta(
        self,
        filters: AnalyticsFilters,
        coverage: Coverage,
        metrics: list[str],
        query_ms: float | None,
        source: FactSource | None,
    ) -> dict[str, Any]:
        return {
            "filters": filters.applied(),
            "coverage": coverage.as_dict(),
            "metric_definitions": [METRICS[m].public() for m in metrics],
            "query_ms": query_ms,
            "source_table": source.table if source else None,
            "generated_at": datetime.now(UTC),
            "timezone_note": "Pickup dates and hours are NYC local wall-clock time as recorded by TLC.",
        }

    def _prepare(
        self,
        filters: AnalyticsFilters,
        *,
        max_days: int | None = None,
        prefer_raw: bool = False,
        dimensions: Sequence[str] = (),
        metrics: Sequence[str] = (),
        quality: QualityScope = "all",
        flag: str | None = None,
    ) -> tuple[Coverage, Query | None, FactSource]:
        coverage = self.coverage(filters.dataset_id)
        self._check_range(filters, coverage, max_days=max_days or self._max_range_days)
        source = choose_source(filters, dimensions=dimensions, metrics=metrics, prefer_raw=prefer_raw)
        if not coverage.periods:
            return coverage, None, source
        where = where_clause(
            filters,
            taxi_type=coverage.taxi_type,
            published_periods=[p.data_period for p in coverage.periods],
            quality=quality,
            flag=flag,
        )
        return coverage, where, source

    @staticmethod
    def _empty(key: str, meta: dict[str, Any]) -> dict[str, Any]:
        return {key: [], "data_state": "no_published_data", "meta": meta}

    # ---- KPIs and series -----------------------------------------------------------------------------------

    def _kpis(
        self, filters: AnalyticsFilters, prefer_raw: bool
    ) -> tuple[dict[str, Any] | None, dict[str, Any], float, FactSource]:
        _, where, source = self._prepare(filters, prefer_raw=prefer_raw, metrics=list(METRICS))
        assert where is not None
        rows, ms = self._run(overview_query(self._database, where, source), f"overview:{source.table}")
        columns = [*METRICS] + [f"excluded__{m}" for m in METRICS if source.excluded(m)]
        row = dict(zip([*columns, "first_date", "last_date", "days"], rows[0], strict=True))
        trips = int(row["total_trips"] or 0)
        if trips == 0:
            return None, row, ms, source
        kpis = {
            metric_id: {
                "value": trips if metric_id == "total_trips" else _number(row[metric_id]),
                "unit": definition.unit,
                "excluded_rows": int(row[f"excluded__{metric_id}"]) if source.excluded(metric_id) else 0,
            }
            for metric_id, definition in METRICS.items()
        }
        return kpis, row, ms, source

    def overview(
        self, filters: AnalyticsFilters, *, prefer_raw: bool = False, compare: str = "none"
    ) -> dict[str, Any]:
        coverage = self.coverage(filters.dataset_id)
        metric_ids = list(METRICS)
        if not coverage.periods:
            return {
                "kpis": None,
                "data_state": "no_published_data",
                "meta": self._meta(filters, coverage, metric_ids, None, None),
            }
        kpis, row, ms, source = self._kpis(filters, prefer_raw)
        meta = self._meta(filters, coverage, metric_ids, ms, source)
        if kpis is None:
            return {"kpis": None, "data_state": "empty", "meta": meta}
        result: dict[str, Any] = {
            "kpis": kpis,
            "data_state": "ok",
            "result_range": {
                "first_date": row["first_date"],
                "last_date": row["last_date"],
                "days_with_data": int(row["days"]),
            },
            "meta": meta,
        }
        if compare == "previous":
            result["comparison"] = self._previous_period(filters, coverage, prefer_raw)
        return result

    def _previous_period(
        self, filters: AnalyticsFilters, coverage: Coverage, prefer_raw: bool
    ) -> dict[str, Any]:
        """Same filters over the equally long window just before the selected one, if fully published."""
        if not (filters.start_date and filters.end_date and coverage.start):
            return {
                "available": False,
                "reason": "Select a start and end date to compare with the previous period.",
            }
        days = (filters.end_date - filters.start_date).days + 1
        previous_end = filters.start_date - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        if previous_start < coverage.start:
            return {
                "available": False,
                "reason": f"The previous {days} days start before published coverage ({coverage.start}).",
            }
        previous = filters.model_copy(update={"start_date": previous_start, "end_date": previous_end})
        kpis, _, _, _ = self._kpis(previous, prefer_raw)
        return {
            "available": kpis is not None,
            "start_date": previous_start,
            "end_date": previous_end,
            "days": days,
            "kpis": kpis,
            "reason": None if kpis else "No trips in the previous period.",
        }

    def time_series(self, query: TimeSeriesQuery, *, prefer_raw: bool = False) -> dict[str, Any]:
        max_days = MAX_HOURLY_RANGE_DAYS if query.granularity == "hour" else None
        coverage, where, source = self._prepare(
            query,
            max_days=max_days,
            prefer_raw=prefer_raw,
            dimensions=[f"time:{query.granularity}"],
            metrics=[query.metric],
        )
        if where is None:
            return self._empty("points", self._meta(query, coverage, [query.metric], None, None))
        sql = time_series_query(
            self._database, where, metric=query.metric, granularity=query.granularity, source=source
        )
        rows, ms = self._run(sql, f"time_series:{query.metric}:{query.granularity}:{source.table}")
        if len(rows) > MAX_SERIES_POINTS:
            raise ValidationFailedError("too many points; use a coarser granularity or a shorter range")
        points = [
            {"bucket": bucket, "value": _number(value), "trips": int(trips)} for bucket, value, trips in rows
        ]
        meta = self._meta(query, coverage, [query.metric], ms, source)
        meta["granularity"], meta["metric"] = query.granularity, query.metric
        return {"points": points, "data_state": "ok" if points else "empty", "meta": meta}

    def _label(self, dimension: str, key: Any) -> dict[str, Any]:
        if dimension == "weekday":
            return {"label": WEEKDAYS[int(key) - 1]}
        if dimension == "hour":
            return {"label": f"{int(key):02d}:00"}
        if dimension == "payment_type":
            return {"label": code_label(PAYMENT_TYPES, key, "Payment type")}
        if dimension == "vendor_id":
            return {"label": code_label(VENDORS, key, "Vendor")}
        zone = self.zones().get(int(key)) if key is not None else None
        mapped = bool(zone and zone["is_geographic"])
        return {
            "label": zone["zone"] if zone and mapped else f"Unmapped ({key})",
            "borough": zone["borough"] if zone and mapped else None,
            "mapped": mapped,
        }

    def breakdown(
        self,
        filters: AnalyticsFilters,
        *,
        metric: str,
        dimension: str,
        limit: int = 300,
        prefer_raw: bool = False,
    ) -> dict[str, Any]:
        coverage, where, source = self._prepare(
            filters, prefer_raw=prefer_raw, dimensions=[dimension], metrics=[metric]
        )
        if where is None:
            return self._empty("groups", self._meta(filters, coverage, [metric], None, None))
        ranked = dimension in {"pickup_zone", "dropoff_zone", "payment_type", "vendor_id"}
        sql = grouped_query(
            self._database,
            where,
            metric=metric,
            dimensions=[dimension],
            source=source,
            order="value" if ranked else "dimension",
            limit=limit,
        )
        rows, ms = self._run(sql, f"breakdown:{dimension}:{metric}:{source.table}")
        groups = [
            {"key": key, "value": _number(value), "trips": int(trips), **self._label(dimension, key)}
            for key, value, trips in rows
        ]
        meta = self._meta(filters, coverage, [metric], ms, source)
        meta["dimension"], meta["metric"] = dimension, metric
        return {"groups": groups, "data_state": "ok" if groups else "empty", "meta": meta}

    def hour_weekday_matrix(
        self, filters: AnalyticsFilters, *, metric: str, prefer_raw: bool = False
    ) -> dict[str, Any]:
        coverage, where, source = self._prepare(
            filters, prefer_raw=prefer_raw, dimensions=["weekday", "hour"], metrics=[metric]
        )
        if where is None:
            return self._empty("cells", self._meta(filters, coverage, [metric], None, None))
        sql = grouped_query(
            self._database, where, metric=metric, dimensions=["weekday", "hour"], source=source, limit=7 * 24
        )
        rows, ms = self._run(sql, f"matrix:{metric}:{source.table}")
        cells = [
            {"weekday": int(d), "hour": int(h), "value": _number(v), "trips": int(t)} for d, h, v, t in rows
        ]
        meta = self._meta(filters, coverage, [metric], ms, source)
        meta["metric"] = metric
        return {"cells": cells, "data_state": "ok" if cells else "empty", "meta": meta}

    def flows(self, filters: AnalyticsFilters, *, metric: str, limit: int) -> dict[str, Any]:
        coverage, where, source = self._prepare(
            filters, dimensions=["pickup_zone", "dropoff_zone"], metrics=[metric]
        )
        if where is None:
            return self._empty("flows", self._meta(filters, coverage, [metric], None, None))
        sql = grouped_query(
            self._database,
            where,
            metric=metric,
            dimensions=["pickup_zone", "dropoff_zone"],
            source=source,
            order="value",
            limit=limit,
        )
        rows, ms = self._run(sql, f"flows:{metric}:{source.table}")
        flows = []
        for pickup, dropoff, value, trips in rows:
            origin, destination = self._label("pickup_zone", pickup), self._label("dropoff_zone", dropoff)
            flows.append(
                {
                    "pickup_zone": pickup,
                    "dropoff_zone": dropoff,
                    "value": _number(value),
                    "trips": int(trips),
                    "pickup_label": origin["label"],
                    "pickup_borough": origin["borough"],
                    "dropoff_label": destination["label"],
                    "dropoff_borough": destination["borough"],
                    "same_zone": pickup == dropoff,
                }
            )
        meta = self._meta(filters, coverage, [metric], ms, source)
        meta["metric"] = metric
        return {"flows": flows, "data_state": "ok" if flows else "empty", "meta": meta}

    def distribution(self, filters: AnalyticsFilters, *, metric: str) -> dict[str, Any]:
        """Histogram of valid values; flagged values are excluded and counted; the top bucket is open."""
        coverage, where, _ = self._prepare(filters)
        overview_metric = "avg_trip_distance" if metric == "trip_distance" else "total_recorded_amount"
        if where is None:
            return self._empty("buckets", self._meta(filters, coverage, [overview_metric], None, None))
        from_buckets = active_filters(filters) <= {"date"}
        rows, ms = self._run(
            distribution_query(self._database, where, metric=metric, from_buckets=from_buckets),
            f"distribution:{metric}:{'buckets' if from_buckets else 'raw'}",
        )
        width, cap = (1.0, 50.0) if metric == "trip_distance" else (5.0, 200.0)
        buckets: list[dict[str, Any]] = [
            {
                "start": float(start),
                "end": None if float(start) >= cap else float(start) + width,
                "trips": int(trips),
                "open_ended": float(start) >= cap,
            }
            for start, trips in rows
        ]
        kpis, _, _, _ = self._kpis(filters, prefer_raw=False)
        excluded = int(kpis[overview_metric]["excluded_rows"]) if kpis else 0
        counted = sum(b["trips"] for b in buckets)
        meta = self._meta(filters, coverage, [overview_metric], ms, None)
        meta["source_table"] = "fare_distance_buckets" if from_buckets else "taxi_trips"
        meta["metric"] = metric
        return {
            "buckets": buckets,
            "summary": {
                "counted_trips": counted,
                "excluded_trips": excluded,
                "bucket_width": width,
                "cap": cap,
                "median_bucket": _quantile_bucket(buckets, 0.5),
                "p90_bucket": _quantile_bucket(buckets, 0.9),
                "above_cap_trips": sum(b["trips"] for b in buckets if b["open_ended"]),
            },
            "data_state": "ok" if buckets else "empty",
            "meta": meta,
        }

    # ---- explorer -----------------------------------------------------------------------------------------

    def rows(self, query: ExplorerQuery) -> dict[str, Any]:
        filters, scope = query.filters(), query.scope()
        coverage, where, _ = self._prepare(filters, prefer_raw=True, quality=scope.quality, flag=scope.flag)
        meta = self._meta(filters, coverage, [], None, RAW)
        meta["scope"] = scope.model_dump()
        if where is None:
            return {
                "rows": [],
                "columns": list(EXPLORER_COLUMNS),
                "total": 0,
                "page": query.page,
                "page_size": query.page_size,
                "data_state": "no_published_data",
                "meta": meta,
            }
        total_rows, count_ms = self._run(count_query(self._database, where), "explorer:count")
        offset = (query.page - 1) * query.page_size
        sql = rows_query(
            self._database, where, sort=scope.sort, order=scope.order, limit=query.page_size, offset=offset
        )
        rows, ms = self._run(sql, "explorer:rows")
        total = int(total_rows[0][0])
        meta["query_ms"] = round(count_ms + ms, 2)
        return {
            "rows": [dict(zip(EXPLORER_COLUMNS, r, strict=True)) for r in rows],
            "columns": list(EXPLORER_COLUMNS),
            "total": total,
            "page": query.page,
            "page_size": query.page_size,
            "max_preview_rows": MAX_PREVIEW_OFFSET,
            "data_state": "ok" if rows else "empty",
            "meta": meta,
        }

    def extract(
        self, request: ExportRequest, *, max_rows: int
    ) -> tuple[int, list[str], Iterator[tuple[Any, ...]]]:
        """Bounded extract for downloads: (matching rows, columns, row iterator of at most `max_rows`)."""
        limit = min(request.max_rows or max_rows, max_rows)
        columns = list(request.columns or EXPLORER_COLUMNS)
        _, where, _ = self._prepare(
            request.filters, prefer_raw=True, quality=request.scope.quality, flag=request.scope.flag
        )
        if where is None:
            return 0, columns, iter(())
        total = int(self._run(count_query(self._database, where), "extract:count")[0][0][0])
        sql = rows_query(
            self._database,
            where,
            sort=request.scope.sort,
            order=request.scope.order,
            limit=limit,
            offset=0,
            columns=columns,
        )

        def stream() -> Iterator[tuple[Any, ...]]:
            with self.client.query_row_block_stream(sql.sql, parameters=sql.parameters) as blocks:
                for block in blocks:
                    yield from (tuple(row) for row in block)

        return total, columns, stream()

    def zones(self) -> dict[int, dict[str, Any]]:
        """TLC zone lookup, cached per process (it changes only when the pipeline reloads it)."""
        with self._zones_lock:
            if self._zones is None:
                rows, _ = self._run(zones_query(self._database), "zones")
                self._zones = {
                    int(r[0]): {
                        "id": int(r[0]),
                        "borough": r[1],
                        "zone": r[2],
                        "service_zone": r[3],
                        "is_geographic": bool(r[4]),
                    }
                    for r in rows
                }
            return self._zones

    # ---- data quality and schema ---------------------------------------------------------------------------

    def quality(self, filters: AnalyticsFilters) -> dict[str, Any]:
        coverage, where, _ = self._prepare(filters)
        with self._sessions() as session:
            run_ids = [p.run_id for p in coverage.periods]
            metrics = {
                q.run_id: q
                for q in session.scalars(
                    select(DataQualityMetrics).where(DataQualityMetrics.run_id.in_(run_ids))
                )
            }
            runs = {
                r.id: r for r in session.scalars(select(ProcessingRun).where(ProcessingRun.id.in_(run_ids)))
            }
        periods = []
        totals: dict[str, Any] = {
            "input_rows": 0,
            "accepted_rows": 0,
            "quarantined_rows": 0,
            "duplicate_rows": 0,
            "quarantine_reasons": {},
            "flags": {},
        }
        for period in coverage.periods:
            q, run = metrics.get(period.run_id), runs.get(period.run_id)
            if q is None:
                continue
            flags = {k: v for k, v in q.flag_counts.items() if k != "cast_failures"}
            periods.append(
                {
                    "period": period.data_period.strftime("%Y-%m"),
                    "run_id": str(period.run_id),
                    "input_rows": q.input_row_count,
                    "accepted_rows": q.accepted_row_count,
                    "quarantined_rows": q.quarantined_row_count,
                    "duplicate_rows": q.duplicate_row_count,
                    "quarantine_reasons": q.quarantine_reason_counts,
                    "flags": flags,
                    "cast_failures": q.flag_counts.get("cast_failures", {}),
                    "missingness": q.missingness_by_column,
                    "schema_version": q.schema_version,
                    "unavailable_fields": run.unavailable_fields if run else [],
                    "quality_rules": run.quality_rules if run else {},
                    "duration_seconds": q.duration_seconds,
                    "input_bytes": q.input_bytes,
                    "output_bytes": q.output_bytes,
                    "completed_at": q.completed_at,
                }
            )
            for key in ("input_rows", "accepted_rows", "quarantined_rows", "duplicate_rows"):
                totals[key] += periods[-1][key]
            for bucket, values in (("quarantine_reasons", q.quarantine_reason_counts), ("flags", flags)):
                for name, count in values.items():
                    totals[bucket][name] = totals[bucket].get(name, 0) + int(count)
        daily: list[dict[str, Any]] = []
        ms = None
        if where is not None:
            date_only = AnalyticsFilters(
                dataset_id=filters.dataset_id, start_date=filters.start_date, end_date=filters.end_date
            )
            date_where = where_clause(
                date_only,
                taxi_type=coverage.taxi_type,
                published_periods=[p.data_period for p in coverage.periods],
            )
            rows, ms = self._run(daily_quality_query(self._database, date_where), "quality:daily")
            daily = [{"date": d, "flag": f, "flagged_trips": int(n), "trips": int(t)} for d, f, n, t in rows]
        meta = self._meta(filters, coverage, [], ms, None)
        meta["note"] = (
            "Run-level counts cover whole published months; daily flag trends follow the date filter. "
            "Flags mark suspect values on kept trips; quarantined rows never reach the dashboard."
        )
        return {"periods": periods, "totals": totals, "daily_flags": daily, "meta": meta}

    def schema(self, dataset_id: str) -> dict[str, Any]:
        coverage = self.coverage(dataset_id)
        with self._sessions() as session:
            sources = session.scalars(
                select(DataSource).where(
                    DataSource.dataset_id == dataset_id, DataSource.source_schema.is_not(None)
                )
            ).all()
            schemas = [
                SourceSchema(
                    period=s.data_period.strftime("%Y-%m"),
                    source_key=s.source_key,
                    file_format=s.file_format,
                    schema_version=s.schema_version,
                    columns=dict((s.source_schema or {}).get("source_columns", {})),
                    mapping=dict((s.source_schema or {}).get("mapping", {})),
                    num_rows=(s.source_schema or {}).get("num_rows"),
                )
                for s in sources
            ]
        report = build_schema_report(schemas)
        report["dataset_id"], report["dataset_name"] = coverage.dataset_id, coverage.dataset_name
        return report
