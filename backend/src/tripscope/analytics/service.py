"""Analytics service: the only path from callers (API today; reports and AI tools later) to ClickHouse.

Every result carries the metadata needed to cite it — applied filters, coverage, metric definitions, lineage
(run IDs per period) and query timing — so downstream narratives can be grounded in exactly what was returned.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from clickhouse_connect.driver.client import Client
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tripscope.analytics.filters import AnalyticsFilters, TimeSeriesQuery
from tripscope.analytics.metrics import METRICS
from tripscope.analytics.query_builder import (
    MAX_SERIES_POINTS,
    Query,
    overview_query,
    time_series_query,
    where_clause,
)
from tripscope.core.errors import NotFoundError, QueryFailedError, ValidationFailedError
from tripscope.metadata.models import Dataset, DatasetPeriod

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
    """ClickHouse returns NaN for avg over zero rows; that is 'no data', never 0."""
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

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

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
        self, filters: AnalyticsFilters, coverage: Coverage, metrics: list[str], query_ms: float | None
    ) -> dict[str, Any]:
        return {
            "filters": filters.applied(),
            "coverage": coverage.as_dict(),
            "metric_definitions": [METRICS[m].public() for m in metrics],
            "query_ms": query_ms,
            "generated_at": datetime.now(UTC),
            "timezone_note": "Pickup dates and hours are NYC local wall-clock time as recorded by TLC.",
        }

    def overview(self, filters: AnalyticsFilters) -> dict[str, Any]:
        coverage = self.coverage(filters.dataset_id)
        self._check_range(filters, coverage, max_days=self._max_range_days)
        metric_ids = list(METRICS)
        if not coverage.periods:
            return {
                "kpis": None,
                "data_state": "no_published_data",
                "meta": self._meta(filters, coverage, metric_ids, None),
            }
        where = where_clause(
            filters, taxi_type=coverage.taxi_type, published_periods=[p.data_period for p in coverage.periods]
        )
        rows, ms = self._run(overview_query(self._database, where), "overview")
        row = dict(zip(self._overview_columns(), rows[0], strict=True))
        trips = int(row["total_trips"])
        if trips == 0:
            return {
                "kpis": None,
                "data_state": "empty",
                "meta": self._meta(filters, coverage, metric_ids, ms),
            }
        kpis = {}
        for metric_id, definition in METRICS.items():
            kpis[metric_id] = {
                "value": _number(row[metric_id]) if metric_id != "total_trips" else trips,
                "unit": definition.unit,
                "excluded_rows": int(row[f"excluded__{metric_id}"])
                if definition.excluded_rows_expression
                else 0,
            }
        result_range = {
            "first_date": row["first_date"],
            "last_date": row["last_date"],
            "days_with_data": int(row["days"]),
        }
        return {
            "kpis": kpis,
            "data_state": "ok",
            "result_range": result_range,
            "meta": self._meta(filters, coverage, metric_ids, ms),
        }

    @staticmethod
    def _overview_columns() -> list[str]:
        columns = list(METRICS)
        columns += [f"excluded__{m.id}" for m in METRICS.values() if m.excluded_rows_expression]
        return [*columns, "first_date", "last_date", "days"]

    def time_series(self, query: TimeSeriesQuery) -> dict[str, Any]:
        coverage = self.coverage(query.dataset_id)
        max_days = MAX_HOURLY_RANGE_DAYS if query.granularity == "hour" else self._max_range_days
        self._check_range(query, coverage, max_days=max_days)
        if not coverage.periods:
            return {
                "points": [],
                "data_state": "no_published_data",
                "meta": self._meta(query, coverage, [query.metric], None),
            }
        where = where_clause(
            query, taxi_type=coverage.taxi_type, published_periods=[p.data_period for p in coverage.periods]
        )
        sql = time_series_query(self._database, where, metric=query.metric, granularity=query.granularity)
        rows, ms = self._run(sql, f"time_series:{query.metric}:{query.granularity}")
        if len(rows) > MAX_SERIES_POINTS:
            raise ValidationFailedError("too many points; use a coarser granularity or a shorter range")
        points = [
            {"bucket": bucket, "value": _number(value), "trips": int(trips)} for bucket, value, trips in rows
        ]
        meta = self._meta(query, coverage, [query.metric], ms)
        meta["granularity"], meta["metric"] = query.granularity, query.metric
        return {"points": points, "data_state": "ok" if points else "empty", "meta": meta}
