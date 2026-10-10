"use client";

import { MapPin } from "lucide-react";
import { useState } from "react";

import { useBreakdown } from "@/api/hooks";
import type { BreakdownGroup, DashboardFilters, MetricId } from "@/api/types";
import { BarList } from "@/components/charts/bar-list";
import { EChart, tooltipBase } from "@/components/charts/echart";
import { Card, CardHeader } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { toggleValue } from "@/lib/filters";
import { escapeHtml, formatAxis, formatInteger, formatPercent, formatValue } from "@/lib/format";

/** Bars where a click toggles the matching filter; selected bars stay saturated, the rest recede. */
function ClickableBars({
  groups,
  selected,
  onToggle,
  label,
  testId,
  dimmed,
}: {
  groups: BreakdownGroup[];
  selected: number[];
  onToggle: (key: number) => void;
  label: string;
  testId: string;
  dimmed: boolean;
}) {
  const total = groups.reduce((sum, g) => sum + g.trips, 0);
  return (
    <EChart
      testId={testId}
      ariaLabel={`${label}. Click a bar to filter.`}
      className="h-56"
      dimmed={dimmed}
      deps={[groups, selected]}
      onClick={(p) => {
        const key = groups[p.dataIndex]?.key;
        if (key !== null && key !== undefined) onToggle(key);
      }}
      build={(theme) => ({
        animationDuration: 250,
        grid: { left: 4, right: 4, top: 12, bottom: 4, containLabel: true },
        xAxis: {
          type: "category",
          data: groups.map((g) => g.label),
          axisLine: { lineStyle: { color: theme.axis } },
          axisTick: { show: false },
          axisLabel: { color: theme.muted, fontSize: 10, interval: groups.length > 12 ? 2 : 0 },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: theme.muted, fontSize: 10, formatter: (v: number) => formatAxis(v, "trips") },
          splitLine: { lineStyle: { color: theme.grid } },
        },
        tooltip: {
          ...tooltipBase(theme),
          trigger: "item",
          formatter: (params: unknown) => {
            const group = groups[(params as { dataIndex: number }).dataIndex];
            if (!group) return "";
            return `<div style="font-weight:600;font-size:14px">${escapeHtml(formatInteger(group.trips))} trips</div><div style="color:${theme.ink2}">${escapeHtml(group.label)} · ${escapeHtml(formatPercent(group.trips, total))} of trips</div><div style="color:${theme.muted};margin-top:2px">Click to ${selected.includes(group.key ?? -1) ? "remove" : "add"} filter</div>`;
          },
        },
        series: [
          {
            type: "bar",
            data: groups.map((g) => ({
              value: g.trips,
              itemStyle: {
                color: selected.length === 0 || selected.includes(g.key ?? -1) ? theme.series : "rgba(42,120,214,0.22)",
                borderRadius: [4, 4, 0, 0],
              },
            })),
            barMaxWidth: 24,
            barCategoryGap: "28%",
            cursor: "pointer",
            emphasis: { itemStyle: { color: "#1c5cab" } },
          },
        ],
      })}
    />
  );
}

export function HourCard({ filters, onFiltersChange }: { filters: DashboardFilters; onFiltersChange: (f: DashboardFilters) => void }) {
  const query = useBreakdown("trips-by-hour", filters, "total_trips");
  return (
    <Card>
      <CardHeader title="Trips by pickup hour" description="Click bars to filter by hour (NYC local time)." />
      <div className="px-3 pb-4 pt-1 sm:px-4">
        {query.isPending ? (
          <LoadingBlock className="h-56" />
        ) : query.isError ? (
          <ErrorBlock error={query.error} className="h-56" />
        ) : query.data.groups.length === 0 ? (
          <EmptyBlock title="No trips" className="h-56" />
        ) : (
          <ClickableBars
            testId="hour-chart"
            label="Trips by hour"
            groups={query.data.groups}
            selected={filters.hour}
            dimmed={query.isPlaceholderData}
            onToggle={(h) => onFiltersChange(toggleValue(filters, "hour", h))}
          />
        )}
      </div>
    </Card>
  );
}

export function WeekdayCard({ filters, onFiltersChange }: { filters: DashboardFilters; onFiltersChange: (f: DashboardFilters) => void }) {
  const query = useBreakdown("trips-by-weekday", filters, "total_trips");
  return (
    <Card>
      <CardHeader title="Trips by day of week" description="Click bars to compare specific days." />
      <div className="px-3 pb-4 pt-1 sm:px-4">
        {query.isPending ? (
          <LoadingBlock className="h-56" />
        ) : query.isError ? (
          <ErrorBlock error={query.error} className="h-56" />
        ) : query.data.groups.length === 0 ? (
          <EmptyBlock title="No trips" className="h-56" />
        ) : (
          <ClickableBars
            testId="weekday-chart"
            label="Trips by weekday"
            groups={query.data.groups}
            selected={filters.weekday}
            dimmed={query.isPlaceholderData}
            onToggle={(d) => onFiltersChange(toggleValue(filters, "weekday", d))}
          />
        )}
      </div>
    </Card>
  );
}

const ZONE_METRICS: { value: MetricId; label: string }[] = [
  { value: "total_trips", label: "Trips" },
  { value: "total_recorded_amount", label: "Amount" },
];

export function TopZonesCard({
  filters,
  onFiltersChange,
  side = "pickup",
  limit = 12,
}: {
  filters: DashboardFilters;
  onFiltersChange: (f: DashboardFilters) => void;
  side?: "pickup" | "dropoff";
  limit?: number;
}) {
  const [metric, setMetric] = useState<MetricId>("total_trips");
  const query = useBreakdown(side === "pickup" ? "top-pickup-zones" : "top-dropoff-zones", filters, metric, limit);
  const field = side === "pickup" ? "pickup_zone" : "dropoff_zone";
  const groups = query.data?.groups ?? [];
  const total = groups.reduce((sum, g) => sum + (g.value ?? 0), 0);
  return (
    <Card>
      <CardHeader
        title={side === "pickup" ? "Top pickup zones" : "Top drop-off zones"}
        description="Ranked by the selected metric. Click a zone to filter the whole view."
        actions={<Segmented label="Rank by" value={metric} onChange={setMetric} options={ZONE_METRICS} />}
      />
      <div className={`px-3 pb-4 pt-2 transition-opacity sm:px-4 ${query.isPlaceholderData ? "opacity-50" : ""}`} data-testid={`top-${side}-zones`}>
        {query.isPending ? (
          <LoadingBlock className="h-72" />
        ) : query.isError ? (
          <ErrorBlock error={query.error} className="h-72" />
        ) : groups.length === 0 ? (
          <EmptyBlock title="No trips" className="h-72" />
        ) : (
          <BarList
            ariaLabel={side === "pickup" ? "Top pickup zones" : "Top drop-off zones"}
            items={groups.map((g) => ({
              key: g.key ?? -1,
              label: g.label,
              sublabel: g.borough ?? null,
              value: g.value ?? 0,
              display: formatValue(g.value, metric === "total_trips" ? "trips" : "usd", { exact: metric === "total_trips" }),
              share: formatPercent(g.value, total),
              muted: !g.mapped,
            }))}
            selected={filters[field]}
            onSelect={(key) => onFiltersChange(toggleValue(filters, field, Number(key)))}
          />
        )}
        <p className="mt-3 flex items-center gap-1.5 text-[11px] text-ink-muted">
          <MapPin className="size-3.5" /> Shares are of the zones shown. IDs 264/265 are TLC’s “Unknown” and “Outside of NYC”, kept but not mapped.
          {query.data?.meta.source_table ? <span className="ml-auto font-mono">{query.data.meta.source_table}</span> : null}
        </p>
      </div>
    </Card>
  );
}
