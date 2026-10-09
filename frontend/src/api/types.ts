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
  periods: CoveragePeriod[];
}

export interface ResultMeta {
  filters: Record<string, unknown>;
  coverage: Coverage;
  metric_definitions: MetricDefinition[];
  query_ms: number | null;
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

export interface DateFilters {
  start_date?: string;
  end_date?: string;
}
