"use client";

import type { MatrixResponse } from "@/api/types";
import { WEEKDAYS } from "@/lib/filters";
import { escapeHtml, formatInteger, formatPercent } from "@/lib/format";

import { EChart, SEQUENTIAL_BLUE, tooltipBase } from "./echart";

const HOURS = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}`);

/** Hour × weekday trip intensity. Click a cell to filter to that hour and day. */
export function HourWeekdayHeatmap({
  cells,
  selectedHours,
  selectedDays,
  onSelect,
  dimmed,
}: {
  cells: MatrixResponse["cells"];
  selectedHours: number[];
  selectedDays: number[];
  onSelect: (weekday: number, hour: number) => void;
  dimmed: boolean;
}) {
  const total = cells.reduce((sum, c) => sum + c.trips, 0);
  const max = Math.max(...cells.map((c) => c.trips), 1);
  const isSelected = (d: number, h: number) =>
    (selectedDays.length === 0 || selectedDays.includes(d)) && (selectedHours.length === 0 || selectedHours.includes(h));
  const filtered = selectedDays.length > 0 || selectedHours.length > 0;
  return (
    <EChart
      testId="hour-weekday-heatmap"
      ariaLabel="Trips by hour of day and day of week. Click a cell to filter to that hour and day."
      className="h-[300px]"
      dimmed={dimmed}
      deps={[cells, selectedHours, selectedDays]}
      onClick={(p) => {
        const [hour, row] = (p.value as [number, number, number]) ?? [];
        if (hour !== undefined && row !== undefined) onSelect(row + 1, hour);
      }}
      build={(theme) => ({
        grid: { left: 4, right: 70, top: 8, bottom: 4, containLabel: true },
        xAxis: { type: "category", data: HOURS, splitArea: { show: false }, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: theme.muted, fontSize: 10 } },
        yAxis: { type: "category", data: [...WEEKDAYS], inverse: true, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: theme.ink2, fontSize: 11 } },
        visualMap: {
          type: "continuous",
          min: 0,
          max,
          orient: "vertical",
          right: 0,
          top: "middle",
          itemHeight: 140,
          itemWidth: 10,
          inRange: { color: SEQUENTIAL_BLUE },
          text: [max >= 1000 ? `${Math.round(max / 1000)}K` : String(max), "0"],
          textGap: 6,
          textStyle: { color: theme.muted, fontSize: 10 },
        },
        tooltip: {
          ...tooltipBase(theme),
          formatter: (params: unknown) => {
            const [hour, row, trips] = (params as { value: [number, number, number] }).value;
            return `<div style="font-weight:600;font-size:14px">${escapeHtml(formatInteger(trips))} trips</div><div style="color:${theme.ink2}">${WEEKDAYS[row]} ${HOURS[hour]}:00–${HOURS[hour]}:59</div><div style="color:${theme.muted}">${escapeHtml(formatPercent(trips, total))} of the selection · click to filter</div>`;
          },
        },
        series: [
          {
            type: "heatmap",
            data: cells.map((c) => ({
              value: [c.hour, c.weekday - 1, c.trips],
              itemStyle: { opacity: filtered && !isSelected(c.weekday, c.hour) ? 0.25 : 1 },
            })),
            itemStyle: { borderColor: theme.surface, borderWidth: 2, borderRadius: 3 },
            emphasis: { itemStyle: { borderColor: theme.ink, borderWidth: 1.5 } },
            cursor: "pointer",
          },
        ],
      })}
    />
  );
}
