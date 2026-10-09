"use client";

import { Info } from "lucide-react";

import type { Kpi, MetricDefinition } from "@/api/types";
import { Sparkline } from "@/components/charts/sparkline";
import { Popover } from "@/components/ui/overlays";
import { cn } from "@/lib/cn";
import { formatInteger, formatValue } from "@/lib/format";

/** One KPI. Missing data renders as "—", never 0; flagged-row exclusions are always disclosed. */
export function KpiCard({
  definition,
  kpi,
  hero = false,
  trend,
  dimmed = false,
}: {
  definition: MetricDefinition;
  kpi: Kpi | undefined;
  hero?: boolean;
  trend?: (number | null)[];
  dimmed?: boolean;
}) {
  const value = kpi?.value ?? null;
  return (
    <section
      aria-label={definition.label}
      className={cn(
        "group relative flex flex-col overflow-hidden rounded-2xl border border-line bg-surface p-5 shadow-card transition-[opacity,box-shadow] duration-200 hover:shadow-md",
        hero && "sm:col-span-2",
        dimmed && "opacity-55",
      )}
    >
      {hero ? <div aria-hidden className="pointer-events-none absolute -right-16 -top-20 size-56 rounded-full bg-accent-soft blur-2xl" /> : null}
      <div className="relative flex items-start justify-between gap-2">
        <h3 className="text-[13px] font-medium text-ink-2">{definition.label}</h3>
        <Popover
          align="end"
          className="w-80"
          trigger={
            <button type="button" aria-label={`How ${definition.label} is calculated`} className="-m-1 rounded-md p-1 text-ink-faint transition-colors hover:bg-surface-3 hover:text-ink-2">
              <Info className="size-4" />
            </button>
          }
        >
          <p className="text-sm font-semibold text-ink">{definition.label}</p>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{definition.description}</p>
          <dl className="mt-3 space-y-2 text-xs">
            <div>
              <dt className="text-ink-muted">Rows included</dt>
              <dd className="text-ink">{definition.rows_included}</dd>
            </div>
            <div>
              <dt className="text-ink-muted">Exact value</dt>
              <dd className="tabular font-medium text-ink">{formatValue(value, definition.unit, { exact: true })}</dd>
            </div>
          </dl>
          {definition.caveats.length ? (
            <ul className="mt-3 space-y-1 border-t border-line pt-3 text-xs leading-relaxed text-ink-2">
              {definition.caveats.map((c) => (
                <li key={c} className="flex gap-1.5">
                  <span className="mt-1.5 size-1 shrink-0 rounded-full bg-ink-faint" />
                  {c}
                </li>
              ))}
            </ul>
          ) : null}
        </Popover>
      </div>
      <p
        data-testid={`kpi-${definition.id}`}
        data-value={value ?? ""}
        className={cn("relative mt-2 font-semibold tracking-tight text-ink", hero ? "text-[44px] leading-none" : "text-[28px] leading-tight")}
      >
        {formatValue(value, definition.unit, { exact: hero })}
      </p>
      <div className="relative mt-auto pt-3">
        {trend ? <Sparkline values={trend} className={hero ? "h-14 w-full" : "h-9 w-full"} /> : null}
        <p className="mt-1.5 text-xs text-ink-muted">
          {kpi && kpi.excluded_rows > 0 ? `Excludes ${formatInteger(kpi.excluded_rows)} flagged trips` : hero ? "Accepted trips after validation" : " "}
        </p>
      </div>
    </section>
  );
}
