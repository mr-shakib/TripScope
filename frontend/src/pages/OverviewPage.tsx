import { lazy, Suspense, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useDataset, useOverview, useTimeSeries } from "../api/hooks";
import type { DateFilters, Granularity, MetricDefinition, MetricId } from "../api/types";
import { SeriesTable } from "../components/DataTable";
import { FilterBar } from "../components/FilterBar";
import { StatTile } from "../components/StatTile";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { MAX_HOURLY_DAYS, rangeDays, readDateFilters, writeDateFilters } from "../lib/filters";
import { formatDate } from "../lib/format";

// ECharts is the largest dependency; load it in its own chunk.
const LineChart = lazy(() => import("../components/LineChart").then((m) => ({ default: m.LineChart })));

const CHART_METRICS: { id: MetricId; label: string }[] = [
  { id: "total_trips", label: "Trips" },
  { id: "total_recorded_amount", label: "Total recorded amount" },
  { id: "avg_trip_distance", label: "Average trip distance" },
];

export function OverviewPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readDateFilters(params), [params]);
  const setFilters = (next: DateFilters) => setParams(writeDateFilters(params, next), { replace: true });

  const [metric, setMetric] = useState<MetricId>("total_trips");
  const [granularity, setGranularity] = useState<Granularity>("day");
  const [showTable, setShowTable] = useState(false);

  const dataset = useDataset();
  const overview = useOverview(filters);
  const days = rangeDays(filters, dataset.data);
  const hourlyAllowed = days !== null && days <= MAX_HOURLY_DAYS;
  const effectiveGranularity: Granularity = granularity === "hour" && !hourlyAllowed ? "day" : granularity;
  const series = useTimeSeries(filters, metric, effectiveGranularity);

  const definitions = overview.data?.meta.metric_definitions ?? [];
  const byId = new Map<MetricId, MetricDefinition>(definitions.map((d) => [d.id, d]));
  const seriesDefinition = series.data?.meta.metric_definitions[0];
  const coverage = dataset.data;
  const latestRun = coverage?.periods.at(-1);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Overview</h1>
        <p className="text-sm text-ink-2">Trip volume and recorded amounts from published, validated TLC data.</p>
      </div>

      <FilterBar coverage={coverage} filters={filters} onChange={setFilters} />

      <section aria-label="Key metrics" aria-busy={overview.isFetching}>
        {overview.isPending ? (
          <LoadingState label="Loading metrics…" />
        ) : overview.isError ? (
          <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
        ) : overview.data.data_state !== "ok" ? (
          <EmptyState title={overview.data.data_state === "no_published_data" ? "No published data yet" : "No trips match these filters"}>
            {overview.data.data_state === "no_published_data"
              ? "Run the pipeline for a source in manifests/sources.yaml to publish a period."
              : `Published coverage is ${formatDate(overview.data.meta.coverage.start_date)} – ${formatDate(overview.data.meta.coverage.end_date)}.`}
          </EmptyState>
        ) : (
          <div className={`grid gap-4 sm:grid-cols-2 lg:grid-cols-3 ${overview.isPlaceholderData ? "opacity-50" : ""}`}>
            {(["total_trips", "total_recorded_amount", "avg_total_amount", "avg_trip_distance", "avg_trip_duration_minutes"] as const).map(
              (id, index) => {
                const definition = byId.get(id);
                return definition ? (
                  <StatTile key={id} definition={definition} kpi={overview.data.kpis?.[id]} hero={index === 0} />
                ) : null;
              },
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="series-title" className="rounded-xl border border-line bg-surface-1 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="series-title" className="text-base font-semibold text-ink">
              {seriesDefinition?.label ?? "Trips"} over time
            </h2>
            <p className="text-xs text-ink-muted">
              {seriesDefinition?.rows_included ?? ""} Pickup {effectiveGranularity === "hour" ? "hour" : "date"}, NYC local time.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs text-ink-muted">
              <span className="sr-only">Metric</span>
              <select
                value={metric}
                onChange={(e) => setMetric(e.target.value as MetricId)}
                className="rounded-md border border-line bg-surface-1 px-2 py-1.5 text-sm text-ink"
              >
                {CHART_METRICS.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
            <div role="radiogroup" aria-label="Granularity" className="flex rounded-md border border-line">
              {(["day", "hour"] as const).map((g) => (
                <button
                  key={g}
                  type="button"
                  role="radio"
                  aria-checked={effectiveGranularity === g}
                  disabled={g === "hour" && !hourlyAllowed}
                  title={g === "hour" && !hourlyAllowed ? `Hourly view needs a range of ${MAX_HOURLY_DAYS} days or less` : undefined}
                  onClick={() => setGranularity(g)}
                  className={`px-3 py-1.5 text-sm capitalize first:rounded-l-md last:rounded-r-md disabled:cursor-not-allowed disabled:text-ink-muted ${
                    effectiveGranularity === g ? "bg-surface-2 font-medium text-ink" : "text-ink-2"
                  }`}
                >
                  {g === "day" ? "Daily" : "Hourly"}
                </button>
              ))}
            </div>
            <button
              type="button"
              aria-pressed={showTable}
              onClick={() => setShowTable((v) => !v)}
              className="rounded-md border border-line px-3 py-1.5 text-sm text-ink-2 hover:bg-surface-2"
            >
              {showTable ? "Show chart" : "Show table"}
            </button>
          </div>
        </div>
        {series.isPending ? (
          <LoadingState label="Loading series…" />
        ) : series.isError ? (
          <ErrorState error={series.error} onRetry={() => void series.refetch()} />
        ) : series.data.points.length === 0 ? (
          <EmptyState title="No trips match these filters" />
        ) : showTable ? (
          <SeriesTable
            points={series.data.points}
            unit={seriesDefinition?.unit ?? "trips"}
            seriesLabel={seriesDefinition?.label ?? "Trips"}
            granularity={effectiveGranularity}
          />
        ) : (
          <Suspense fallback={<LoadingState label="Loading chart…" />}>
            <LineChart
              points={series.data.points}
              unit={seriesDefinition?.unit ?? "trips"}
              seriesLabel={seriesDefinition?.label ?? "Trips"}
              granularity={effectiveGranularity}
              dimmed={series.isPlaceholderData}
            />
          </Suspense>
        )}
      </section>

      <footer className="space-y-1 text-xs text-ink-muted">
        <p>
          Source: {coverage?.source_attribution ?? "NYC Taxi & Limousine Commission (TLC) Trip Record Data"}. Pickup dates and
          hours are NYC local time as recorded by TLC. Quarantined records are excluded; flagged values are excluded only from
          the metrics they affect.
        </p>
        {latestRun ? (
          <p>
            Published period {latestRun.period} · run <span className="font-mono">{latestRun.run_id.slice(0, 8)}</span> ·
            {" "}
            {overview.data?.meta.query_ms != null ? `query ${overview.data.meta.query_ms} ms` : ""}
          </p>
        ) : null}
      </footer>
    </div>
  );
}
