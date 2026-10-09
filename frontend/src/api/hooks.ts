"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { toApiParams } from "@/lib/filters";

import { ApiError, apiFetch, toQueryString } from "./client";
import type {
  BreakdownResponse,
  Coverage,
  DashboardFilters,
  DataSourceItem,
  Granularity,
  Job,
  JobStatus,
  LogEntry,
  MetricId,
  OverviewResponse,
  QualityResponse,
  RunSummary,
  SchemaReport,
  TimeSeriesResponse,
  User,
  Zone,
} from "./types";

export const DATASET_ID = "nyc-tlc-yellow";

const noRetryOnAuth = (count: number, error: unknown) =>
  !(error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) && count < 2;

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<User>("/auth/me"),
    retry: noRetryOnAuth,
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
    retry: noRetryOnAuth,
  });
}

function analyticsQuery<T>(path: string, filters: DashboardFilters, extra: Record<string, string | number | undefined> = {}) {
  return {
    queryKey: ["analytics", path, filters, extra],
    queryFn: () => apiFetch<T>(`/analytics/${path}${toQueryString({ dataset_id: DATASET_ID, ...toApiParams(filters), ...extra })}`),
    placeholderData: keepPreviousData, // refetch keeps the previous frame (dimmed) instead of flashing
    retry: noRetryOnAuth,
  };
}

export function useOverview(filters: DashboardFilters) {
  return useQuery(analyticsQuery<OverviewResponse>("overview", filters));
}

export function useTimeSeries(filters: DashboardFilters, metric: MetricId, granularity: Granularity) {
  return useQuery(analyticsQuery<TimeSeriesResponse>("trips-over-time", filters, { metric, granularity }));
}

export function useBreakdown(
  path: "trips-by-hour" | "trips-by-weekday" | "top-pickup-zones",
  filters: DashboardFilters,
  metric: MetricId,
  limit?: number,
) {
  return useQuery(analyticsQuery<BreakdownResponse>(path, filters, { metric, limit }));
}

export function useZones() {
  return useQuery({
    queryKey: ["zones"],
    queryFn: () => apiFetch<{ zones: Zone[] }>("/analytics/zones").then((r) => r.zones),
    staleTime: Infinity,
  });
}

const ACTIVE: JobStatus[] = ["queued", "running"];

export function useJobs(status?: JobStatus) {
  return useQuery({
    queryKey: ["jobs", status ?? "all"],
    queryFn: () => apiFetch<{ jobs: Job[] }>(`/ingestion-jobs${toQueryString({ status, limit: 100 })}`).then((r) => r.jobs),
    // Poll while anything is in flight so status, stage and timings update live.
    refetchInterval: (query) => (query.state.data?.some((j) => ACTIVE.includes(j.status)) ? 2000 : 15_000),
    retry: noRetryOnAuth,
  });
}

export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => apiFetch<Job>(`/ingestion-jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && ACTIVE.includes(query.state.data.status) ? 1500 : false),
  });
}

export function useRunLogs(runId: string | null, live: boolean) {
  return useQuery({
    queryKey: ["run-logs", runId],
    queryFn: () => apiFetch<RunSummary & { logs: LogEntry[] }>(`/processing-runs/${runId}/logs`),
    enabled: Boolean(runId),
    refetchInterval: live ? 2000 : false,
  });
}

export function useDataSources() {
  return useQuery({
    queryKey: ["data-sources"],
    queryFn: () => apiFetch<{ sources: DataSourceItem[] }>("/data-sources").then((r) => r.sources),
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.latest_job && ACTIVE.includes(s.latest_job.status)) ? 2000 : 30_000,
    retry: noRetryOnAuth,
  });
}

function useJobMutation<TVars>(fn: (vars: TVars) => Promise<Job>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (job) => {
      client.setQueryData(["job", job.job_id], job);
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["data-sources"] });
    },
  });
}

export function useQueueJob() {
  return useJobMutation((sourceKey: string) =>
    apiFetch<Job>("/ingestion-jobs", { method: "POST", body: JSON.stringify({ source_key: sourceKey }) }),
  );
}

export function useRetryJob() {
  return useJobMutation((jobId: string) => apiFetch<Job>(`/ingestion-jobs/${jobId}/retry`, { method: "POST" }));
}

export function useCancelJob() {
  return useJobMutation((jobId: string) => apiFetch<Job>(`/ingestion-jobs/${jobId}/cancel`, { method: "POST" }));
}

export function useQuality(range: { start_date?: string; end_date?: string }) {
  return useQuery({
    queryKey: ["quality", range],
    queryFn: () => apiFetch<QualityResponse>(`/datasets/${DATASET_ID}/quality${toQueryString(range)}`),
    placeholderData: keepPreviousData,
    retry: noRetryOnAuth,
  });
}

export function useSchemaReport() {
  return useQuery({
    queryKey: ["schema", DATASET_ID],
    queryFn: () => apiFetch<SchemaReport>(`/datasets/${DATASET_ID}/schema`),
    staleTime: 60_000,
    retry: noRetryOnAuth,
  });
}
