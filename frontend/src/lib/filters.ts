import type { Coverage, DashboardFilters } from "@/api/types";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export const EMPTY_FILTERS: DashboardFilters = {
  pickup_zone: [],
  dropoff_zone: [],
  payment_type: [],
  vendor_id: [],
  hour: [],
  weekday: [],
};

export type ListKey = "pickup_zone" | "dropoff_zone" | "payment_type" | "vendor_id" | "hour" | "weekday";

// Short URL keys keep shareable links readable: ?start=2025-01-01&end=2025-01-31&pz=132,161&h=7,8
const URL_KEYS: Record<ListKey, { key: string; min: number; max: number }> = {
  pickup_zone: { key: "pz", min: 1, max: 265 },
  dropoff_zone: { key: "dz", min: 1, max: 265 },
  payment_type: { key: "pt", min: 0, max: 6 },
  vendor_id: { key: "v", min: 1, max: 99 },
  hour: { key: "h", min: 0, max: 23 },
  weekday: { key: "wd", min: 1, max: 7 },
};

function readDistance(params: URLSearchParams, key: string): number | undefined {
  const raw = params.get(key);
  if (raw === null || raw === "") return undefined;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 && value <= 1000 ? value : undefined;
}

function readList(params: URLSearchParams, key: string, min: number, max: number): number[] {
  const raw = params.get(key);
  if (!raw) return [];
  const values = raw
    .split(",")
    .map((v) => Number(v))
    .filter((v) => Number.isInteger(v) && v >= min && v <= max);
  return [...new Set(values)].sort((a, b) => a - b);
}

/** Read filters from the URL; anything malformed or out of range is dropped rather than sent to the API. */
export function readFilters(params: URLSearchParams): DashboardFilters {
  const filters: DashboardFilters = { ...EMPTY_FILTERS };
  const start = params.get("start");
  const end = params.get("end");
  if (start && ISO_DATE.test(start)) filters.start_date = start;
  if (end && ISO_DATE.test(end)) filters.end_date = end;
  for (const [field, spec] of Object.entries(URL_KEYS) as [ListKey, (typeof URL_KEYS)[ListKey]][]) {
    filters[field] = readList(params, spec.key, spec.min, spec.max);
  }
  const min = readDistance(params, "dmin");
  const max = readDistance(params, "dmax");
  if (min !== undefined) filters.min_distance = min;
  if (max !== undefined && (min === undefined || max >= min)) filters.max_distance = max;
  return filters;
}

export function writeFilters(params: URLSearchParams, filters: DashboardFilters): URLSearchParams {
  const next = new URLSearchParams(params);
  const set = (key: string, value: string | undefined) => (value ? next.set(key, value) : next.delete(key));
  set("start", filters.start_date);
  set("end", filters.end_date);
  for (const [field, spec] of Object.entries(URL_KEYS) as [ListKey, (typeof URL_KEYS)[ListKey]][]) {
    set(spec.key, filters[field].length ? filters[field].join(",") : undefined);
  }
  set("dmin", filters.min_distance !== undefined ? String(filters.min_distance) : undefined);
  set("dmax", filters.max_distance !== undefined ? String(filters.max_distance) : undefined);
  return next;
}

/** Query parameters for the analytics API (lists repeat the key, as FastAPI expects). */
export function toApiParams(filters: DashboardFilters): Record<string, string | number | number[] | undefined> {
  return {
    start_date: filters.start_date,
    end_date: filters.end_date,
    pickup_zone: filters.pickup_zone,
    dropoff_zone: filters.dropoff_zone,
    payment_type: filters.payment_type,
    vendor_id: filters.vendor_id,
    hour: filters.hour,
    weekday: filters.weekday,
    min_distance: filters.min_distance,
    max_distance: filters.max_distance,
  };
}

/** The same filters as a JSON body (exports). */
export function toApiBody(filters: DashboardFilters): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(toApiParams(filters)).filter(([, v]) => v !== undefined && !(Array.isArray(v) && v.length === 0)),
  );
}

export function setDistance(filters: DashboardFilters, min?: number, max?: number): DashboardFilters {
  const next: DashboardFilters = { ...filters };
  delete next.min_distance;
  delete next.max_distance;
  if (min !== undefined) next.min_distance = min;
  if (max !== undefined) next.max_distance = max;
  return next;
}

export function toggleValue(filters: DashboardFilters, field: ListKey, value: number): DashboardFilters {
  const current = filters[field];
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value].sort((a, b) => a - b);
  return { ...filters, [field]: next };
}

export function setDates(filters: DashboardFilters, start?: string, end?: string): DashboardFilters {
  const next: DashboardFilters = { ...filters };
  delete next.start_date;
  delete next.end_date;
  if (start) next.start_date = start;
  if (end) next.end_date = end;
  return next;
}

export function activeFilterCount(filters: DashboardFilters): number {
  return (
    (filters.start_date || filters.end_date ? 1 : 0) +
    (filters.pickup_zone.length ? 1 : 0) +
    (filters.dropoff_zone.length ? 1 : 0) +
    (filters.payment_type.length ? 1 : 0) +
    (filters.vendor_id.length ? 1 : 0) +
    (filters.hour.length ? 1 : 0) +
    (filters.weekday.length ? 1 : 0) +
    (filters.min_distance !== undefined || filters.max_distance !== undefined ? 1 : 0)
  );
}

function shiftDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function monthEnd(period: string): string {
  const [year, month] = period.split("-").map(Number) as [number, number];
  return new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
}

export interface Preset {
  id: string;
  label: string;
  start?: string;
  end?: string;
}

/** Presets relative to the published data (it is historical, so "last 7 days" means of the data). */
export function coveragePresets(coverage: Coverage | undefined): Preset[] {
  if (!coverage?.start_date || !coverage.end_date) return [];
  const { start_date: start, end_date: end } = coverage;
  return [
    { id: "all", label: "All published data" },
    { id: "last-7", label: "Last 7 days", start: shiftDays(end, -6), end },
    { id: "last-30", label: "Last 30 days", start: shiftDays(end, -29), end },
    { id: "first-7", label: "First 7 days", start, end: shiftDays(start, 6) },
  ];
}

export function monthPresets(coverage: Coverage | undefined): Preset[] {
  return (coverage?.periods ?? []).map((p) => ({
    id: `month-${p.period}`,
    label: new Date(`${p.period}-01T00:00:00Z`).toLocaleString("en-US", { month: "short", year: "numeric", timeZone: "UTC" }),
    start: `${p.period}-01`,
    end: monthEnd(p.period),
  }));
}

export function rangeDays(filters: Pick<DashboardFilters, "start_date" | "end_date">, coverage: Coverage | undefined): number | null {
  const start = filters.start_date ?? coverage?.start_date;
  const end = filters.end_date ?? coverage?.end_date;
  if (!start || !end) return null;
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000) + 1;
}

export const MAX_HOURLY_DAYS = 62;

export const VENDORS: { id: number; label: string }[] = [
  { id: 1, label: "Creative Mobile Technologies" },
  { id: 2, label: "Curb Mobility" },
  { id: 6, label: "Myle Technologies" },
  { id: 7, label: "Helix" },
];

export const DISTANCE_PRESETS: { id: string; label: string; min?: number; max?: number }[] = [
  { id: "short", label: "Under 1 mi", max: 1 },
  { id: "1-3", label: "1–3 mi", min: 1, max: 3 },
  { id: "3-10", label: "3–10 mi", min: 3, max: 10 },
  { id: "long", label: "10 mi +", min: 10 },
];

export const PAYMENT_TYPES: { id: number; label: string }[] = [
  { id: 1, label: "Credit card" },
  { id: 2, label: "Cash" },
  { id: 0, label: "Flex Fare" },
  { id: 3, label: "No charge" },
  { id: 4, label: "Dispute" },
  { id: 5, label: "Unknown" },
];

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

export const HOUR_PRESETS: { id: string; label: string; hours: number[] }[] = [
  { id: "am-peak", label: "Morning peak", hours: [7, 8, 9] },
  { id: "pm-peak", label: "Evening peak", hours: [16, 17, 18, 19] },
  { id: "night", label: "Late night", hours: [0, 1, 2, 3, 4, 5] },
];
