"use client";

import { CalendarRange, Check, ChevronDown, Clock, CreditCard, MapPin, RotateCcw, Search, X } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";

import { useZones } from "@/api/hooks";
import type { Coverage, DashboardFilters, Zone } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Popover } from "@/components/ui/overlays";
import { cn } from "@/lib/cn";
import {
  activeFilterCount,
  coveragePresets,
  EMPTY_FILTERS,
  HOUR_PRESETS,
  monthPresets,
  PAYMENT_TYPES,
  setDates,
  toggleValue,
  WEEKDAYS,
} from "@/lib/filters";
import { formatDate } from "@/lib/format";

interface FilterBarProps {
  coverage: Coverage | undefined;
  filters: DashboardFilters;
  onChange: (filters: DashboardFilters) => void;
}

function TriggerButton({ icon, label, value, active, ...props }: { icon: ReactNode; label: string; value: string; active: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        "inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-[13px] shadow-card transition-all",
        active
          ? "border-accent/50 bg-accent-soft text-accent-strong ring-1 ring-accent/15"
          : "border-line-strong bg-surface text-ink-2 hover:border-ink-faint hover:text-ink",
      )}
    >
      <span className={cn(active ? "text-accent" : "text-ink-muted")}>{icon}</span>
      <span className="text-ink-muted">{label}</span>
      <span className={cn("font-medium", active ? "text-accent-strong" : "text-ink")}>{value}</span>
      <ChevronDown className="size-3.5 opacity-60" />
    </button>
  );
}

function OptionChip({ selected, onClick, children, testId }: { selected: boolean; onClick: () => void; children: ReactNode; testId?: string }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      data-testid={testId}
      onClick={onClick}
      className={cn(
        "inline-flex items-center justify-center gap-1 rounded-lg border px-2.5 py-1.5 text-[13px] font-medium transition-all",
        selected ? "border-accent bg-accent text-white shadow-[0_1px_2px_rgba(28,92,171,0.35)]" : "border-line-strong bg-surface text-ink-2 hover:border-ink-faint hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function DateRange({ coverage, filters, onChange }: FilterBarProps) {
  const presets = coveragePresets(coverage);
  const months = monthPresets(coverage);
  const min = coverage?.start_date ?? undefined;
  const max = coverage?.end_date ?? undefined;
  const active = Boolean(filters.start_date || filters.end_date);
  const label = active ? `${formatDate(filters.start_date ?? min)} – ${formatDate(filters.end_date ?? max)}` : "All published data";
  const isPreset = (p: { start?: string; end?: string }) => p.start === filters.start_date && p.end === filters.end_date;

  return (
    <Popover
      className="w-[min(92vw,460px)] p-0"
      trigger={<TriggerButton icon={<CalendarRange className="size-4" />} label="Dates" value={label} active={active} data-testid="filter-dates" />}
    >
      <div className="grid gap-0 sm:grid-cols-[170px_1fr]">
        <div className="border-b border-line p-2 sm:border-b-0 sm:border-r">
          {presets.map((preset) => (
            <button
              key={preset.id}
              type="button"
              onClick={() => onChange(setDates(filters, preset.start, preset.end))}
              className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-[13px] text-ink-2 hover:bg-surface-3 hover:text-ink"
            >
              {preset.label}
              {isPreset(preset) ? <Check className="size-4 text-accent" strokeWidth={2.5} /> : null}
            </button>
          ))}
        </div>
        <div className="space-y-4 p-4">
          <div>
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">Months</p>
            <div className="grid grid-cols-3 gap-1.5">
              {months.map((m) => (
                <OptionChip key={m.id} selected={isPreset(m)} onClick={() => onChange(setDates(filters, m.start, m.end))}>
                  {m.label.replace(" 2025", "")}
                </OptionChip>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(["start_date", "end_date"] as const).map((key) => (
              <label key={key} className="text-xs text-ink-muted">
                {key === "start_date" ? "From" : "To"}
                <input
                  type="date"
                  aria-label={key === "start_date" ? "Start date" : "End date"}
                  value={filters[key] ?? ""}
                  min={key === "end_date" ? (filters.start_date ?? min) : min}
                  max={key === "start_date" ? (filters.end_date ?? max) : max}
                  onChange={(e) =>
                    onChange(
                      key === "start_date"
                        ? setDates(filters, e.target.value || undefined, filters.end_date)
                        : setDates(filters, filters.start_date, e.target.value || undefined),
                    )
                  }
                  className="mt-1 h-9 w-full rounded-lg border border-line-strong bg-surface px-2 text-[13px] text-ink outline-none focus:border-accent focus:ring-4 focus:ring-[var(--accent-ring)]"
                />
              </label>
            ))}
          </div>
          <p className="text-[11px] text-ink-muted">Pickup dates, NYC local time. Coverage {formatDate(min)} – {formatDate(max)}.</p>
        </div>
      </div>
    </Popover>
  );
}

const AIRPORTS = [132, 138, 1];
const BOROUGH_ORDER = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR"];

function ZonePicker({ filters, onChange }: Omit<FilterBarProps, "coverage">) {
  const zones = useZones();
  const [query, setQuery] = useState("");
  const selected = filters.pickup_zone;
  const grouped = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = (zones.data ?? []).filter(
      (z) => !needle || z.zone.toLowerCase().includes(needle) || z.borough.toLowerCase().includes(needle) || String(z.id) === needle,
    );
    const groups = new Map<string, Zone[]>();
    for (const zone of list) {
      const key = zone.is_geographic ? zone.borough : "Unmapped IDs";
      groups.set(key, [...(groups.get(key) ?? []), zone]);
    }
    return [...groups.entries()].sort(
      ([a], [b]) => (BOROUGH_ORDER.indexOf(a) + 1 || 99) - (BOROUGH_ORDER.indexOf(b) + 1 || 99),
    );
  }, [zones.data, query]);
  const names = new Map((zones.data ?? []).map((z) => [z.id, z.zone]));
  const value = selected.length === 0 ? "All zones" : selected.length === 1 ? (names.get(selected[0]!) ?? `#${selected[0]}`) : `${selected.length} zones`;

  return (
    <Popover
      className="w-[min(92vw,380px)] p-0"
      trigger={<TriggerButton icon={<MapPin className="size-4" />} label="Pickup" value={value} active={selected.length > 0} data-testid="filter-zones" />}
    >
      <div className="border-b border-line p-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-faint" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search zones or boroughs"
            aria-label="Search pickup zones"
            className="h-9 w-full rounded-lg border border-line-strong bg-surface pl-8 pr-3 text-[13px] outline-none focus:border-accent focus:ring-4 focus:ring-[var(--accent-ring)]"
          />
        </div>
        <div className="mt-2 flex gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => onChange({ ...filters, pickup_zone: AIRPORTS })}>
            Airports
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onChange({ ...filters, pickup_zone: [] })} disabled={!selected.length}>
            Clear
          </Button>
        </div>
      </div>
      <div className="scroll-thin max-h-80 overflow-y-auto p-1.5" role="listbox" aria-multiselectable aria-label="Pickup zones">
        {grouped.map(([borough, list]) => (
          <div key={borough} className="mb-1">
            <p className="sticky top-0 bg-surface px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">{borough}</p>
            {list.map((zone) => {
              const isSelected = selected.includes(zone.id);
              return (
                <button
                  key={zone.id}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  onClick={() => onChange(toggleValue(filters, "pickup_zone", zone.id))}
                  className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink-2 hover:bg-surface-3 hover:text-ink"
                >
                  <span className={cn("flex size-4 shrink-0 items-center justify-center rounded border", isSelected ? "border-accent bg-accent text-white" : "border-line-strong")}>
                    {isSelected ? <Check className="size-3" strokeWidth={3} /> : null}
                  </span>
                  <span className="flex-1 truncate">{zone.is_geographic ? zone.zone : `${zone.zone || zone.borough} (not a zone)`}</span>
                  <span className="tabular text-[11px] text-ink-faint">#{zone.id}</span>
                </button>
              );
            })}
          </div>
        ))}
        {grouped.length === 0 ? <p className="px-3 py-6 text-center text-sm text-ink-muted">No zones match “{query}”.</p> : null}
      </div>
    </Popover>
  );
}

function PaymentPicker({ filters, onChange }: Omit<FilterBarProps, "coverage">) {
  const selected = filters.payment_type;
  const value = selected.length === 0 ? "Any" : selected.length === 1 ? (PAYMENT_TYPES.find((p) => p.id === selected[0])?.label ?? "1") : `${selected.length} types`;
  return (
    <Popover
      className="w-72"
      trigger={<TriggerButton icon={<CreditCard className="size-4" />} label="Payment" value={value} active={selected.length > 0} data-testid="filter-payment" />}
    >
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">Payment type</p>
      <div className="grid grid-cols-2 gap-1.5">
        {PAYMENT_TYPES.map((p) => (
          <OptionChip key={p.id} selected={selected.includes(p.id)} onClick={() => onChange(toggleValue(filters, "payment_type", p.id))}>
            {p.label}
          </OptionChip>
        ))}
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-ink-muted">Codes from the TLC data dictionary. Flex Fare trips have no passenger count or rate code.</p>
    </Popover>
  );
}

function TimePicker({ filters, onChange }: Omit<FilterBarProps, "coverage">) {
  const hours = filters.hour;
  const days = filters.weekday;
  const parts = [hours.length ? `${hours.length}h` : null, days.length ? days.map((d) => WEEKDAYS[d - 1]).join(", ") : null].filter(Boolean);
  return (
    <Popover
      className="w-[min(92vw,360px)]"
      trigger={<TriggerButton icon={<Clock className="size-4" />} label="Time" value={parts.join(" · ") || "Any time"} active={parts.length > 0} data-testid="filter-time" />}
    >
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">Day of week</p>
      <div className="grid grid-cols-7 gap-1">
        {WEEKDAYS.map((day, i) => (
          <OptionChip key={day} selected={days.includes(i + 1)} onClick={() => onChange(toggleValue(filters, "weekday", i + 1))}>
            {day.slice(0, 2)}
          </OptionChip>
        ))}
      </div>
      <div className="mb-2 mt-4 flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">Pickup hour</p>
        <div className="flex gap-1">
          {HOUR_PRESETS.map((preset) => (
            <button key={preset.id} type="button" onClick={() => onChange({ ...filters, hour: preset.hours })} className="rounded-md px-1.5 py-0.5 text-[11px] text-accent-strong hover:bg-accent-soft">
              {preset.label}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-6 gap-1">
        {Array.from({ length: 24 }, (_, h) => (
          <OptionChip key={h} selected={hours.includes(h)} onClick={() => onChange(toggleValue(filters, "hour", h))} testId={`hour-${h}`}>
            <span className="tabular">{String(h).padStart(2, "0")}</span>
          </OptionChip>
        ))}
      </div>
    </Popover>
  );
}

function Chip({ children, onRemove, label }: { children: ReactNode; onRemove: () => void; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line bg-surface py-0.5 pl-2.5 pr-1 text-xs text-ink-2 shadow-card animate-fade-in">
      {children}
      <button type="button" onClick={onRemove} aria-label={`Remove ${label}`} className="rounded-full p-0.5 text-ink-faint hover:bg-surface-3 hover:text-ink">
        <X className="size-3" />
      </button>
    </span>
  );
}

export function FilterBar({ coverage, filters, onChange }: FilterBarProps) {
  const zones = useZones();
  const names = new Map((zones.data ?? []).map((z) => [z.id, z.zone]));
  const count = activeFilterCount(filters);
  return (
    <div className="space-y-2.5" role="group" aria-label="Filters">
      <div className="flex flex-wrap items-center gap-2">
        <DateRange coverage={coverage} filters={filters} onChange={onChange} />
        <ZonePicker filters={filters} onChange={onChange} />
        <PaymentPicker filters={filters} onChange={onChange} />
        <TimePicker filters={filters} onChange={onChange} />
        {count > 0 ? (
          <Button variant="ghost" size="sm" onClick={() => onChange(EMPTY_FILTERS)} className="text-accent-strong">
            <RotateCcw className="size-3.5" /> Reset
          </Button>
        ) : null}
      </div>
      {count > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="active-filters" aria-live="polite">
          {filters.start_date || filters.end_date ? (
            <Chip label="date range" onRemove={() => onChange(setDates(filters))}>
              {formatDate(filters.start_date ?? coverage?.start_date)} – {formatDate(filters.end_date ?? coverage?.end_date)}
            </Chip>
          ) : null}
          {filters.pickup_zone.map((id) => (
            <Chip key={`z${id}`} label={`zone ${id}`} onRemove={() => onChange(toggleValue(filters, "pickup_zone", id))}>
              {names.get(id) ?? `Zone ${id}`}
            </Chip>
          ))}
          {filters.payment_type.map((id) => (
            <Chip key={`p${id}`} label="payment type" onRemove={() => onChange(toggleValue(filters, "payment_type", id))}>
              {PAYMENT_TYPES.find((p) => p.id === id)?.label}
            </Chip>
          ))}
          {filters.weekday.map((d) => (
            <Chip key={`w${d}`} label={WEEKDAYS[d - 1] ?? "day"} onRemove={() => onChange(toggleValue(filters, "weekday", d))}>
              {WEEKDAYS[d - 1]}
            </Chip>
          ))}
          {filters.hour.length ? (
            <Chip label="hours" onRemove={() => onChange({ ...filters, hour: [] })}>
              {filters.hour.length <= 4 ? filters.hour.map((h) => `${String(h).padStart(2, "0")}:00`).join(", ") : `${filters.hour.length} hours`}
            </Chip>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
