"use client";

import { BarChart, LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, MarkAreaComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/cn";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, DataZoomComponent, MarkAreaComponent, SVGRenderer]);

export type EChartsOption = echarts.EChartsCoreOption;

export interface ChartTheme {
  series: string;
  seriesSoft: string;
  surface: string;
  ink: string;
  ink2: string;
  muted: string;
  grid: string;
  axis: string;
  line: string;
  accentSoft: string;
}

function readTheme(): ChartTheme {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    series: v("--series-1"),
    seriesSoft: v("--accent-soft"),
    surface: v("--surface"),
    ink: v("--ink"),
    ink2: v("--ink-2"),
    muted: v("--ink-muted"),
    grid: v("--grid"),
    axis: v("--axis"),
    line: v("--line"),
    accentSoft: v("--accent-soft"),
  };
}

/** Shared tooltip chrome: surface card, hairline border, values lead (see `tooltipHtml`). */
export function tooltipBase(theme: ChartTheme) {
  return {
    backgroundColor: theme.surface,
    borderColor: theme.line,
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: theme.ink, fontSize: 12, fontFamily: "inherit" },
    extraCssText: "border-radius:10px;box-shadow:0 10px 30px rgba(15,23,42,.12);",
  };
}

export function EChart({
  build,
  deps,
  onClick,
  onDataZoom,
  className,
  ariaLabel,
  dimmed = false,
  testId,
}: {
  build: (theme: ChartTheme) => EChartsOption;
  deps: unknown[];
  onClick?: (params: { dataIndex: number; name: string; value: unknown }) => void;
  onDataZoom?: (range: { startIndex: number; endIndex: number }) => void;
  className?: string;
  ariaLabel: string;
  dimmed?: boolean;
  testId?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const handlers = useRef({ onClick, onDataZoom });
  useEffect(() => {
    handlers.current = { onClick, onDataZoom };
  });

  useEffect(() => {
    if (!container.current) return;
    const instance = echarts.init(container.current, undefined, { renderer: "svg" });
    chart.current = instance;
    instance.on("click", (params) => {
      const p = params as { dataIndex: number; name: string; value: unknown };
      handlers.current.onClick?.(p);
    });
    instance.on("datazoom", () => {
      const option = instance.getOption() as { dataZoom?: { startValue?: number; endValue?: number }[] };
      const zoom = option.dataZoom?.[0];
      if (zoom?.startValue !== undefined && zoom.endValue !== undefined) {
        handlers.current.onDataZoom?.({ startIndex: zoom.startValue, endIndex: zoom.endValue });
      }
    });
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(container.current);
    return () => {
      resize.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(build(readTheme()), { notMerge: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- callers list the data the option depends on
  }, deps);

  return (
    <div
      ref={container}
      role="img"
      aria-label={ariaLabel}
      data-testid={testId}
      className={cn("w-full transition-opacity duration-200", dimmed ? "opacity-45" : "opacity-100", className)}
    />
  );
}
