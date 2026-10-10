"use client";

import { useMemo } from "react";

import type { BreakdownGroup, Unit, ZoneFeatureCollection } from "@/api/types";
import { escapeHtml, formatPercent, formatValue } from "@/lib/format";

import { EChart, registerGeoJson, SEQUENTIAL_BLUE, tooltipBase } from "./echart";

const MAP_NAME = "nyc-taxi-zones";

/** Taxi-zone choropleth. Colour = log10 of the value (zone totals span four orders of magnitude). */
export function ZoneMap({
  geometry,
  groups,
  unit,
  selected,
  onToggle,
  dimmed,
  height = "h-[460px]",
  label,
}: {
  geometry: ZoneFeatureCollection;
  groups: BreakdownGroup[];
  unit: Unit;
  selected: number[];
  onToggle: (zoneId: number) => void;
  dimmed: boolean;
  height?: string;
  label: string;
}) {
  // ECharts matches data to features by `name`; give each feature its zone id as the name.
  const named = useMemo(
    () => ({
      ...geometry,
      features: geometry.features.map((f) => ({ ...f, properties: { ...f.properties, name: String(f.id) } })),
    }),
    [geometry],
  );
  registerGeoJson(MAP_NAME, named);
  const byZone = useMemo(() => new Map(groups.filter((g) => g.key !== null).map((g) => [Number(g.key), g])), [groups]);
  const total = groups.reduce((sum, g) => sum + (g.value ?? 0), 0);
  const values = groups.map((g) => g.value ?? 0).filter((v) => v > 0);
  const maxLog = Math.log10(Math.max(...values, 10));

  return (
    <EChart
      testId="zone-map"
      ariaLabel={`${label} by taxi zone. Click a zone to filter. Zone values are listed in the ranked table.`}
      className={height}
      dimmed={dimmed}
      deps={[named, groups, selected, unit]}
      onClick={(p) => {
        const id = Number(p.name);
        if (Number.isFinite(id)) onToggle(id);
      }}
      build={(theme) => ({
        tooltip: {
          ...tooltipBase(theme),
          trigger: "item",
          formatter: (params: unknown) => {
            const { name } = params as { name: string };
            const feature = geometry.features.find((f) => String(f.id) === name);
            const group = byZone.get(Number(name));
            if (!feature) return "";
            const value = group ? formatValue(group.value, unit, { exact: unit === "trips" }) : "No trips";
            return [
              `<div style="font-weight:600;font-size:14px">${escapeHtml(value)}</div>`,
              `<div style="color:${theme.ink2}">${escapeHtml(feature.properties.zone)} · ${escapeHtml(feature.properties.borough)}</div>`,
              group ? `<div style="color:${theme.muted}">${escapeHtml(formatPercent(group.value, total))} of the selection · zone ${feature.id}</div>` : "",
            ].join("");
          },
        },
        visualMap: {
          type: "continuous",
          min: 0,
          max: maxLog,
          calculable: false,
          orient: "vertical",
          left: 8,
          bottom: 12,
          itemHeight: 120,
          itemWidth: 10,
          inRange: { color: SEQUENTIAL_BLUE },
          text: [formatValue(10 ** maxLog, unit), formatValue(1, unit)],
          textStyle: { color: theme.muted, fontSize: 10 },
          formatter: (value: number) => formatValue(10 ** value, unit),
        },
        series: [
          {
            type: "map",
            map: MAP_NAME,
            roam: true,
            scaleLimit: { min: 1, max: 8 },
            aspectScale: 0.76, // ≈ cos(40.7°): undo longitude stretching at NYC's latitude
            layoutCenter: ["52%", "50%"],
            layoutSize: "100%",
            selectedMode: false,
            itemStyle: { areaColor: theme.line, borderColor: theme.surface, borderWidth: 0.6 },
            emphasis: { label: { show: false }, itemStyle: { areaColor: "#f6b73c", borderColor: theme.ink, borderWidth: 1 } },
            data: groups
              .filter((g) => g.key !== null && (g.value ?? 0) > 0)
              .map((g) => ({
                name: String(g.key),
                value: Math.log10(g.value ?? 1),
                ...(selected.includes(Number(g.key)) ? { itemStyle: { borderColor: theme.ink, borderWidth: 2 } } : {}),
              })),
          },
        ],
      })}
    />
  );
}
