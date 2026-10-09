import type { Unit } from "@/api/types";

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

const percentFormat = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 2 });
const percentPrecise = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 3 });

/** Percent that never rounds a non-zero share to 0% or a partial share to 100%. */
export function formatPercent(part: number | null | undefined, whole: number | null | undefined): string {
  if (part == null || !whole) return MISSING;
  const ratio = part / whole;
  if (ratio > 0 && ratio < 0.0001) return "<0.01%";
  if (ratio < 1 && ratio >= 0.9999) {
    // e.g. 24,082,454 of 24,083,384 → 99.996%, not 100%
    const digits = Math.min(6, Math.max(2, Math.ceil(-Math.log10(1 - ratio)) - 2));
    return `${(Math.floor(ratio * 10 ** (digits + 2)) / 10 ** digits).toFixed(digits)}%`;
  }
  return (ratio < 0.01 ? percentPrecise : percentFormat).format(ratio);
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return MISSING;
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return MISSING;
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

const relative = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });

export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return MISSING;
  const seconds = (Date.parse(iso) - now) / 1000;
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return relative.format(Math.round(seconds / 60), "minute");
  if (abs < 86_400) return relative.format(Math.round(seconds / 3600), "hour");
  return relative.format(Math.round(seconds / 86_400), "day");
}

const timestampFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** Event times (job runs, logs) are real instants; show them in the viewer's local time. */
export function formatTimestamp(iso: string | null | undefined): string {
  return iso ? timestampFormat.format(new Date(iso)) : MISSING;
}

export function humanize(id: string): string {
  const text = id.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
