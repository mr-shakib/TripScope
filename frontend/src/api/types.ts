// Response shapes of the TripScope API (backend/src/tripscope/analytics/service.py and api/routers).

export type Role = "admin" | "analyst" | "viewer";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
}

export type MetricId =
  | "total_trips"
  | "total_recorded_amount"
  | "avg_total_amount"
  | "avg_trip_distance"
  | "avg_trip_duration_minutes";

export type Unit = "trips" | "usd" | "miles" | "minutes";

export interface MetricDefinition {
  id: MetricId;
  label: string;
  unit: Unit;
  description: string;
  rows_included: string;
  caveats: string[];
}

export interface CoveragePeriod {
  period: string;
  row_count: number;
  run_id: string;
  published_at: string;
}

export interface Coverage {
  dataset_id: string;
  dataset_name: string;
  start_date: string | null;
  end_date: string | null;
  source_attribution: string;
  total_rows?: number;
  periods: CoveragePeriod[];
}

export interface ResultMeta {
  filters: Record<string, unknown>;
  coverage: Coverage;
  metric_definitions: MetricDefinition[];
  query_ms: number | null;
  source_table?: string | null;
  generated_at: string;
  timezone_note: string;
}

export interface Kpi {
  value: number | null;
  unit: Unit;
  excluded_rows: number;
}

export type DataState = "ok" | "empty" | "no_published_data";

export interface OverviewResponse {
  kpis: Record<MetricId, Kpi> | null;
  data_state: DataState;
  result_range?: { first_date: string; last_date: string; days_with_data: number };
  meta: ResultMeta;
}

export type Granularity = "hour" | "day" | "month";

export interface SeriesPoint {
  bucket: string;
  value: number | null;
  trips: number;
}

export interface TimeSeriesResponse {
  points: SeriesPoint[];
  data_state: DataState;
  meta: ResultMeta & { granularity?: Granularity; metric?: MetricId };
}

export interface DashboardFilters {
  start_date?: string;
  end_date?: string;
  pickup_zone: number[];
  payment_type: number[];
  hour: number[];
  weekday: number[];
}

// ---- Phase 2 -------------------------------------------------------------------------------------------

export interface BreakdownGroup {
  key: number | null;
  label: string;
  value: number | null;
  trips: number;
  borough?: string | null;
  mapped?: boolean;
}

export interface BreakdownResponse {
  groups: BreakdownGroup[];
  data_state: DataState;
  meta: ResultMeta & { dimension?: string; metric?: MetricId; source_table?: string | null };
}

export interface Zone {
  id: number;
  borough: string;
  zone: string;
  service_zone: string;
  is_geographic: boolean;
}

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface RunSummary {
  run_id: string;
  status: JobStatus;
  current_stage: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  stage_timings: Record<string, number>;
  schema_version: string | null;
  unavailable_fields: string[];
  error_summary: string | null;
  input_rows: number | null;
  accepted_rows: number | null;
  quarantined_rows: number | null;
  clickhouse_rows: number | null;
}

export interface LogEntry {
  at: string;
  level: "debug" | "info" | "warning" | "error" | "critical";
  logger: string;
  message: string;
  fields: Record<string, string>;
}

export interface Job {
  job_id: string;
  source_key: string;
  period: string;
  status: JobStatus;
  attempt: number;
  trigger: string;
  requested_by: string | null;
  retry_of_job_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  worker_id: string | null;
  cancel_requested: boolean;
  error_summary: string | null;
  runs: RunSummary[];
}

export interface DataSourceItem {
  source_key: string;
  dataset_id: string;
  period: string;
  format: "parquet" | "csv";
  uri: string;
  checksum_pinned: boolean;
  file_size_bytes: number | null;
  sha256: string | null;
  schema_version: string | null;
  retrieved_at: string | null;
  published: {
    row_count: number;
    run_id: string;
    published_at: string;
    min_pickup_date: string;
    max_pickup_date: string;
  } | null;
  latest_job: {
    job_id: string;
    status: JobStatus;
    attempt: number;
    created_at: string;
    finished_at: string | null;
    error_summary: string | null;
  } | null;
}

export interface QualityPeriod {
  period: string;
  run_id: string;
  input_rows: number;
  accepted_rows: number;
  quarantined_rows: number;
  duplicate_rows: number;
  quarantine_reasons: Record<string, number>;
  flags: Record<string, number>;
  cast_failures: Record<string, number>;
  missingness: Record<string, { null_count: number; null_pct: number | null }>;
  schema_version: string;
  unavailable_fields: string[];
  quality_rules: Record<string, number>;
  duration_seconds: number | null;
  input_bytes: number;
  output_bytes: number | null;
  completed_at: string | null;
}

export interface QualityResponse {
  periods: QualityPeriod[];
  totals: {
    input_rows: number;
    accepted_rows: number;
    quarantined_rows: number;
    duplicate_rows: number;
    quarantine_reasons: Record<string, number>;
    flags: Record<string, number>;
  };
  daily_flags: { date: string; flag: string; flagged_trips: number; trips: number }[];
  meta: ResultMeta & { note: string };
}

export interface SchemaField {
  name: string;
  kind: string;
  required: boolean;
  description: string;
  source_column_by_period: Record<string, string | null>;
  available_in: number;
  periods: number;
}

export interface SchemaReport {
  dataset_id: string;
  dataset_name: string;
  fields: SchemaField[];
  schema_versions: { schema_version: string; file_format: string; column_count: number; periods: string[] }[];
  drift: {
    from_period: string;
    to_period: string;
    added: string[];
    removed: string[];
    renamed_case: { from: string; to: string }[];
    type_changed: { column: string; from: string; to: string }[];
    changed: boolean;
  }[];
  sources: { period: string; source_key: string; file_format: string; schema_version: string | null; column_count: number; num_rows: number | null }[];
}
