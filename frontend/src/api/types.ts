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
  | "avg_daily_trips"
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
  comparison?: Comparison;
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
  dropoff_zone: number[];
  payment_type: number[];
  vendor_id: number[];
  hour: number[];
  weekday: number[];
  min_distance?: number;
  max_distance?: number;
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

// ---- Phase 3 -------------------------------------------------------------------------------------------

export interface Comparison {
  available: boolean;
  start_date?: string;
  end_date?: string;
  days?: number;
  kpis?: Record<MetricId, Kpi> | null;
  reason?: string | null;
}

export interface MatrixResponse {
  cells: { weekday: number; hour: number; value: number | null; trips: number }[];
  data_state: DataState;
  meta: ResultMeta;
}

export interface Flow {
  pickup_zone: number;
  dropoff_zone: number;
  value: number | null;
  trips: number;
  pickup_label: string;
  pickup_borough: string | null;
  dropoff_label: string;
  dropoff_borough: string | null;
  same_zone: boolean;
}

export interface FlowsResponse {
  flows: Flow[];
  data_state: DataState;
  meta: ResultMeta;
}

export interface DistributionResponse {
  buckets: { start: number; end: number | null; trips: number; open_ended: boolean }[];
  summary: {
    counted_trips: number;
    excluded_trips: number;
    bucket_width: number;
    cap: number;
    median_bucket: { start: number; end: number | null } | null;
    p90_bucket: { start: number; end: number | null } | null;
    above_cap_trips: number;
  };
  data_state: DataState;
  meta: ResultMeta & { metric: "trip_distance" | "total_amount" };
}

export interface ZoneFeatureCollection {
  type: "FeatureCollection";
  features: { type: "Feature"; id: number; properties: { location_id: number; zone: string; borough: string }; geometry: unknown }[];
}

export interface ExplorerField {
  name: string;
  origin: "source" | "derived";
  type: string;
  description: string;
  source_columns: string[];
  available_in: number | null;
  periods: number | null;
  unit: string | null;
  codes: string | null;
}

export type ExplorerRow = Record<string, string | number | boolean | null | string[]>;

export interface ExplorerRowsResponse {
  rows: ExplorerRow[];
  columns: string[];
  total: number;
  page: number;
  page_size: number;
  max_preview_rows: number;
  data_state: DataState;
  meta: ResultMeta;
}

export type QualityScope = "all" | "clean" | "flagged";

// ---- Phase 4: report center ----------------------------------------------------------------------------

export type ReportFormat = "pdf" | "xlsx" | "csv";
export type ReportVisibility = "private" | "shared";

export interface ReportTemplate {
  id: string;
  number: number;
  title: string;
  description: string;
  kpis: string[];
  sections: { id: string; title: string; description: string }[];
}

export interface Person {
  id: string;
  name: string;
}

export interface ReportRun {
  run_id: string;
  format: ReportFormat;
  status: JobStatus;
  requested_by: Person | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  error_summary: string | null;
  file_name: string | null;
  size_bytes: number | null;
  sha256: string | null;
  page_count: number | null;
  dataset_version_id: string | null;
  title: string | null;
}

export interface ReportSummary {
  report_id: string;
  template: string;
  template_title: string;
  title: string;
  dataset_id: string;
  filters: Record<string, unknown>;
  sections: string[];
  visibility: ReportVisibility;
  created_by: Person | null;
  created_at: string;
  updated_at: string;
  permissions: { edit: boolean; generate: boolean };
  latest_runs: Partial<Record<ReportFormat, ReportRun>>;
}

export interface ReportDetail extends ReportSummary {
  runs: ReportRun[];
}

/** Units in a report document: the metric units plus counts, shares (fractions) and plain text. */
export type ReportUnit = Unit | "rows" | "count" | "share" | "seconds" | "bytes" | "date" | "text";

export interface ReportKpi {
  id: string;
  label: string;
  unit: ReportUnit;
  value: number | null;
  display: string;
  previous: number | null;
  previous_display: string | null;
  change: number | null;
  change_display: string | null;
  excluded_rows: number;
  note: string | null;
}

export interface ReportTableBlock {
  type: "table";
  id: string;
  title: string;
  columns: { key: string; label: string; unit: ReportUnit }[];
  rows: (string | number | boolean | null)[][];
  display: string[][];
  note: string | null;
}

export interface ReportChartBlock {
  type: "chart";
  id: string;
  title: string;
  kind: "line" | "column" | "bar" | "heatmap" | "histogram";
  unit: ReportUnit;
  categories: string[];
  series: { name: string; values: (number | null)[] }[];
  rows: string[];
  matrix: (number | null)[][];
  markers: { index: number; label: string }[];
  note: string | null;
}

export interface ReportSection {
  id: string;
  title: string;
  description: string;
  blocks: (ReportTableBlock | ReportChartBlock)[];
}

export interface ReportFinding {
  id: string;
  section: string;
  statement: string;
  kind: "descriptive" | "predictive" | "hypothesis";
  evidence: { source: string; source_table: string | null; values: Record<string, string | number | boolean | null> };
  caveat: string | null;
}

export interface ReportDocument {
  template: string;
  template_title: string;
  title: string;
  period: { start: string; end: string; days: number; label: string; data_first: string | null; data_last: string | null };
  filters: { label: string; value: string }[];
  applied_filters: Record<string, unknown>;
  dataset: {
    id: string;
    name: string;
    attribution: string;
    version_id: string;
    periods: { period: string; run_id: string; row_count: number; published_at: string }[];
  };
  generated_at: string;
  prepared_by: string;
  summary: string[];
  kpis: ReportKpi[];
  comparison: { available: boolean; label: string | null; start: string | null; end: string | null; reason: string | null };
  sections: ReportSection[];
  findings: ReportFinding[];
  methodology: string[];
  limitations: string[];
}
