"use client";

import type { DistributionResponse } from "@/api/types";
import { escapeHtml, formatInteger, formatPercent } from "@/lib/format";

import { EChart, tooltipBase } from "./echart";

export function bucketLabel(start: number, end: number | null, unit: "mi" | "usd"): string {
  const fmt = (v: number) => (unit === "usd" ? `$${v}` : `${v}`);
  return end === null ? `${fmt(start)}+${unit === "mi" ? " mi" : ""}` : `${fmt(start)}–${fmt(end)}${unit === "mi" ? " mi" : ""}`;
}

/** Histogram of valid values with median / p90 markers; the last bucket is open-ended (outliers capped). */
export function Histogram({
  data,
  unit,
  selected,
  onSelect,
  dimmed,
  testId,
}: {
  data: DistributionResponse;
  unit: "mi" | "usd";
  selected?: { min?: number; max?: number };
  onSelect?: (start: number, end: number | null) => void;
  dimmed: boolean;
  testId: string;
}) {
  const { buckets, summary } = data;
  const labels = buckets.map((b) => bucketLabel(b.start, b.end, unit));
  const indexOf = (bucket: { start: number } | null) => (bucket ? buckets.findIndex((b) => b.start === bucket.start) : -1);
  const inSelection = (start: number, end: number | null) =>
    selected && (selected.min !== undefined || selected.max !== undefined)
      ? start >= (selected.min ?? 0) && (selected.max === undefined || (end !== null && end <= selected.max))
      : true;
  const median = indexOf(summary.median_bucket);
  const p90 = indexOf(summary.p90_bucket);
  return (
    <EChart
      testId={testId}
      ariaLabel={`Distribution of ${unit === "mi" ? "trip distance" : "total amount"}. Median and 90th percentile marked.`}
      className="h-64"
      dimmed={dimmed}
      deps={[buckets, selected]}
      onClick={(p) => {
        const bucket = buckets[p.dataIndex];
        if (bucket && onSelect) onSelect(bucket.start, bucket.end);
      }}
      build={(theme) => ({
        grid: { left: 4, right: 12, top: 26, bottom: 4, containLabel: true },
        xAxis: { type: "category", data: labels, axisTick: { show: false }, axisLine: { lineStyle: { color: theme.axis } }, axisLabel: { color: theme.muted, fontSize: 10, hideOverlap: true } },
        yAxis: { type: "value", axisLabel: { color: theme.muted, fontSize: 10, formatter: (v: number) => (v >= 1e6 ? `${v / 1e6}M` : v >= 1000 ? `${v / 1000}K` : String(v)) }, splitLine: { lineStyle: { color: theme.grid } } },
        tooltip: {
          ...tooltipBase(theme),
          trigger: "item",
          formatter: (params: unknown) => {
            const bucket = buckets[(params as { dataIndex: number }).dataIndex];
            if (!bucket) return "";
            return `<div style="font-weight:600;font-size:14px">${escapeHtml(formatInteger(bucket.trips))} trips</div><div style="color:${theme.ink2}">${escapeHtml(bucketLabel(bucket.start, bucket.end, unit))}${bucket.open_ended ? " (capped outliers)" : ""}</div><div style="color:${theme.muted}">${escapeHtml(formatPercent(bucket.trips, summary.counted_trips))} of valid trips</div>`;
          },
        },
        series: [
          {
            type: "bar",
            barCategoryGap: "12%",
            cursor: onSelect ? "pointer" : "default",
            data: buckets.map((b) => ({
              value: b.trips,
              itemStyle: {
                color: b.open_ended ? "#184f95" : theme.series,
                opacity: inSelection(b.start, b.end) ? 1 : 0.25,
                borderRadius: [3, 3, 0, 0],
              },
            })),
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { color: theme.ink2, type: "solid", width: 1 },
              label: { color: theme.ink2, fontSize: 10, formatter: "{b}" },
              data: [
                ...(median >= 0 ? [{ name: "median", xAxis: median }] : []),
                ...(p90 >= 0 && p90 !== median ? [{ name: "p90", xAxis: p90 }] : []),
              ],
            },
          },
        ],
      })}
    />
  );
}
