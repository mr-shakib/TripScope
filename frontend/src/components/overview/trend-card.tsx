"use client";

import { ChartLine, Table2, ZoomIn } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useTimeSeries } from "@/api/hooks";
import type { Coverage, DashboardFilters, Granularity, MetricId, SeriesPoint, Unit } from "@/api/types";
import { EChart, tooltipBase } from "@/components/charts/echart";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { MAX_HOURLY_DAYS, rangeDays, setDates } from "@/lib/filters";
import { escapeHtml, formatAxis, formatBucket, formatInteger, formatValue } from "@/lib/format";

const METRICS: { value: MetricId; label: string }[] = [
  { value: "total_trips", label: "Trips" },
  { value: "total_recorded_amount", label: "Amount" },
  { value: "avg_total_amount", label: "Avg fare" },
  { value: "avg_trip_distance", label: "Distance" },
  { value: "avg_trip_duration_minutes", label: "Duration" },
];

function SeriesTable({ points, unit, granularity }: { points: SeriesPoint[]; unit: Unit; granularity: Granularity }) {
  return (
    <div className="scroll-thin max-h-80 overflow-auto rounded-xl border border-line">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-surface-2 text-left text-xs text-ink-muted">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">Period</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Trips</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {points.map((p) => (
            <tr key={p.bucket} className="border-t border-line">
              <td className="px-3 py-1.5 text-ink-2">{formatBucket(p.bucket, granularity)}</td>
              <td className="px-3 py-1.5 text-right font-medium text-ink">{formatValue(p.value, unit, { exact: true })}</td>
              <td className="px-3 py-1.5 text-right text-ink-2">{formatInteger(p.trips)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TrendCard({
  filters,
  coverage,
  onFiltersChange,
}: {
  filters: DashboardFilters;
  coverage: Coverage | undefined;
  onFiltersChange: (filters: DashboardFilters) => void;
}) {
  const [metric, setMetric] = useState<MetricId>("total_trips");
  const [granularity, setGranularity] = useState<Granularity>("day");
  const [view, setView] = useState<"chart" | "table">("chart");
  const [zoom, setZoom] = useState<{ startIndex: number; endIndex: number } | null>(null);
  const days = rangeDays(filters, coverage);
  const hourlyAllowed = days !== null && days <= MAX_HOURLY_DAYS;
  const effective: Granularity = granularity === "hour" && !hourlyAllowed ? "day" : granularity;
  const series = useTimeSeries(filters, metric, effective);
  const definition = series.data?.meta.metric_definitions[0];
  const unit: Unit = definition?.unit ?? "trips";
  const points = series.data?.points ?? [];
  const zoomed = zoom && effective === "day" && (zoom.startIndex > 0 || zoom.endIndex < points.length - 1);

  const drillTo = (index: number) => {
    const point = points[index];
    if (!point || effective === "month") return;
    const day = point.bucket.slice(0, 10);
    onFiltersChange(setDates(filters, day, day));
    setGranularity("hour");
    toast.success(`Drilled into ${formatBucket(day, "day")}`, { description: "Showing hourly detail for that day." });
  };

  const applyZoom = () => {
    if (!zoom) return;
    const start = points[zoom.startIndex]?.bucket.slice(0, 10);
    const end = points[zoom.endIndex]?.bucket.slice(0, 10);
    if (start && end) onFiltersChange(setDates(filters, start, end));
    setZoom(null);
  };

  return (
    <Card>
      <CardHeader
        title={`${definition?.label ?? "Trips"} over time`}
        description={`${definition?.rows_included ?? ""} Click a ${effective === "hour" ? "point" : "day"} to drill in; drag the slider to zoom.`}
        actions={
          <>
            <Segmented label="Metric" value={metric} onChange={setMetric} options={METRICS} />
            <Segmented
              label="Granularity"
              value={effective}
              onChange={(g) => {
                setGranularity(g);
                setZoom(null);
              }}
              options={[
                { value: "month", label: "Monthly" },
                { value: "day", label: "Daily" },
                { value: "hour", label: "Hourly", disabled: !hourlyAllowed, title: hourlyAllowed ? undefined : `Pick ${MAX_HOURLY_DAYS} days or fewer for hourly detail` },
              ]}
            />
            <Segmented
              label="View"
              value={view}
              onChange={setView}
              options={[
                { value: "chart", label: "Chart" },
                { value: "table", label: "Table" },
              ]}
            />
          </>
        }
      />
      <div className="px-3 pb-4 pt-2 sm:px-5">
        {zoomed ? (
          <div className="mb-2 flex items-center justify-between rounded-lg bg-accent-soft px-3 py-2 text-[13px] text-accent-strong animate-fade-in">
            <span className="flex items-center gap-2">
              <ZoomIn className="size-4" />
              Zoomed to {formatBucket(points[zoom.startIndex]!.bucket, "day")} – {formatBucket(points[zoom.endIndex]!.bucket, "day")}
            </span>
            <Button size="sm" variant="primary" onClick={applyZoom}>
              Apply as date filter
            </Button>
          </div>
        ) : null}
        {series.isPending ? (
          <LoadingBlock className="h-80" label="Loading series…" />
        ) : series.isError ? (
          <ErrorBlock error={series.error} onRetry={() => void series.refetch()} className="h-80" />
        ) : points.length === 0 ? (
          <EmptyBlock title="No trips match these filters" className="h-80" />
        ) : view === "table" ? (
          <SeriesTable points={points} unit={unit} granularity={effective} />
        ) : (
          <EChart
            testId="trend-chart"
            ariaLabel={`${definition?.label ?? "Trips"} by ${effective}, ${points.length} points. Switch to Table for the values.`}
            className="h-80"
            dimmed={series.isPlaceholderData}
            deps={[points, unit, effective]}
            onClick={(p) => drillTo(p.dataIndex)}
            onDataZoom={setZoom}
            build={(theme) => ({
              animationDuration: 300,
              grid: { left: 6, right: 16, top: 16, bottom: effective === "day" ? 52 : 8, containLabel: true },
              xAxis: {
                type: "category",
                data: points.map((p) => formatBucket(p.bucket, effective)),
                boundaryGap: false,
                axisLine: { lineStyle: { color: theme.axis } },
                axisTick: { show: false },
                axisLabel: { color: theme.muted, hideOverlap: true, fontSize: 11 },
              },
              yAxis: {
                type: "value",
                axisLabel: { color: theme.muted, fontSize: 11, formatter: (v: number) => formatAxis(v, unit) },
                splitLine: { lineStyle: { color: theme.grid } },
              },
              dataZoom:
                effective === "day"
                  ? [
                      { type: "inside", throttle: 50 },
                      {
                        type: "slider",
                        height: 22,
                        bottom: 8,
                        borderColor: theme.line,
                        fillerColor: "rgba(42,120,214,0.10)",
                        handleStyle: { color: theme.surface, borderColor: theme.series },
                        moveHandleStyle: { color: theme.axis },
                        dataBackground: { lineStyle: { color: theme.axis }, areaStyle: { color: theme.grid } },
                        selectedDataBackground: { lineStyle: { color: theme.series }, areaStyle: { color: theme.accentSoft } },
                        textStyle: { color: theme.muted, fontSize: 10 },
                        labelFormatter: (_: number, label: string) => label,
                      },
                    ]
                  : [],
              tooltip: {
                ...tooltipBase(theme),
                trigger: "axis",
                axisPointer: { type: "line", lineStyle: { color: theme.axis } },
                formatter: (params: unknown) => {
                  const [first] = params as { dataIndex: number }[];
                  const point = first ? points[first.dataIndex] : undefined;
                  if (!point) return "";
                  return [
                    `<div style="font-weight:600;font-size:15px">${escapeHtml(formatValue(point.value, unit, { exact: true }))}</div>`,
                    `<div style="display:flex;align-items:center;gap:6px;color:${theme.ink2}"><span style="width:12px;height:2px;background:${theme.series};display:inline-block"></span>${escapeHtml(definition?.label ?? "Trips")}</div>`,
                    `<div style="color:${theme.muted};margin-top:2px">${escapeHtml(formatBucket(point.bucket, effective))}${unit === "trips" ? "" : ` · ${escapeHtml(formatInteger(point.trips))} trips`}</div>`,
                  ].join("");
                },
              },
              series: [
                {
                  type: "line",
                  data: points.map((p) => p.value),
                  showSymbol: points.length < 40,
                  symbolSize: 7,
                  connectNulls: false,
                  lineStyle: { width: 2, color: theme.series, cap: "round", join: "round" },
                  itemStyle: { color: theme.series, borderColor: theme.surface, borderWidth: 2 },
                  areaStyle: {
                    color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(42,120,214,0.18)" }, { offset: 1, color: "rgba(42,120,214,0.01)" }] },
                  },
                  emphasis: { focus: "none", scale: 1.4 },
                  cursor: effective === "month" ? "default" : "pointer",
                },
              ],
            })}
          />
        )}
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-muted">
          <ChartLine className="size-3.5" />
          {points.length} {effective === "day" ? "days" : effective === "hour" ? "hours" : "months"} · NYC local time
          {series.data?.meta.source_table ? ` · served from ${series.data.meta.source_table}` : ""}
          {view === "table" ? null : <Table2 className="ml-auto size-3.5" aria-hidden />}
        </p>
      </div>
    </Card>
  );
}
