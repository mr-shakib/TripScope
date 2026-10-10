"use client";

import { Tabs } from "radix-ui";

import { useDataset } from "@/api/hooks";
import { AmountCard, DistanceCard, FlowsCard, HeatmapCard, PaymentTypesCard, VendorsCard } from "@/components/dashboards/cards";
import { ZoneMapCard } from "@/components/dashboards/zone-map-card";
import { FilterBar } from "@/components/filters/filter-bar";
import { PageHeader } from "@/components/layout/app-shell";
import { DefinitionsButton } from "@/components/layout/definitions-drawer";
import { HourCard, TopZonesCard, WeekdayCard } from "@/components/overview/breakdowns";
import { useUrlFilters, useUrlParam } from "@/hooks/use-url-filters";

const TABS = ["demand", "fares", "zones"] as const;
type Tab = (typeof TABS)[number];
const LABELS: Record<Tab, string> = { demand: "Demand patterns", fares: "Trips & fares", zones: "Zones & flows" };

export function DashboardsView() {
  const [filters, setFilters] = useUrlFilters();
  const [tab, setTab] = useUrlParam<Tab>("tab", "demand", TABS);
  const dataset = useDataset();
  const props = { filters, onFiltersChange: setFilters };

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Dashboards"
        description="Every chart answers to the same filters. Click bars, cells, zones or pairs to drill in; chips above show what is applied."
        actions={<DefinitionsButton />}
      />
      <div className="sticky top-14 z-20 -mx-4 mb-6 border-b border-line/70 bg-page/85 px-4 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <FilterBar coverage={dataset.data} filters={filters} onChange={setFilters} />
      </div>
      <Tabs.Root value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <Tabs.List aria-label="Dashboard sections" className="mb-6 inline-flex gap-1 rounded-xl bg-surface-3 p-1">
          {TABS.map((t) => (
            <Tabs.Trigger
              key={t}
              value={t}
              className="rounded-lg px-4 py-2 text-sm font-medium text-ink-2 transition-all hover:text-ink data-[state=active]:bg-surface data-[state=active]:text-ink data-[state=active]:shadow-[0_1px_3px_rgba(15,23,42,0.1)]"
            >
              {LABELS[t]}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="demand" className="space-y-6 outline-none animate-fade-in">
          <HeatmapCard {...props} />
          <div className="grid gap-6 xl:grid-cols-2">
            <HourCard {...props} />
            <WeekdayCard {...props} />
          </div>
          <div className="grid gap-6 xl:grid-cols-2">
            <PaymentTypesCard {...props} />
            <VendorsCard {...props} />
          </div>
        </Tabs.Content>
        <Tabs.Content value="fares" className="space-y-6 outline-none animate-fade-in">
          <div className="grid gap-6 xl:grid-cols-2">
            <DistanceCard {...props} />
            <AmountCard {...props} />
          </div>
        </Tabs.Content>
        <Tabs.Content value="zones" className="space-y-6 outline-none animate-fade-in">
          <ZoneMapCard {...props} height="h-[560px]" />
          <div className="grid gap-6 xl:grid-cols-2">
            <TopZonesCard {...props} side="pickup" />
            <TopZonesCard {...props} side="dropoff" />
          </div>
          <FlowsCard {...props} />
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}
