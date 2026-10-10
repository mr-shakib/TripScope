"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import type { DashboardFilters } from "@/api/types";
import { readFilters, writeFilters } from "@/lib/filters";

/**
 * Merge a change into the *current* query string and replace the history entry in place.
 * Reading window.location at call time (not the render-time useSearchParams snapshot) means two quick
 * changes — a tab switch then a filter reset — compose instead of the second overwriting the first.
 * Native replaceState is synchronous and Next keeps useSearchParams in sync; no server round trip is needed
 * because these pages render entirely on the client from the URL.
 */
function replaceQuery(update: (query: URLSearchParams) => URLSearchParams) {
  const text = update(new URLSearchParams(window.location.search)).toString();
  // null, not history.state: Next ignores calls whose state carries its own marker and would not sync the router.
  window.history.replaceState(null, "", text ? `${window.location.pathname}?${text}` : window.location.pathname);
}

/** Filters live in the URL so views are shareable and every page sees the same slice. */
export function useUrlFilters(): [DashboardFilters, (next: DashboardFilters) => void] {
  const params = useSearchParams();
  const filters = useMemo(() => readFilters(new URLSearchParams(params.toString())), [params]);
  const setFilters = useCallback((next: DashboardFilters) => replaceQuery((query) => writeFilters(query, next)), []);
  return [filters, setFilters];
}

/** Another URL parameter (e.g. the active tab) that should survive filter changes. */
export function useUrlParam<T extends string>(key: string, fallback: T, allowed: readonly T[]): [T, (value: T) => void] {
  const params = useSearchParams();
  const raw = params.get(key);
  const value = raw && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback;
  const setValue = useCallback(
    (next: T) =>
      replaceQuery((query) => {
        if (next === fallback) query.delete(key);
        else query.set(key, next);
        return query;
      }),
    [key, fallback],
  );
  return [value, setValue];
}
