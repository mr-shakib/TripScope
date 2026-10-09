import type { Coverage, DateFilters } from "../api/types";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Read the date filters from the URL; anything malformed is dropped rather than sent to the API. */
export function readDateFilters(params: URLSearchParams): DateFilters {
  const filters: DateFilters = {};
  const start = params.get("start");
  const end = params.get("end");
  if (start && ISO_DATE.test(start)) filters.start_date = start;
  if (end && ISO_DATE.test(end)) filters.end_date = end;
  return filters;
}

export function writeDateFilters(params: URLSearchParams, filters: DateFilters): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const [key, value] of [
    ["start", filters.start_date],
    ["end", filters.end_date],
  ] as const) {
    if (value) next.set(key, value);
    else next.delete(key);
  }
  return next;
}

function shiftDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export interface Preset {
  id: string;
  label: string;
  filters: DateFilters;
}

/** Presets relative to the published coverage (the data is historical, so "last 7 days" means of the data). */
export function coveragePresets(coverage: Coverage | undefined): Preset[] {
  if (!coverage?.start_date || !coverage.end_date) return [];
  const { start_date: start, end_date: end } = coverage;
  return [
    { id: "all", label: "All published data", filters: {} },
    { id: "first-7", label: "First 7 days", filters: { start_date: start, end_date: shiftDays(start, 6) } },
    { id: "last-7", label: "Last 7 days", filters: { start_date: shiftDays(end, -6), end_date: end } },
  ];
}

export function rangeDays(filters: DateFilters, coverage: Coverage | undefined): number | null {
  const start = filters.start_date ?? coverage?.start_date;
  const end = filters.end_date ?? coverage?.end_date;
  if (!start || !end) return null;
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000) + 1;
}

export const MAX_HOURLY_DAYS = 62;
