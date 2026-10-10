"use client";

import { ArrowRight } from "lucide-react";
import { toast } from "sonner";

import { type BreakdownPath, useBreakdown, useDistribution, useFlows, useMatrix } from "@/api/hooks";
import type { DashboardFilters } from "@/api/types";
import { BarList } from "@/components/charts/bar-list";
import { HourWeekdayHeatmap } from "@/components/charts/heatmap";
import { bucketLabel, Histogram } from "@/components/charts/histogram";
import { Card, CardHeader } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/overlays";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import { setDistance, toggleValue, WEEKDAYS } from "@/lib/filters";
import { formatInteger, formatPercent } from "@/lib/format";

type Props = { filters: DashboardFilters; onFiltersChange: (f: DashboardFilters) => void };

export function HeatmapCard({ filters, onFiltersChange }: Props) {
  const query = useMatrix(filters, "total_trips");
  const select = (weekday: number, hour: number) => {
    const same = filters.weekday.length === 1 && filters.weekday[0] === weekday && filters.hour.length === 1 && filters.hour[0] === hour;
    onFiltersChange(same ? { ...filters, weekday: [], hour: [] } : { ...filters, weekday: [weekday], hour: [hour] });
    if (!same) toast.message(`Filtered to ${WEEKDAYS[weekday - 1]} ${String(hour).padStart(2, "0")}:00`, { description: "Click the cell again to clear." });
  };
  return (
    <Card>
      <CardHeader title="When trips happen" description="Trips by pickup hour and day of week (NYC local time). Click a cell to filter to that hour and day." />
      <div className="px-3 pb-4 pt-2 sm:px-5">
        {query.isPending ? (
          <LoadingBlock className="h-[300px]" />
        ) : query.isError ? (
          <ErrorBlock error={query.error} className="h-[300px]" />
        ) : query.data.cells.length === 0 ? (
          <EmptyBlock title="No trips" className="h-[300px]" />
        ) : (
          <HourWeekdayHeatmap cells={query.data.cells} selectedHours={filters.hour} selectedDays={filters.weekday} onSelect={select} dimmed={query.isPlaceholderData} />
        )}
      </div>
    </Card>
  );
}

function CodeBreakdownCard({
  path,
  field,
  title,
  description,
  filters,
  onFiltersChange,
}: Props & { path: BreakdownPath; field: "payment_type" | "vendor_id"; title: string; description: string }) {
  const query = useBreakdown(path, filters, "total_trips");
  const groups = query.data?.groups ?? [];
  const total = groups.reduce((sum, g) => sum + g.trips, 0);
  return (
    <Card>
      <CardHeader title={title} description={description} />
      <div className={cn("px-3 pb-4 pt-3 transition-opacity sm:px-4", query.isPlaceholderData && "opacity-50")} data-testid={`${path}-card`}>
        {query.isPending ? (
          <LoadingBlock className="h-40" />
        ) : query.isError ? (
          <ErrorBlock error={query.error} className="h-40" />
        ) : groups.length === 0 ? (
          <EmptyBlock title="No trips" className="h-40" />
        ) : (
          <BarList
            ariaLabel={title}
            items={groups.map((g) => ({ key: g.key ?? -1, label: g.label, value: g.trips, display: formatInteger(g.trips), share: formatPercent(g.trips, total) }))}
            selected={filters[field]}
            onSelect={(key) => Number(key) >= 0 && onFiltersChange(toggleValue(filters, field, Number(key)))}
          />
        )}
      </div>
    </Card>
  );
}

export function PaymentTypesCard(props: Props) {
  return (
    <CodeBreakdownCard
      {...props}
      path="payment-types"
      field="payment_type"
      title="Payment types"
      description="TLC payment codes. Cash tips are not recorded, so card and cash amounts are not directly comparable."
    />
  );
}

export function VendorsCard(props: Props) {
  return <CodeBreakdownCard {...props} path="vendors" field="vendor_id" title="Vendors" description="TPEP technology provider that recorded the trip." />;
}

function DistributionCard({ metric, filters, onFiltersChange }: Props & { metric: "trip_distance" | "total_amount" }) {
  const query = useDistribution(filters, metric);
  const unit = metric === "trip_distance" ? "mi" : "usd";
  const summary = query.data?.summary;
  const interval = (b: { start: number; end: number | null } | null | undefined) => (b ? bucketLabel(b.start, b.end, unit) : "—");
  return (
    <Card>
      <CardHeader
        title={metric === "trip_distance" ? "Trip distance" : "Total amount per trip"}
        description={
          metric === "trip_distance"
            ? "Valid distances in 1-mile buckets; 50+ miles grouped. Click a bar to filter to that distance."
            : "Valid totals in $5 buckets; $200+ grouped. Negative (refund) and >$1,000 totals are excluded."
        }
      />
      <div className="px-3 pb-4 pt-2 sm:px-5">
        {query.isPending ? (
          <LoadingBlock className="h-64" />
        ) : query.isError ? (
          <ErrorBlock error={query.error} className="h-64" />
        ) : query.data.buckets.length === 0 ? (
          <EmptyBlock title="No valid values" className="h-64" />
        ) : (
          <Histogram
            data={query.data}
            unit={unit}
            testId={`${metric}-histogram`}
            dimmed={query.isPlaceholderData}
            {...(metric === "trip_distance"
              ? {
                  selected: { ...(filters.min_distance !== undefined ? { min: filters.min_distance } : {}), ...(filters.max_distance !== undefined ? { max: filters.max_distance } : {}) },
                  onSelect: (start: number, end: number | null) => onFiltersChange(setDistance(filters, start, end ?? undefined)),
                }
              : {})}
          />
        )}
        {summary ? (
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
            {[
              ["Median", interval(summary.median_bucket)],
              ["90th percentile", interval(summary.p90_bucket)],
              [`Above ${unit === "mi" ? `${summary.cap} mi` : `$${summary.cap}`}`, formatInteger(summary.above_cap_trips)],
              ["Excluded (flagged)", formatInteger(summary.excluded_trips)],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-ink-muted">{label}</dt>
                <dd className="tabular font-medium text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
    </Card>
  );
}

export function DistanceCard(props: Props) {
  return <DistributionCard {...props} metric="trip_distance" />;
}

export function AmountCard(props: Props) {
  return <DistributionCard {...props} metric="total_amount" />;
}

export function FlowsCard({ filters, onFiltersChange }: Props) {
  const query = useFlows(filters, 12);
  const flows = query.data?.flows ?? [];
  const max = Math.max(...flows.map((f) => f.trips), 1);
  return (
    <Card>
      <CardHeader title="Busiest pickup → drop-off pairs" description="Click a pair to filter both zones. Same-zone trips start and end in one zone." />
      <div className={cn("px-3 pb-4 pt-3 transition-opacity sm:px-4", query.isPlaceholderData && "opacity-50")}>
        {query.isPending ? (
          <LoadingBlock className="h-72" />
        ) : query.isError ? (
          <ErrorBlock error={query.error} className="h-72" />
        ) : flows.length === 0 ? (
          <EmptyBlock title="No trips" className="h-72" />
        ) : (
          <ul className="space-y-1" data-testid="top-flows">
            {flows.map((flow) => {
              const active = filters.pickup_zone.includes(flow.pickup_zone) && filters.dropoff_zone.includes(flow.dropoff_zone);
              return (
                <li key={`${flow.pickup_zone}-${flow.dropoff_zone}`}>
                  <button
                    type="button"
                    aria-pressed={active}
                    onClick={() =>
                      onFiltersChange(
                        active
                          ? { ...filters, pickup_zone: [], dropoff_zone: [] }
                          : { ...filters, pickup_zone: [flow.pickup_zone], dropoff_zone: [flow.dropoff_zone] },
                      )
                    }
                    className="group relative flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left"
                  >
                    <span aria-hidden className={cn("absolute inset-y-0.5 left-0 rounded-md transition-all", active ? "bg-accent/25" : "bg-accent/10 group-hover:bg-accent/18")} style={{ width: `${(flow.trips / max) * 100}%` }} />
                    <span className="relative flex min-w-0 flex-1 items-center gap-1.5 text-[13px]">
                      <Tooltip content={flow.pickup_borough ?? "Unmapped zone"}>
                        <span className="truncate font-medium text-ink">{flow.pickup_label}</span>
                      </Tooltip>
                      <ArrowRight className="size-3.5 shrink-0 text-ink-faint" />
                      <Tooltip content={flow.dropoff_borough ?? "Unmapped zone"}>
                        <span className="truncate font-medium text-ink">{flow.same_zone ? "same zone" : flow.dropoff_label}</span>
                      </Tooltip>
                    </span>
                    <span className="relative tabular text-[13px] font-semibold text-ink">{formatInteger(flow.trips)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
