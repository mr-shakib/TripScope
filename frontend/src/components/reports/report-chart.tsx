"use client";

import type { ReportChartBlock, ReportUnit } from "@/api/types";
import { type ChartTheme, EChart, type EChartsOption, SEQUENTIAL_BLUE, tooltipBase } from "@/components/charts/echart";
import { escapeHtml, formatAxis, formatInteger, MISSING } from "@/lib/format";

/** Same palette and order as the PDF renderer (backend/src/tripscope/reports/render_pdf.py). */
export const REPORT_SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#7c5cc4"];
const OPEN_BUCKET = "#184f95";

export function formatReportValue(value: number | null | undefined, unit: ReportUnit): string {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  if (unit === "share") return `${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
  if (unit === "usd") return value.toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (unit === "miles") return `${value.toFixed(2)} mi`;
  if (unit === "minutes") return `${value.toFixed(1)} min`;
  return formatInteger(value);
}

function axisLabel(value: number, unit: ReportUnit): string {
  if (unit === "share") return `${Math.round(value * 1000) / 10}%`;
  return formatAxis(value, unit === "usd" ? "usd" : "trips");
}

function build(block: ReportChartBlock): (theme: ChartTheme) => EChartsOption {
  return (theme) => {
    const horizontal = block.kind === "bar";
    const categoryAxis = {
      type: "category" as const,
      data: horizontal ? [...block.categories].reverse() : block.categories,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: theme.axis } },
      axisLabel: { color: horizontal ? theme.ink : theme.muted, fontSize: horizontal ? 11 : 10, hideOverlap: true },
    };
    const valueAxis = {
      type: "value" as const,
      axisLabel: { color: theme.muted, fontSize: 10, formatter: (v: number) => axisLabel(v, block.unit) },
      splitLine: { lineStyle: { color: theme.grid } },
    };
    const tooltip = {
      ...tooltipBase(theme),
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { name: string; seriesName: string; value: number | null };
        const series = block.series.length > 1 ? `<div style="color:${theme.muted}">${escapeHtml(p.seriesName)}</div>` : "";
        return `<div style="font-weight:600">${escapeHtml(formatReportValue(p.value, block.unit))}</div><div style="color:${theme.ink2}">${escapeHtml(p.name)}</div>${series}`;
      },
    };
    if (block.kind === "line") {
      return {
        grid: { left: 4, right: 16, top: 12, bottom: 4, containLabel: true },
        xAxis: { ...categoryAxis, boundaryGap: false },
        yAxis: valueAxis,
        tooltip: { ...tooltip, trigger: "axis", formatter: undefined, valueFormatter: (v: number) => formatReportValue(v, block.unit) },
        series: block.series.map((s, i) => ({
          type: "line",
          name: s.name,
          data: s.values,
          showSymbol: false,
          lineStyle: { width: 1.6, color: REPORT_SERIES[i % REPORT_SERIES.length] },
          itemStyle: { color: REPORT_SERIES[i % REPORT_SERIES.length] },
        })),
      };
    }
    const values = horizontal ? [...(block.series[0]?.values ?? [])].reverse() : (block.series[0]?.values ?? []);
    const openIndex = block.kind === "histogram" && block.categories.at(-1)?.includes("+") ? values.length - 1 : -1;
    return {
      grid: { left: 4, right: horizontal ? 70 : 16, top: block.markers.length ? 22 : 12, bottom: 4, containLabel: true },
      xAxis: horizontal ? valueAxis : categoryAxis,
      yAxis: horizontal ? categoryAxis : valueAxis,
      tooltip,
      series: [
        {
          type: "bar",
          name: block.series[0]?.name ?? "",
          barCategoryGap: block.kind === "histogram" ? "12%" : "28%",
          data: values.map((v, i) => ({ value: v, itemStyle: { color: i === openIndex ? OPEN_BUCKET : REPORT_SERIES[0], borderRadius: horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0] } })),
          label: horizontal
            ? { show: true, position: "right", color: theme.ink2, fontSize: 11, formatter: (p: { value: number }) => formatReportValue(p.value, block.unit) }
            : { show: false },
          markLine: block.markers.length
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { color: theme.ink2, type: "solid", width: 1 },
                label: { color: theme.ink2, fontSize: 10, formatter: "{b}" },
                data: block.markers.map((m) => ({ name: m.label, xAxis: m.index })),
              }
            : undefined,
        },
      ],
    };
  };
}

function Heatmap({ block }: { block: ReportChartBlock }) {
  const cells = block.matrix.flatMap((row, r) => row.map((value, c) => [c, r, value] as [number, number, number | null]));
  const values = cells.map((c) => c[2]).filter((v): v is number => v !== null);
  return (
    <EChart
      testId={`report-chart-${block.id}`}
      ariaLabel={block.title}
      className="h-60"
      deps={[block]}
      build={(theme) => ({
        grid: { left: 4, right: 8, top: 4, bottom: 36, containLabel: true },
        xAxis: { type: "category", data: block.categories, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: theme.muted, fontSize: 10, interval: 2 }, splitArea: { show: false } },
        yAxis: { type: "category", data: block.rows, inverse: true, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: theme.ink2, fontSize: 11 } },
        visualMap: { min: Math.min(...values, 0), max: Math.max(...values, 1), orient: "horizontal", left: "center", bottom: 0, itemWidth: 10, itemHeight: 120, calculable: false, inRange: { color: SEQUENTIAL_BLUE }, textStyle: { color: theme.muted, fontSize: 10 }, formatter: (v: number) => axisLabel(v, block.unit) },
        tooltip: {
          ...tooltipBase(theme),
          formatter: (params: unknown) => {
            const [c, r, v] = (params as { value: [number, number, number | null] }).value;
            return `<div style="font-weight:600">${escapeHtml(formatReportValue(v, block.unit))} trips</div><div style="color:${theme.ink2}">${escapeHtml(`${block.rows[r] ?? ""} ${block.categories[c] ?? ""}:00`)}</div>`;
          },
        },
        series: [{ type: "heatmap", data: cells, itemStyle: { borderColor: theme.surface, borderWidth: 2 } }],
      })}
    />
  );
}

/** A report chart as the PDF draws it: same kind, palette, markers and order. */
export function ReportChart({ block }: { block: ReportChartBlock }) {
  if (block.kind === "heatmap") return <Heatmap block={block} />;
  const height = block.kind === "bar" ? Math.max(140, block.categories.length * 26 + 20) : 240;
  return (
    <div>
      {block.series.length > 1 ? (
        <ul className="mb-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-2" aria-label="Legend">
          {block.series.map((s, i) => (
            <li key={s.name} className="flex items-center gap-1.5">
              <span className="h-0.5 w-3 rounded" style={{ background: REPORT_SERIES[i % REPORT_SERIES.length] }} aria-hidden />
              {s.name}
            </li>
          ))}
        </ul>
      ) : null}
      <div style={{ height }}>
        <EChart testId={`report-chart-${block.id}`} ariaLabel={block.title} deps={[block]} build={build(block)} className="h-full w-full" />
      </div>
    </div>
  );
}
