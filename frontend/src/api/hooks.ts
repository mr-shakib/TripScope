"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { toApiBody, toApiParams } from "@/lib/filters";

import { ApiError, apiFetch, toQueryString } from "./client";
import type {
  BreakdownResponse,
  Coverage,
  DistributionResponse,
  ExplorerField,
  ExplorerRowsResponse,
  FlowsResponse,
  MatrixResponse,
  MetricDefinition,
  QualityScope,
  ReportDetail,
  ReportDocument,
  ReportFormat,
  ReportRun,
  ReportSummary,
  ReportTemplate,
  ReportVisibility,
  ZoneFeatureCollection,
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

export function useOverview(filters: DashboardFilters, compare = false) {
  return useQuery(analyticsQuery<OverviewResponse>("overview", filters, { compare: compare ? "previous" : undefined }));
}

export function useTimeSeries(filters: DashboardFilters, metric: MetricId, granularity: Granularity) {
  return useQuery(analyticsQuery<TimeSeriesResponse>("trips-over-time", filters, { metric, granularity }));
}

export type BreakdownPath =
  | "trips-by-hour"
  | "trips-by-weekday"
  | "top-pickup-zones"
  | "top-dropoff-zones"
  | "payment-types"
  | "vendors";

export function useBreakdown(path: BreakdownPath, filters: DashboardFilters, metric: MetricId, limit?: number) {
  return useQuery(analyticsQuery<BreakdownResponse>(path, filters, { metric, limit }));
}

export function useZoneTotals(filters: DashboardFilters, side: "pickup" | "dropoff", metric: MetricId) {
  return useQuery(analyticsQuery<BreakdownResponse>("zone-totals", filters, { side, metric, limit: 300 }));
}

export function useMatrix(filters: DashboardFilters, metric: MetricId) {
  return useQuery(analyticsQuery<MatrixResponse>("hour-weekday", filters, { metric }));
}

export function useFlows(filters: DashboardFilters, limit = 12) {
  return useQuery(analyticsQuery<FlowsResponse>("top-flows", filters, { limit }));
}

export function useDistribution(filters: DashboardFilters, metric: "trip_distance" | "total_amount") {
  return useQuery(analyticsQuery<DistributionResponse>("distribution", filters, { metric }));
}

export function useZoneGeometry() {
  return useQuery({
    queryKey: ["zone-geometry"],
    queryFn: () => apiFetch<ZoneFeatureCollection>("/analytics/zones/geometry"),
    staleTime: Infinity,
    retry: noRetryOnAuth,
  });
}

export function useMetricCatalogue() {
  return useQuery({
    queryKey: ["metric-catalogue"],
    queryFn: () => apiFetch<{ metrics: MetricDefinition[] }>("/analytics/metrics").then((r) => r.metrics),
    staleTime: Infinity,
  });
}

export interface RowScope {
  sort: string;
  order: "asc" | "desc";
  quality: QualityScope;
  flag?: string;
}

export function useExplorerRows(filters: DashboardFilters, scope: RowScope, page: number, pageSize: number) {
  return useQuery({
    queryKey: ["explorer", filters, scope, page, pageSize],
    queryFn: () =>
      apiFetch<ExplorerRowsResponse>(
        `/explorer/rows${toQueryString({ dataset_id: DATASET_ID, ...toApiParams(filters), ...scope, page, page_size: pageSize })}`,
      ),
    placeholderData: keepPreviousData,
    retry: noRetryOnAuth,
  });
}

export function useExplorerFields() {
  return useQuery({
    queryKey: ["explorer-fields"],
    queryFn: () =>
      apiFetch<{ fields: ExplorerField[]; sortable: string[]; periods: string[]; max_export_rows: number }>("/explorer/fields"),
    staleTime: 60_000,
  });
}

export interface ExtractResult {
  blob: Blob;
  fileName: string;
  totalRows: number;
  exportedRows: number;
  truncated: boolean;
}

/** POST /exports and return the file as a Blob (the browser then saves it). */
export async function downloadExtract(
  filters: DashboardFilters,
  scope: RowScope,
  columns?: string[],
  format: "csv" | "xlsx" = "csv",
): Promise<ExtractResult> {
  const response = await fetch("/api/v1/exports", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      format,
      filters: { dataset_id: DATASET_ID, ...toApiBody(filters) },
      scope: { sort: scope.sort, order: scope.order, quality: scope.quality, ...(scope.flag ? { flag: scope.flag } : {}) },
      ...(columns ? { columns } : {}),
    }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: { code?: string; message?: string; request_id?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "http_error", body?.error?.message ?? "Export failed", body?.error?.request_id ?? null);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  return {
    blob: await response.blob(),
    fileName: /filename="([^"]+)"/.exec(disposition)?.[1] ?? `tripscope-trips.${format}`,
    totalRows: Number(response.headers.get("x-total-rows") ?? 0),
    exportedRows: Number(response.headers.get("x-exported-rows") ?? 0),
    truncated: response.headers.get("x-truncated") === "true",
  };
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

// ---- Phase 4: report center ----------------------------------------------------------------------------

export function useReportTemplates() {
  return useQuery({
    queryKey: ["report-templates"],
    queryFn: () => apiFetch<{ templates: ReportTemplate[]; formats: ReportFormat[] }>("/reports/templates"),
    staleTime: Infinity,
    retry: noRetryOnAuth,
  });
}

const runActive = (run: ReportRun | undefined) => run !== undefined && ACTIVE.includes(run.status);

export function useReports(scope: "all" | "mine" | "shared") {
  return useQuery({
    queryKey: ["reports", scope],
    queryFn: () => apiFetch<{ reports: ReportSummary[] }>(`/reports${toQueryString({ scope })}`).then((r) => r.reports),
    // Poll while any listed file is being generated so status chips update live.
    refetchInterval: (query) =>
      query.state.data?.some((r) => Object.values(r.latest_runs).some(runActive)) ? 2000 : 30_000,
    placeholderData: keepPreviousData,
    retry: noRetryOnAuth,
  });
}

export function useReport(reportId: string) {
  return useQuery({
    queryKey: ["report", reportId],
    queryFn: () => apiFetch<ReportDetail>(`/reports/${encodeURIComponent(reportId)}`),
    refetchInterval: (query) => (query.state.data?.runs.some(runActive) ? 1500 : false),
    retry: noRetryOnAuth,
  });
}

/** The document a generated file would contain, rebuilt whenever the saved report changes. */
export function useReportPreview(reportId: string, version: string | undefined) {
  return useQuery({
    queryKey: ["report-preview", reportId, version],
    queryFn: () => apiFetch<ReportDocument>(`/reports/${encodeURIComponent(reportId)}/preview`),
    enabled: Boolean(version),
    placeholderData: keepPreviousData,
    retry: noRetryOnAuth,
  });
}

export interface ReportDraft {
  template?: string;
  title?: string;
  filters?: DashboardFilters;
  sections?: string[];
  visibility?: ReportVisibility;
}

function reportBody(draft: ReportDraft): Record<string, unknown> {
  return {
    ...(draft.template ? { template: draft.template } : {}),
    ...(draft.title !== undefined ? { title: draft.title } : {}),
    ...(draft.filters ? { filters: { dataset_id: DATASET_ID, ...toApiBody(draft.filters) } } : {}),
    ...(draft.sections ? { sections: draft.sections } : {}),
    ...(draft.visibility ? { visibility: draft.visibility } : {}),
  };
}

export function useCreateReport() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (draft: ReportDraft) =>
      apiFetch<ReportDetail>("/reports", { method: "POST", body: JSON.stringify(reportBody(draft)) }),
    onSuccess: (report) => {
      client.setQueryData(["report", report.report_id], report);
      void client.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

export function useUpdateReport(reportId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (draft: ReportDraft) =>
      apiFetch<ReportDetail>(`/reports/${encodeURIComponent(reportId)}`, {
        method: "PATCH",
        body: JSON.stringify(reportBody(draft)),
      }),
    onSuccess: (report) => {
      client.setQueryData(["report", reportId], report);
      void client.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

export function useDeleteReport() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (reportId: string) => apiFetch<null>(`/reports/${encodeURIComponent(reportId)}`, { method: "DELETE" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["reports"] }),
  });
}

export function useGenerateReport(reportId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (format: ReportFormat) =>
      apiFetch<ReportRun>(`/reports/${encodeURIComponent(reportId)}/generate`, {
        method: "POST",
        body: JSON.stringify({ format }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["report", reportId] });
      void client.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

/** Fetch a generated report file and hand it to the browser to save. */
export async function downloadReport(reportId: string, runId: string): Promise<string> {
  const response = await fetch(
    `/api/v1/reports/${encodeURIComponent(reportId)}/download${toQueryString({ run_id: runId })}`,
    { credentials: "same-origin" },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: { code?: string; message?: string; request_id?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "http_error", body?.error?.message ?? "Download failed", body?.error?.request_id ?? null);
  }
  const fileName = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? "tripscope-report";
  saveBlob(await response.blob(), fileName);
  return fileName;
}

export function saveBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}
