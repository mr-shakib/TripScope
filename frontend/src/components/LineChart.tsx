import { LineChart as ELineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useRef, useState } from "react";

import type { Granularity, SeriesPoint, Unit } from "../api/types";
import { escapeHtml, formatAxis, formatBucket, formatInteger, formatValue } from "../lib/format";

echarts.use([ELineChart, GridComponent, TooltipComponent, SVGRenderer]);

interface LineChartProps {
  points: SeriesPoint[];
  unit: Unit;
  seriesLabel: string;
  granularity: Granularity;
  dimmed?: boolean;
}

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Re-render when the OS theme or the data-theme attribute changes; colours come from CSS tokens. */
function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const bump = () => setVersion((v) => v + 1);
    media.addEventListener("change", bump);
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      media.removeEventListener("change", bump);
      observer.disconnect();
    };
  }, []);
  return version;
}

/** Single-series line: 2px line, 10% area wash, axis crosshair tooltip, end-point label only. */
export function LineChart({ points, unit, seriesLabel, granularity, dimmed = false }: LineChartProps) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const themeVersion = useThemeVersion();

  useEffect(() => {
    if (!container.current) return;
    const instance = echarts.init(container.current, undefined, { renderer: "svg" });
    chart.current = instance;
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(container.current);
    return () => {
      resize.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    const series = token("--series-1");
    const surface = token("--surface-1");
    const labels = points.map((p) => formatBucket(p.bucket, granularity));
    const lastIndex = points.length - 1;

    instance.setOption(
      {
        animationDuration: 250,
        grid: { left: 8, right: 72, top: 16, bottom: 8, containLabel: true },
        xAxis: {
          type: "category",
          data: labels,
          boundaryGap: false,
          axisLine: { lineStyle: { color: token("--axis"), width: 1 } },
          axisTick: { show: false },
          axisLabel: { color: token("--text-muted"), hideOverlap: true },
        },
        yAxis: {
          type: "value",
          axisLine: { show: false },
          axisLabel: { color: token("--text-muted"), formatter: (v: number) => formatAxis(v, unit) },
          splitLine: { lineStyle: { color: token("--grid"), width: 1, type: "solid" } },
        },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: token("--axis"), width: 1 } },
          backgroundColor: surface,
          borderColor: token("--border"),
          borderWidth: 1,
          padding: [8, 10],
          textStyle: { color: token("--text-primary"), fontSize: 12 },
          formatter: (params: unknown) => {
            const [first] = params as { dataIndex: number }[];
            const point = first ? points[first.dataIndex] : undefined;
            if (!point) return "";
            // Values lead, labels follow; every string is escaped (labels are data, not markup).
            return [
              `<div style="font-weight:600;font-size:14px">${escapeHtml(formatValue(point.value, unit, { exact: true }))}</div>`,
              `<div style="display:flex;align-items:center;gap:6px;color:${token("--text-secondary")}">`,
              `<span style="display:inline-block;width:12px;height:2px;background:${series}"></span>`,
              `${escapeHtml(seriesLabel)}</div>`,
              `<div style="color:${token("--text-muted")}">${escapeHtml(formatBucket(point.bucket, granularity))}`,
              unit === "trips" ? "" : ` · ${escapeHtml(formatInteger(point.trips))} trips`,
              `</div>`,
            ].join("");
          },
        },
        series: [
          {
            type: "line",
            name: seriesLabel,
            data: points.map((p) => p.value),
            connectNulls: false,
            showSymbol: false,
            symbolSize: 8,
            lineStyle: { width: 2, color: series, cap: "round", join: "round" },
            itemStyle: { color: series, borderColor: surface, borderWidth: 2 },
            areaStyle: { color: series, opacity: 0.1 },
            emphasis: { disabled: true },
            endLabel: {
              show: lastIndex >= 0,
              color: token("--text-secondary"),
              formatter: () => formatValue(points[lastIndex]?.value ?? null, unit),
            },
          },
        ],
      },
      { notMerge: true },
    );
  }, [points, unit, seriesLabel, granularity, themeVersion]);

  return (
    <div
      ref={container}
      role="img"
      aria-label={`${seriesLabel} by ${granularity}, ${points.length} points. Use “Show table” for the values.`}
      className={`h-80 w-full transition-opacity ${dimmed ? "opacity-50" : "opacity-100"}`}
      data-testid="trips-chart"
    />
  );
}
