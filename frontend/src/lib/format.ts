import type { Unit } from "../api/types";

const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
const usdCompact = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const oneDecimal = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const twoDecimals = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Unavailable values render as an em dash, never as 0. */
export const MISSING = "—";

export function formatInteger(value: number | null | undefined): string {
  return value === null || value === undefined ? MISSING : integer.format(value);
}

/** Short display for stat tiles and axes. */
export function formatValue(value: number | null | undefined, unit: Unit, { exact = false } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  switch (unit) {
    case "trips":
      return exact || Math.abs(value) < 100_000 ? integer.format(value) : compact.format(value);
    case "usd":
      return exact || Math.abs(value) < 10_000 ? usd.format(value) : usdCompact.format(value);
    case "miles":
      return `${twoDecimals.format(value)} mi`;
    case "minutes":
      return `${oneDecimal.format(value)} min`;
  }
}

export function formatAxis(value: number, unit: Unit): string {
  if (unit === "usd") return Math.abs(value) >= 1000 ? usdCompact.format(value) : usd.format(value);
  if (unit === "trips") return Math.abs(value) >= 10_000 ? compact.format(value) : integer.format(value);
  return twoDecimals.format(value);
}

const dateFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const dateTimeFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

/** Dates from the API are NYC local calendar dates; format them without shifting into the browser's zone. */
export function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return MISSING;
  return dateFormat.format(new Date(`${isoDate.slice(0, 10)}T00:00:00Z`));
}

export function formatBucket(bucket: string, granularity: "hour" | "day" | "month"): string {
  if (granularity === "hour") return dateTimeFormat.format(new Date(`${bucket.replace(" ", "T").slice(0, 19)}Z`));
  if (granularity === "month") return bucket.slice(0, 7);
  return formatDate(bucket);
}

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}
