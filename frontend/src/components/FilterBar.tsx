import type { Coverage, DateFilters } from "../api/types";
import { coveragePresets } from "../lib/filters";
import { formatDate } from "../lib/format";

interface FilterBarProps {
  coverage: Coverage | undefined;
  filters: DateFilters;
  onChange: (filters: DateFilters) => void;
}

function withDate(filters: DateFilters, key: keyof DateFilters, value: string): DateFilters {
  const entries = Object.entries({ ...filters, [key]: value }).filter(([, v]) => Boolean(v));
  return Object.fromEntries(entries) as DateFilters;
}

/** One row above everything it scopes. Phase 1 exposes the date range; more dimensions arrive in Phase 3. */
export function FilterBar({ coverage, filters, onChange }: FilterBarProps) {
  const presets = coveragePresets(coverage);
  const min = coverage?.start_date ?? undefined;
  const max = coverage?.end_date ?? undefined;
  const active = Boolean(filters.start_date || filters.end_date);
  const activePreset = presets.find(
    (p) => p.filters.start_date === filters.start_date && p.filters.end_date === filters.end_date,
  );

  return (
    <div className="flex flex-wrap items-end gap-3" role="group" aria-label="Filters">
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Date range presets">
        {presets.map((preset) => {
          const selected = preset.id === activePreset?.id;
          return (
            <button
              key={preset.id}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(preset.filters)}
              className={`rounded-md border px-3 py-1.5 text-sm ${
                selected ? "border-accent bg-surface-1 font-medium text-ink" : "border-line text-ink-2 hover:bg-surface-2"
              }`}
            >
              {preset.label}
            </button>
          );
        })}
      </div>
      <label className="flex flex-col gap-1 text-xs text-ink-muted">
        From
        <input
          type="date"
          aria-label="Start date"
          value={filters.start_date ?? ""}
          min={min}
          max={filters.end_date ?? max}
          onChange={(e) => onChange(withDate(filters, "start_date", e.target.value))}
          className="rounded-md border border-line bg-surface-1 px-2 py-1.5 text-sm text-ink"
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-ink-muted">
        To
        <input
          type="date"
          aria-label="End date"
          value={filters.end_date ?? ""}
          min={filters.start_date ?? min}
          max={max}
          onChange={(e) => onChange(withDate(filters, "end_date", e.target.value))}
          className="rounded-md border border-line bg-surface-1 px-2 py-1.5 text-sm text-ink"
        />
      </label>
      <button
        type="button"
        onClick={() => onChange({})}
        disabled={!active}
        className="rounded-md px-3 py-1.5 text-sm text-accent-ink hover:bg-surface-2 disabled:cursor-not-allowed disabled:text-ink-muted"
      >
        Reset filters
      </button>
      <p className="basis-full text-xs text-ink-muted" aria-live="polite" data-testid="active-filters">
        Showing {active ? `${formatDate(filters.start_date ?? min)} – ${formatDate(filters.end_date ?? max)}` : "all published data"}
        {coverage?.start_date ? ` · Coverage ${formatDate(coverage.start_date)} – ${formatDate(coverage.end_date)}` : ""}
      </p>
    </div>
  );
}
