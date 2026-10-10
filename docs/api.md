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
| GET | `/analytics/metrics` | — | metric catalogue: label, unit, description, rows included, caveats |
| GET | `/analytics/overview` | `compare` (`none`, `previous`) | 6 KPIs with excluded-row counts and the date range found; `compare=previous` adds the same KPIs for the equally long window just before the selection; without a start and end date, or when that window starts before published data, `comparison.available` is false with a `reason` |
| GET | `/analytics/trips-over-time` | `metric`, `granularity` (`hour`≤62 days, `day`, `month`) | time series |
| GET | `/analytics/trips-by-hour` | `metric` | 24 groups |
| GET | `/analytics/trips-by-weekday` | `metric` | 7 groups, labelled Mon–Sun |
| GET | `/analytics/hour-weekday` | `metric` | 168 cells (weekday × hour) |
| GET | `/analytics/payment-types` | `metric` | groups labelled from the TLC data dictionary |
| GET | `/analytics/vendors` | `metric` | groups labelled from the TLC data dictionary |
| GET | `/analytics/top-pickup-zones` | `metric`, `limit` (1–50) | zone names from the TLC lookup; 264/265 labelled unmapped |
| GET | `/analytics/top-dropoff-zones` | `metric`, `limit` (1–50) | as above, by drop-off zone |
| GET | `/analytics/zone-totals` | `side` (`pickup`, `dropoff`), `metric`, `limit` (1–300) | every zone, for the map |
| GET | `/analytics/top-flows` | `metric`, `limit` (1–50) | busiest pickup → drop-off pairs, with labels and boroughs |
| GET | `/analytics/distribution` | `metric` (`trip_distance`, `total_amount`) | histogram of valid values (1-mile buckets to 50+, $5 buckets to $200+), median and p90 buckets, excluded and above-cap counts |
| GET | `/analytics/zones` | — | TLC zone lookup |
| GET | `/analytics/zones/geometry` | — | simplified WGS84 zone boundaries (`application/geo+json`, 263 features); 404 until built |

`metric` ∈ `total_trips`, `total_recorded_amount`, `avg_total_amount`, `avg_trip_distance`,
`avg_trip_duration_minutes` (definitions: [metric-definitions.md](metric-definitions.md)). `meta.source_table`
names the table that answered: the cheapest one whose declared filters, dimensions and metrics cover the request.

## Data explorer
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/explorer/fields` | any | field catalogue: type, description, source columns, availability by period, units and codes; sortable columns; export limit |
| GET | `/explorer/rows` | any | shared filters + `sort`, `order`, `quality` (`all`, `clean`, `flagged`), `flag`, `page`, `page_size` (25/50/100). Returns the page, the total matching rows and the columns. The preview stops at the first 10,000 rows (422 beyond) |
| POST | `/exports` | any | `{format: "csv" \| "xlsx", filters, scope: {sort, order, quality, flag?}, columns?, max_rows?}` → streamed CSV, or an XLSX workbook (Trips sheet with typed cells and a frozen header, About sheet with filters, row counts and attribution); at most `MAX_EXPORT_ROWS` (default 100,000). Headers `X-Total-Rows`, `X-Exported-Rows`, `X-Truncated`. Audited as `export.csv` / `export.xlsx` |

## Reports
Templates 1–5 (FR-09). A report stores a template, title, filters (the shared analytics filters), the enabled sections
and its visibility (`private`: the owner and administrators; `shared`: every signed-in user). Generating a file queues a
run that snapshots the definition; the report worker renders it from the same document as the preview.

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/reports/templates` | any | templates 1–6 with their KPIs and sections; formats `pdf`, `xlsx`, `csv` |
| GET | `/reports?scope&limit` | any | reports the caller can see (`all`, `mine`, `shared`), newest first, with the latest run per format |
| POST | `/reports` | admin, analyst | `{template, title?, filters?, sections?, visibility?}` → 201; title defaults to template and period; audited |
| GET | `/reports/{id}` | can see | the report, its permissions and run history (status, requester, size, pages, sha256, dataset version) |
| PATCH | `/reports/{id}` | owner, admin | `{title?, filters?, sections?, visibility?}`; audited with the changed fields |
| DELETE | `/reports/{id}` | owner, admin | removes the report, its runs and stored files (409 while a file is being generated); audited |
| GET | `/reports/{id}/preview` | can see | the report document built now: period, filters, dataset version, summary, KPIs with comparison, sections (charts and tables with raw values and display text), findings with evidence, methodology, limitations. 422 when no trips match |
| POST | `/reports/{id}/generate` | admin, analyst who can see it | `{format}` → 202 queued run; 409 if that format is already queued or running; audited |
| GET | `/reports/{id}/download?run_id&format` | can see | the file of a completed run (or the newest completed one); 409 if the run is not finished; integrity-checked against its sha256 (`X-Report-SHA256`); audited as `report.downloaded` |

A report the caller may not see answers 404, so private reports are not disclosed. Report CSV is one tidy table with
the columns `section, block, row, field, label, value, unit`.

## AI analyst
The analyst answers only through allowlisted tools over the analytics service (no SQL, no trip rows). Admins and
analysts may ask; viewers only when `AI_ALLOW_VIEWERS=true`. Conversations are private to their owner and deleted
after `AI_RETENTION_DAYS`. With `LLM_PROVIDER=disabled`, demo questions still work (fixed answer rules, no model).

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/ai/status` | any | provider, model, local or external, whether the caller may chat, demo questions, suggestions |
| POST | `/ai/chat` | admin, analyst (viewer if enabled) | `{message \| demo_id, conversation_id?, filters?}` → 202 `{conversation_id, message_id}`; answered in the background. `filters` are the dashboard filters offered as context. 503 when no model is configured (demo questions still work); 409 while the previous question is being answered |
| GET | `/ai/conversations?limit` | as chat | the caller's conversations |
| GET | `/ai/conversations/{id}` | owner | messages with status (`running`, `answered`, `clarification`, `failed`), answer, caveats, follow-ups, verification of every figure (`claims` with offsets), chart, model calls and latency, and each tool run (tool, validated arguments, status, duration, bounded result, period, filters, metric definitions, source table). Poll while the last message is `running` |
| DELETE | `/ai/conversations/{id}` | owner | 204 |
| POST | `/ai/report-outline` | admin, analyst | `{filters?, focus?}` → `{title, sections, rationale, library, model}` from the template-6 section library (validated, one retry) |
| POST | `/ai/report-draft` | admin, analyst | `{title?, filters?, sections, focus?, visibility?}` → 202 `{report_id}`; the report exists at once and its narrative is drafted in the background (`narrative.status` on the report: `drafting`, `ready`, `stale`, `failed`) |
| POST | `/ai/reports/{id}/redraft` | owner, admin | `{focus?}` → 202; drafts the narrative again for the saved filters and sections |

Tools the model may call (spec §10.1): `get_overview_metrics`, `get_time_series`, `compare_periods`,
`get_top_zones`, `get_breakdown`, `get_distribution`, `get_data_quality_summary`, `run_anomaly_analysis`,
`create_chart_spec`, `create_report_draft` (saves a private report; never exports or sends), `find_zones`. Their
inputs are strict schemas; invalid calls come back to the model as an error message.

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
