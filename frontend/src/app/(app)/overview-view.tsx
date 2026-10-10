"use client";

import { CalendarRange, Database, Timer } from "lucide-react";
import { useState } from "react";

import { useDataset, useOverview, useTimeSeries } from "@/api/hooks";
import type { DashboardFilters, MetricDefinition, MetricId, OverviewResponse } from "@/api/types";
import { ZoneMapCard } from "@/components/dashboards/zone-map-card";
import { FilterBar } from "@/components/filters/filter-bar";
import { PageHeader } from "@/components/layout/app-shell";
import { DefinitionsButton } from "@/components/layout/definitions-drawer";
import { TopZonesCard } from "@/components/overview/breakdowns";
import { KpiCard } from "@/components/overview/kpi-card";
import { TrendCard } from "@/components/overview/trend-card";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { useUrlFilters } from "@/hooks/use-url-filters";
import { rangeDays } from "@/lib/filters";
import { formatDate, formatInteger } from "@/lib/format";

const KPI_ORDER: MetricId[] = [
  "total_trips",
  "avg_daily_trips",
  "total_recorded_amount",
  "avg_total_amount",
  "avg_trip_distance",
  "avg_trip_duration_minutes",
];

function KpiTrend({
  metric,
  filters,
  children,
}: {
  metric: MetricId;
  filters: DashboardFilters;
  children: (trend: (number | null)[] | undefined) => React.ReactNode;
}) {
  // Trips per day has no daily series of its own; its sparkline is daily trips.
  const series = useTimeSeries(filters, metric === "avg_daily_trips" ? "total_trips" : metric, "day");
  return <>{children(series.data?.points.map((p) => p.value))}</>;
}

function SelectionTile({ data, filters, compare, onCompare }: { data: OverviewResponse; filters: DashboardFilters; compare: boolean; onCompare: (v: boolean) => void }) {
  const range = data.result_range;
  const comparison = data.comparison;
  const hasRange = Boolean(filters.start_date && filters.end_date);
  return (
    <section className="flex flex-col rounded-2xl border border-dashed border-line-strong bg-surface-2 p-5">
      <h3 className="flex items-center gap-1.5 text-[13px] font-medium text-ink-2">
        <CalendarRange className="size-4 text-ink-muted" /> Selection
      </h3>
      <p className="mt-2 text-sm font-semibold text-ink">
        {formatDate(range?.first_date)} – {formatDate(range?.last_date)}
      </p>
      <p className="text-xs text-ink-muted">{range?.days_with_data ?? 0} days with trips</p>
      <label className="mt-auto flex items-center gap-2 pt-3 text-xs text-ink-2">
        <input type="checkbox" checked={compare && hasRange} disabled={!hasRange} onChange={(e) => onCompare(e.target.checked)} className="size-3.5 accent-[var(--accent)]" data-testid="compare-toggle" />
        Compare with previous period
      </label>
      <p className="mt-1 text-[11px] leading-snug text-ink-muted">
        {!hasRange
          ? "Pick a date range to compare."
          : comparison?.available
            ? `Previous ${comparison.days} days: ${formatDate(comparison.start_date)} – ${formatDate(comparison.end_date)}`
            : (comparison?.reason ?? "")}
      </p>
    </section>
  );
}

export function OverviewView() {
  const [filters, setFilters] = useUrlFilters();
  const [compare, setCompare] = useState(true);
  const dataset = useDataset();
  const hasRange = Boolean(filters.start_date && filters.end_date);
  const overview = useOverview(filters, compare && hasRange);
  const definitions = new Map<MetricId, MetricDefinition>((overview.data?.meta.metric_definitions ?? []).map((d) => [d.id, d]));
  const meta = overview.data?.meta;
  const comparison = overview.data?.comparison;
  const comparisonLabel = comparison?.available ? `previous ${comparison.days} days` : undefined;
  const days = rangeDays(filters, dataset.data);

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Overview"
        description="Trip demand, recorded amounts and where trips start — from validated, published TLC data."
        actions={<DefinitionsButton />}
      />
      <div className="sticky top-14 z-20 -mx-4 mb-6 border-b border-line/70 bg-page/85 px-4 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <FilterBar coverage={dataset.data} filters={filters} onChange={setFilters} />
      </div>

      <section aria-label="Key metrics" aria-busy={overview.isFetching} className="mb-6">
        {overview.isPending ? (
          <LoadingBlock label="Loading metrics…" className="h-44" />
        ) : overview.isError ? (
          <ErrorBlock error={overview.error} onRetry={() => void overview.refetch()} />
        ) : overview.data.data_state !== "ok" ? (
          <div className="rounded-2xl border border-dashed border-line-strong bg-surface">
            <EmptyBlock title={overview.data.data_state === "no_published_data" ? "No published data yet" : "No trips match these filters"}>
              {overview.data.data_state === "no_published_data"
                ? "Queue a source on the Data sources page to publish its month."
                : `Published coverage is ${formatDate(meta?.coverage.start_date)} – ${formatDate(meta?.coverage.end_date)}. Try widening the filters.`}
            </EmptyBlock>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {KPI_ORDER.map((id, index) => {
              const definition = definitions.get(id);
              if (!definition) return null;
              return (
                <div key={id} className={index === 0 ? "sm:col-span-2" : undefined}>
                  <KpiTrend metric={id} filters={filters}>
                    {(trend) => (
                      <KpiCard
                        definition={definition}
                        kpi={overview.data.kpis?.[id]}
                        hero={index === 0}
                        trend={trend}
                        dimmed={overview.isPlaceholderData}
                        previous={comparison?.available ? comparison.kpis?.[id]?.value : undefined}
                        comparisonLabel={comparisonLabel}
                      />
                    )}
                  </KpiTrend>
                </div>
              );
            })}
            <SelectionTile data={overview.data} filters={filters} compare={compare} onCompare={setCompare} />
          </div>
        )}
      </section>

      <div className="space-y-6">
        <TrendCard filters={filters} coverage={dataset.data} onFiltersChange={setFilters} />
        <div className="grid gap-6 xl:grid-cols-5">
          <div className="xl:col-span-3">
            <ZoneMapCard filters={filters} onFiltersChange={setFilters} />
          </div>
          <div className="xl:col-span-2">
            <TopZonesCard filters={filters} onFiltersChange={setFilters} limit={10} />
          </div>
        </div>
      </div>

      {meta ? (
        <footer className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line pt-4 text-xs text-ink-muted">
          <span className="flex items-center gap-1.5">
            <Database className="size-3.5" /> {meta.coverage.dataset_name} · {formatInteger(meta.coverage.total_rows)} trips in{" "}
            {meta.coverage.periods.length} published months
          </span>
          <span className="flex items-center gap-1.5">
            <Timer className="size-3.5" /> KPIs from <span className="font-mono">{meta.source_table}</span> in {meta.query_ms} ms
          </span>
          {days ? <span>{days}-day selection</span> : null}
          <span>
            Source:{" "}
            <a href="https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page" className="underline decoration-line-strong underline-offset-2 hover:text-ink" target="_blank" rel="noreferrer">
              NYC TLC Trip Record Data
            </a>
          </span>
        </footer>
      ) : null}
    </div>
  );
}
