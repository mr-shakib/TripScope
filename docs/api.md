# API reference

Base path `/api/v1`. All endpoints except `/health`, `/ready` and `POST /auth/login` need a session cookie.
Errors share one shape: `{"error": {"code", "message", "request_id", "details?"}}`. Unknown query parameters return
422. Interactive OpenAPI docs: `/api/docs` (disabled when `APP_ENV=production`).

## Authentication
| Method | Path | Roles | Notes |
|---|---|---|---|
| POST | `/auth/login` | — | `{email, password}` → HttpOnly session cookie; throttled; audited |
| POST | `/auth/logout` | any | clears the cookie; audited |
| GET | `/auth/me` | any | current user and role |

## Datasets
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/datasets` | any | datasets with published coverage and run lineage |
| GET | `/datasets/{id}` | any | one dataset's coverage |
| GET | `/datasets/{id}/schema` | any | schema versions, per-field availability by period, drift between consecutive files |
| GET | `/datasets/{id}/quality?start_date&end_date` | any | per-period run metrics, totals, daily flag counts |

## Analytics
Shared filters on every endpoint: `dataset_id`, `start_date`, `end_date`, `pickup_zone[]`, `dropoff_zone[]`,
`payment_type[]`, `vendor_id[]`, `hour[]`, `weekday[]` (ISO 1–7), `min_distance`, `max_distance`.
Responses carry `meta` (applied filters, coverage, metric definitions, `source_table`, `query_ms`).

| Method | Path | Extra params | Notes |
|---|---|---|---|
| GET | `/analytics/overview` | — | 5 KPIs with excluded-row counts |
| GET | `/analytics/trips-over-time` | `metric`, `granularity` (`hour`≤62 days, `day`, `month`) | time series |
| GET | `/analytics/trips-by-hour` | `metric` | 24 groups |
| GET | `/analytics/trips-by-weekday` | `metric` | 7 groups, labelled Mon–Sun |
| GET | `/analytics/top-pickup-zones` | `metric`, `limit` (1–50) | zone names from the TLC lookup; 264/265 labelled unmapped |
| GET | `/analytics/zones` | — | TLC zone lookup |

`metric` ∈ `total_trips`, `total_recorded_amount`, `avg_total_amount`, `avg_trip_distance`,
`avg_trip_duration_minutes` (definitions: [metric-definitions.md](metric-definitions.md)).

## Ingestion and processing
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/data-sources` | admin, analyst | manifest entries + registration, publication, latest job |
| GET | `/ingestion-jobs?status&source_key&limit` | admin, analyst | newest first, with runs |
| GET | `/ingestion-jobs/{id}` | admin, analyst | job with runs, stage timings, reconciliation |
| POST | `/ingestion-jobs` | admin | `{source_key}` → queued job (409 if one is already queued/running) |
| POST | `/ingestion-jobs/{id}/retry` | admin | failed/cancelled only → new queued attempt |
| POST | `/ingestion-jobs/{id}/cancel` | admin | queued → cancelled now; running → cancel request honoured by the worker |
| GET | `/processing-runs/{id}/logs` | admin, analyst | the run's structured log records (redacted) |

## System
| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/ready` | PostgreSQL, ClickHouse and object-storage checks (503 if degraded) |
