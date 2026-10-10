"use client";

import { Map as MapIcon } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useZoneGeometry, useZoneTotals } from "@/api/hooks";
import type { DashboardFilters, MetricId } from "@/api/types";
import { ZoneMap } from "@/components/charts/zone-map";
import { Card, CardHeader } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { toggleValue } from "@/lib/filters";

export function ZoneMapCard({
  filters,
  onFiltersChange,
  height,
}: {
  filters: DashboardFilters;
  onFiltersChange: (f: DashboardFilters) => void;
  height?: string;
}) {
  const [side, setSide] = useState<"pickup" | "dropoff">("pickup");
  const [metric, setMetric] = useState<MetricId>("total_trips");
  const geometry = useZoneGeometry();
  const totals = useZoneTotals(filters, side, metric);
  const field = side === "pickup" ? "pickup_zone" : "dropoff_zone";
  const missing = geometry.error instanceof ApiError && geometry.error.status === 404;
  return (
    <Card>
      <CardHeader
        title={`${side === "pickup" ? "Pickups" : "Drop-offs"} by taxi zone`}
        description="Colour uses a logarithmic scale. Scroll to zoom, drag to pan, click a zone to filter."
        actions={
          <>
            <Segmented label="Side" value={side} onChange={setSide} options={[{ value: "pickup", label: "Pickup" }, { value: "dropoff", label: "Drop-off" }]} />
            <Segmented label="Metric" value={metric} onChange={setMetric} options={[{ value: "total_trips", label: "Trips" }, { value: "total_recorded_amount", label: "Amount" }]} />
          </>
        }
      />
      <div className="px-3 pb-4 pt-2 sm:px-5">
        {geometry.isPending || totals.isPending ? (
          <LoadingBlock className={height ?? "h-[460px]"} label="Loading map…" />
        ) : missing ? (
          <EmptyBlock title="Zone boundaries not built yet" className={height ?? "h-[460px]"}>
            They are created automatically by the next pipeline run, or with <span className="font-mono">tripscope-pipeline build-zone-geometry</span>.
          </EmptyBlock>
        ) : geometry.isError || totals.isError ? (
          <ErrorBlock error={geometry.error ?? totals.error} className={height ?? "h-[460px]"} />
        ) : (
          <ZoneMap
            geometry={geometry.data!}
            groups={totals.data!.groups}
            unit={metric === "total_trips" ? "trips" : "usd"}
            selected={filters[field]}
            onToggle={(id) => onFiltersChange(toggleValue(filters, field, id))}
            dimmed={totals.isPlaceholderData}
            label={`${side === "pickup" ? "Pickups" : "Drop-offs"}`}
            {...(height ? { height } : {})}
          />
        )}
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-muted">
          <MapIcon className="size-3.5" /> Boundaries: NYC TLC taxi zones (shapefile reprojected to WGS84 and simplified). Grey zones have no trips in the selection.
        </p>
      </div>
    </Card>
  );
}
