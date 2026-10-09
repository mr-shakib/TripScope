import { useId, useState } from "react";

import type { Kpi, MetricDefinition } from "../api/types";
import { formatInteger, formatValue } from "../lib/format";

interface StatTileProps {
  definition: MetricDefinition;
  kpi: Kpi | undefined;
  hero?: boolean;
}

/** One KPI: label, value (never 0 for missing data), excluded-row note and an inline definition. */
export function StatTile({ definition, kpi, hero = false }: StatTileProps) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const value = kpi?.value ?? null;
  const exact = formatValue(value, definition.unit, { exact: true });

  return (
    <section
      aria-label={definition.label}
      className={`flex flex-col gap-1 rounded-xl border border-line bg-surface-1 p-4 ${hero ? "sm:col-span-2" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm text-ink-2">{definition.label}</h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={detailsId}
          onClick={() => setOpen((v) => !v)}
          className="rounded px-1.5 text-xs text-ink-muted hover:bg-surface-2 hover:text-ink"
        >
          {open ? "Hide definition" : "Definition"}
        </button>
      </div>
      <p
        data-testid={`kpi-${definition.id}`}
        data-value={value ?? ""}
        title={exact}
        className={`${hero ? "text-5xl" : "text-3xl"} font-semibold tracking-tight text-ink`}
      >
        {formatValue(value, definition.unit, { exact: hero })}
      </p>
      {kpi && kpi.excluded_rows > 0 ? (
        <p className="text-xs text-ink-muted">Excludes {formatInteger(kpi.excluded_rows)} flagged trips</p>
      ) : null}
      <div id={detailsId} hidden={!open} className="mt-2 space-y-1 border-t border-line pt-2 text-xs text-ink-2">
        <p>{definition.description}</p>
        <p>
          <span className="text-ink-muted">Rows included: </span>
          {definition.rows_included}
        </p>
        <p>
          <span className="text-ink-muted">Exact value: </span>
          <span className="tabular">{exact}</span>
        </p>
        {definition.caveats.length > 0 ? (
          <ul className="list-disc pl-4">
            {definition.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
