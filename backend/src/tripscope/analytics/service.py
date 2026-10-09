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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from clickhouse_connect.driver.client import Client
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tripscope.analytics.filters import AnalyticsFilters, TimeSeriesQuery
from tripscope.analytics.metrics import METRICS
from tripscope.analytics.query_builder import (
    MAX_SERIES_POINTS,
    FactSource,
    Query,
    choose_source,
    daily_quality_query,
    grouped_query,
    overview_query,
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
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


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
        self, filters: AnalyticsFilters, *, max_days: int | None = None, prefer_raw: bool = False
    ) -> tuple[Coverage, Query | None, FactSource]:
        coverage = self.coverage(filters.dataset_id)
        self._check_range(filters, coverage, max_days=max_days or self._max_range_days)
        source = choose_source(filters, prefer_raw=prefer_raw)
        if not coverage.periods:
            return coverage, None, source
        where = where_clause(
            filters, taxi_type=coverage.taxi_type, published_periods=[p.data_period for p in coverage.periods]
        )
        return coverage, where, source

    # ---- KPIs and series -----------------------------------------------------------------------------------

    def overview(self, filters: AnalyticsFilters, *, prefer_raw: bool = False) -> dict[str, Any]:
        coverage, where, source = self._prepare(filters, prefer_raw=prefer_raw)
        metric_ids = list(METRICS)
        if where is None:
            return {
                "kpis": None,
                "data_state": "no_published_data",
                "meta": self._meta(filters, coverage, metric_ids, None, None),
            }
        rows, ms = self._run(overview_query(self._database, where, source), f"overview:{source.kind}")
        columns = [*METRICS]
        columns += [f"excluded__{m.id}" for m in METRICS.values() if source.excluded(m)]
        row = dict(zip([*columns, "first_date", "last_date", "days"], rows[0], strict=True))
        meta = self._meta(filters, coverage, metric_ids, ms, source)
        trips = int(row["total_trips"] or 0)
        if trips == 0:
            return {"kpis": None, "data_state": "empty", "meta": meta}
        kpis = {
            metric_id: {
                "value": trips if metric_id == "total_trips" else _number(row[metric_id]),
                "unit": definition.unit,
                "excluded_rows": int(row[f"excluded__{metric_id}"]) if source.excluded(definition) else 0,
            }
            for metric_id, definition in METRICS.items()
        }
        result_range = {
            "first_date": row["first_date"],
            "last_date": row["last_date"],
            "days_with_data": int(row["days"]),
        }
        return {"kpis": kpis, "data_state": "ok", "result_range": result_range, "meta": meta}

    def time_series(self, query: TimeSeriesQuery, *, prefer_raw: bool = False) -> dict[str, Any]:
        max_days = MAX_HOURLY_RANGE_DAYS if query.granularity == "hour" else None
        coverage, where, source = self._prepare(query, max_days=max_days, prefer_raw=prefer_raw)
        if where is None:
            return {
                "points": [],
                "data_state": "no_published_data",
                "meta": self._meta(query, coverage, [query.metric], None, None),
            }
        sql = time_series_query(
            self._database, where, metric=query.metric, granularity=query.granularity, source=source
        )
        rows, ms = self._run(sql, f"time_series:{query.metric}:{query.granularity}:{source.kind}")
        if len(rows) > MAX_SERIES_POINTS:
            raise ValidationFailedError("too many points; use a coarser granularity or a shorter range")
        points = [
            {"bucket": bucket, "value": _number(value), "trips": int(trips)} for bucket, value, trips in rows
        ]
        meta = self._meta(query, coverage, [query.metric], ms, source)
        meta["granularity"], meta["metric"] = query.granularity, query.metric
        return {"points": points, "data_state": "ok" if points else "empty", "meta": meta}

    def breakdown(
        self,
        filters: AnalyticsFilters,
        *,
        metric: str,
        dimension: Literal["hour", "weekday", "pickup_zone"],
        limit: int = 300,
        prefer_raw: bool = False,
    ) -> dict[str, Any]:
        coverage, where, source = self._prepare(filters, prefer_raw=prefer_raw)
        if where is None:
            return {
                "groups": [],
                "data_state": "no_published_data",
                "meta": self._meta(filters, coverage, [metric], None, None),
            }
        order: Literal["dimension", "value"] = "value" if dimension == "pickup_zone" else "dimension"
        sql = grouped_query(
            self._database, where, metric=metric, dimension=dimension, source=source, order=order, limit=limit
        )
        rows, ms = self._run(sql, f"breakdown:{dimension}:{metric}:{source.kind}")
        zones = self.zones() if dimension == "pickup_zone" else {}
        groups = []
        for key, value, trips in rows:
            group: dict[str, Any] = {"key": key, "value": _number(value), "trips": int(trips)}
            if dimension == "weekday":
                group["label"] = WEEKDAYS[int(key) - 1]
            elif dimension == "hour":
                group["label"] = f"{int(key):02d}:00"
            else:
                zone = zones.get(int(key)) if key is not None else None
                group["label"] = zone["zone"] if zone and zone["is_geographic"] else f"Unmapped ({key})"
                group["borough"] = zone["borough"] if zone and zone["is_geographic"] else None
                group["mapped"] = bool(zone and zone["is_geographic"])
            groups.append(group)
        meta = self._meta(filters, coverage, [metric], ms, source)
        meta["dimension"], meta["metric"] = dimension, metric
        return {"groups": groups, "data_state": "ok" if groups else "empty", "meta": meta}

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
