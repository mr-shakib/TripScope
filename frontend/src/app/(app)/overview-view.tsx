"use client";

import { Database, Timer } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import { useDataset, useOverview, useTimeSeries } from "@/api/hooks";
import type { DashboardFilters, MetricDefinition, MetricId } from "@/api/types";
import { FilterBar } from "@/components/filters/filter-bar";
import { PageHeader } from "@/components/layout/app-shell";
import { HourCard, TopZonesCard, WeekdayCard } from "@/components/overview/breakdowns";
import { KpiCard } from "@/components/overview/kpi-card";
import { TrendCard } from "@/components/overview/trend-card";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { readFilters, writeFilters } from "@/lib/filters";
import { formatDate, formatInteger } from "@/lib/format";

const KPI_ORDER: MetricId[] = [
  "total_trips",
  "total_recorded_amount",
  "avg_total_amount",
  "avg_trip_distance",
  "avg_trip_duration_minutes",
];

function useUrlFilters(): [DashboardFilters, (next: DashboardFilters) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const filters = useMemo(() => readFilters(new URLSearchParams(params.toString())), [params]);
  const setFilters = useCallback(
    (next: DashboardFilters) => {
      const query = writeFilters(new URLSearchParams(params.toString()), next).toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );
  return [filters, setFilters];
}

function KpiTrend({ metric, filters, children }: { metric: MetricId; filters: DashboardFilters; children: (trend: (number | null)[] | undefined) => React.ReactNode }) {
  const series = useTimeSeries(filters, metric, "day");
  return <>{children(series.data?.points.map((p) => p.value))}</>;
}

export function OverviewView() {
  const [filters, setFilters] = useUrlFilters();
  const dataset = useDataset();
  const overview = useOverview(filters);
  const definitions = new Map<MetricId, MetricDefinition>((overview.data?.meta.metric_definitions ?? []).map((d) => [d.id, d]));
  const meta = overview.data?.meta;

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Overview"
        description="Trip demand, recorded amounts and where trips start — from validated, published TLC data."
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
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
            {KPI_ORDER.map((id, index) => {
              const definition = definitions.get(id);
              if (!definition) return null;
              return (
                <div key={id} className={index === 0 ? "sm:col-span-2 xl:col-span-2" : "xl:col-span-1"}>
                  <KpiTrend metric={id} filters={filters}>
                    {(trend) => (
                      <KpiCard
                        definition={definition}
                        kpi={overview.data.kpis?.[id]}
                        hero={index === 0}
                        trend={trend}
                        dimmed={overview.isPlaceholderData}
                      />
                    )}
                  </KpiTrend>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <div className="space-y-6">
        <TrendCard filters={filters} coverage={dataset.data} onFiltersChange={setFilters} />
        <div className="grid gap-6 xl:grid-cols-2">
          <HourCard filters={filters} onFiltersChange={setFilters} />
          <WeekdayCard filters={filters} onFiltersChange={setFilters} />
        </div>
        <TopZonesCard filters={filters} onFiltersChange={setFilters} />
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
          <span>Times are NYC local wall-clock as recorded by TLC.</span>
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
