import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiFetch, toQueryString } from "./client";
import type {
  Coverage,
  DateFilters,
  Granularity,
  MetricId,
  OverviewResponse,
  TimeSeriesResponse,
  User,
} from "./types";

export const DATASET_ID = "nyc-tlc-yellow";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<User>("/auth/me"),
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 2,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      apiFetch<User>("/auth/login", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (user) => client.setQueryData(["me"], user),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<null>("/auth/logout", { method: "POST" }),
    onSettled: () => client.clear(),
  });
}

export function useDataset(datasetId: string = DATASET_ID) {
  return useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => apiFetch<Coverage>(`/datasets/${encodeURIComponent(datasetId)}`),
    staleTime: 60_000,
  });
}

export function useOverview(filters: DateFilters) {
  return useQuery({
    queryKey: ["overview", DATASET_ID, filters],
    queryFn: () => apiFetch<OverviewResponse>(`/analytics/overview${toQueryString({ dataset_id: DATASET_ID, ...filters })}`),
    placeholderData: keepPreviousData, // refetch keeps the previous frame instead of flashing a skeleton
  });
}

export function useTimeSeries(filters: DateFilters, metric: MetricId, granularity: Granularity) {
  return useQuery({
    queryKey: ["trips-over-time", DATASET_ID, filters, metric, granularity],
    queryFn: () =>
      apiFetch<TimeSeriesResponse>(
        `/analytics/trips-over-time${toQueryString({ dataset_id: DATASET_ID, ...filters, metric, granularity })}`,
      ),
    placeholderData: keepPreviousData,
  });
}
