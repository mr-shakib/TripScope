# TripScope — Full Project Specification
## AI-Powered Big Data Transportation Intelligence Platform

**Document type:** Product requirements + technical specification + implementation guide  
**Version:** 1.0  
**Project status:** Ready for implementation  
**Primary dataset:** NYC Taxi & Limousine Commission (TLC) Trip Record Data  
**Primary users:** Project team, instructors/evaluators, data analysts, operations managers

---

## 1. Project Summary

Build **TripScope**, a web-based Big Data analytics platform for exploring NYC taxi trip data. Users can ingest public trip records, validate and transform them with Apache Spark, store curated data in Parquet and ClickHouse, explore interactive dashboards, export reports, and use an AI data analyst to ask natural-language questions and generate evidence-backed reports.

The project must demonstrate real data engineering and analytics—not merely a dashboard over a small CSV or an LLM that invents insights.

### 1.1 Core outcomes

1. Ingest one or more TLC monthly trip-record files.
2. Validate, clean, transform, and aggregate data with PySpark.
3. Store raw and curated data as Parquet in a data lake.
4. Make dashboard queries fast using ClickHouse.
5. Provide interactive charts, filters, drill-downs, and data tables.
6. Export filtered data to CSV and Excel, and polished analytical reports to PDF.
7. Provide an AI agent that answers questions using approved analytics tools and verified results.
8. Generate report drafts with methodology, key metrics, trends, caveats, charts, and recommendations.
9. Track ingestion and processing jobs, data quality, and performance.
10. Provide reproducible deployment using Docker Compose.

### 1.2 Non-goals for the first release

- Production-grade multi-tenant SaaS billing.
- Unrestricted SQL generated and executed by the LLM.
- Guaranteed prediction of traffic, demand, or fare outcomes.
- Real-world live taxi dispatch or vehicle tracking.
- A claim that every unusual fare is fraud.
- Mandatory cloud deployment.
- Kafka streaming in the initial MVP. Streaming is an optional extension after batch processing works.

---

## 2. Dataset and Data Governance

### 2.1 Primary data source

Use the official NYC Taxi & Limousine Commission (TLC) Trip Record Data portal:

https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page

TLC publishes monthly trip-record files, commonly in Parquet format. Dataset schemas and available fields can change by vehicle type and year. The implementation must inspect and validate the actual schema of every downloaded file rather than assuming every month has identical columns.

Official data dictionary / related TLC information should be checked from the TLC portal before finalizing field mappings.

### 2.2 Initial dataset scope

Start with **Yellow Taxi** records for a selected contiguous period, such as 3–6 months. Choose files that are currently available from the official portal. Do not hardcode assumed download URLs; implement a source manifest that records the chosen URLs or local file paths and allows configuration.

After the pipeline works, scale the benchmark to 12 or more months, subject to available disk space and machine capacity.

### 2.3 Data licensing and attribution

- Include the TLC source URL and retrieval date in the dataset metadata.
- Include source attribution in the README and report methodology.
- Do not redistribute source data without checking the applicable terms.
- Preserve the source file name and ingestion timestamp for lineage.

### 2.4 Important limitations

TLC records are operational trip records, not a complete representation of all city transport. They may contain missing, anomalous, revised, or differently defined fields across time. Do not treat recorded pickup/drop-off locations as precise GPS tracks. Do not claim an anomaly proves fraud or misconduct.

---

## 3. User Roles and Main User Journeys

### 3.1 Roles

**Administrator**
- Manage users and roles.
- Configure data sources and system settings.
- View all ingestion jobs and audit events.
- Retry or cancel supported jobs.

**Analyst**
- Browse datasets and dashboards.
- Apply filters and save views.
- Ask questions of the AI analyst.
- Generate and export reports.
- Inspect data quality and processing summaries.

**Viewer**
- View dashboards and reports they have permission to access.
- Download permitted exports.
- Ask AI questions only against authorized datasets, if enabled by the administrator.

For a single-user academic demo, role-based access can be implemented simply but should still exist in the architecture.

### 3.2 Main user journey

1. User signs in.
2. User opens Data Sources and registers a TLC data file or a source manifest.
3. User starts ingestion.
4. The application validates file format, schema, size, and record counts.
5. A Spark job cleans and transforms the data.
6. Raw files and curated Parquet outputs are stored in MinIO or a local S3-compatible data lake.
7. Aggregates and query-ready records are loaded into ClickHouse.
8. The user opens the dashboard and filters by date, zone, hour, payment type, and other supported dimensions.
9. The user asks the AI analyst a question.
10. The agent selects an approved tool, executes a bounded query, and explains the actual returned values.
11. The user previews a report, adjusts the date range or sections, then exports PDF or Excel.
12. Job history, report metadata, and audit events remain available.

---

## 4. Functional Requirements

### FR-01 — Authentication and authorization
- Sign in/sign out.
- Passwords must be hashed using a standard password-hashing implementation.
- Enforce roles and dataset permissions on the backend, not only in the frontend.
- Do not expose secrets or service credentials to the browser.
- Record important administrative actions in an audit log.

### FR-02 — Data source management
- Register a local file, an uploaded file, or a configured source manifest.
- Support Parquet as the preferred input; CSV may be supported for demonstration.
- Show source name, vehicle type, period, file size, schema, source URI, and ingestion status.
- Validate supported file type and maximum configured upload size.
- Never execute uploaded files or treat file content as instructions.

### FR-03 — Ingestion jobs
- Create, queue, start, complete, fail, retry, and cancel jobs where supported.
- Display status, start/end times, elapsed time, records read/written, error summary, and logs.
- Make job operations idempotent where possible so retrying does not silently duplicate curated data.
- Store detailed logs server-side; avoid exposing secrets in logs.

### FR-04 — Data validation and quality
At minimum, calculate:
- Total records read.
- Total records accepted and rejected.
- Missing values by important field.
- Duplicate count using a documented key or deduplication strategy.
- Invalid or impossible timestamp intervals.
- Non-positive or implausible trip distances and durations.
- Negative or implausible monetary amounts.
- Unknown or unmapped location IDs.
- Schema differences across source files.

Do not silently discard suspicious records. Preserve raw data, document cleaning rules, and write rejected rows or reason summaries to a quarantine area where practical.

### FR-05 — Data transformation
Create a canonical schema with normalized field names and types. Where a field does not exist in a source version, mark it unavailable rather than fabricating it.

Suggested canonical fields, depending on source availability:
- `vendor_id`
- `pickup_datetime`
- `dropoff_datetime`
- `passenger_count`
- `trip_distance`
- `pickup_location_id`
- `dropoff_location_id`
- `rate_code_id`
- `store_and_fwd_flag`
- `payment_type`
- `fare_amount`
- `extra`
- `mta_tax`
- `tip_amount`
- `tolls_amount`
- `improvement_surcharge`
- `total_amount`
- `congestion_surcharge`
- `airport_fee`
- `cbd_congestion_fee` (only if present in the selected schema)
- `source_file`
- `ingested_at`
- `data_period`

Derived fields:
- `pickup_date`
- `pickup_hour`
- `pickup_day_of_week`
- `trip_duration_minutes`
- `average_speed_mph` (only for valid distance and duration)
- `fare_per_mile` (only where denominator is valid)

Use explicit data types and UTC/local-time handling consistently. TLC timestamps should be interpreted according to source documentation and the project’s chosen NYC local-time convention.

### FR-06 — Storage
- Store immutable raw input files separately from curated output.
- Use Parquet for columnar analytical storage and compression.
- Partition curated data by a sensible time field, such as year/month or pickup date, avoiding excessive tiny partitions.
- Store query-ready analytical tables in ClickHouse.
- Store user accounts, dataset metadata, job metadata, saved views, report metadata, and audit events in PostgreSQL.
- Provide scripts or migrations to initialize all required schemas.

### FR-07 — Interactive dashboard
Required overview metrics:
- Total trips.
- Total recorded fare or total amount, clearly labeling which metric is used.
- Average trip distance.
- Average trip duration for valid records.
- Average recorded total amount per trip.
- Trips per day/hour.
- Top pickup and drop-off zones, where zone mapping data is available.

Required charts:
- Trips over time (line chart).
- Total amount/revenue metric over time (line or bar chart).
- Trips by hour of day.
- Trips by day of week.
- Pickup zones ranked by trip volume.
- Trip-distance distribution.
- Fare/total-amount distribution with outlier handling.
- Payment type distribution when the field is available.
- Pickup-to-drop-off flow or zone comparison where useful.

Filters:
- Date range.
- Vehicle type.
- Pickup zone/location ID.
- Drop-off zone/location ID.
- Payment type.
- Vendor ID.
- Trip-distance range.
- Time-of-day/hour.

Requirements:
- Filter state must be visible and resettable.
- Charts and KPIs must update consistently when filters change.
- Display loading, empty, and error states.
- Include metric definitions and data coverage dates.
- Avoid presenting unavailable or untrustworthy values as zero.
- Provide pagination or server-side limiting for large tables.
- Do not fetch all raw rows into the browser.

### FR-08 — Data explorer
- Browse dataset schema and field descriptions.
- View data-quality summaries.
- Preview a bounded number of rows.
- Filter and sort supported fields.
- Download a permitted filtered extract with a configured row/size limit.
- Show the applied filters and row count.

### FR-09 — Report center
Provide report templates:
1. Executive Overview.
2. Trip Demand and Time Patterns.
3. Fare and Distance Analysis.
4. Pickup/Drop-off Zone Analysis.
5. Data Quality and Processing Report.
6. Custom AI-Assisted Report.

Each report should support:
- Title and reporting period.
- Applied filters and dataset version.
- Executive summary.
- KPI table.
- Charts and tables.
- Key findings with evidence.
- Limitations and methodology.
- Generated-at timestamp and report creator.

Exports:
- **CSV:** tabular rows, appropriate escaping, documented column names.
- **XLSX:** multiple sheets where useful (summary, trends, quality, details), freeze panes, sensible number/date formats, safe handling of spreadsheet-formula injection.
- **PDF:** readable title page or header, sections, tables, charts, page numbers, source attribution, methodology, and limitations.

Report requirements:
- Exports must use the same filter definitions as the on-screen report.
- Use a background task for long-running reports.
- Track `queued`, `running`, `completed`, and `failed` status.
- Keep report metadata and a download link/path.
- Enforce authorization for report downloads.
- Never let the LLM directly fabricate numeric tables; populate them from query results.

### FR-10 — AI Data Analyst
The AI analyst must answer questions grounded in the selected dataset.

Example prompts:
- “How did trip volume change month over month?”
- “Which pickup zones had the most trips in the selected period?”
- “Compare average trip distance by hour of day.”
- “Show the difference in trip volume between weekdays and weekends.”
- “Generate an executive report for the selected date range.”
- “Find unusual fare and trip-distance patterns, then explain the limits of this analysis.”

The agent may:
- Interpret the question.
- Ask a clarifying question when the metric, period, or filters are ambiguous.
- Select from a controlled set of analytics tools.
- Execute parameterized, bounded queries.
- Explain returned metrics and trends.
- Draft report narratives and recommendations.
- Propose a chart configuration using an allowlisted chart schema.
- Cite the filters, period, metric definition, and query/result metadata used.

The agent must not:
- Have unrestricted database credentials.
- Execute arbitrary shell commands.
- run arbitrary Python generated by the LLM.
- Access datasets the user cannot access.
- Treat data content or uploaded text as system instructions.
- Claim correlation proves causation.
- Call a record fraudulent solely because it is statistically unusual.
- Invent values when a query fails or data is missing.
- State that a report was exported or emailed unless the backend confirms success.

### FR-11 — AI report generation
Workflow:
1. User chooses dataset, reporting period, and optional filters.
2. Agent proposes a report outline.
3. Backend executes required approved analytical queries.
4. Agent receives only the relevant bounded results and data-quality context.
5. Agent drafts a narrative tied to the results.
6. Backend validates numeric claims where feasible against result payloads.
7. User previews and edits title/sections.
8. Export service generates the selected file.
9. Application stores report metadata and shows status/download.

Each finding should ideally contain:
- Statement.
- Metric or comparison supporting it.
- Period and filters.
- Result values or reference to result IDs.
- Confidence/caveat where appropriate.
- Whether it is descriptive, predictive, or a hypothesis.

### FR-12 — Optional real-time streaming extension
After batch functionality is stable:
- Use Kafka to generate or ingest simulated trip events.
- Validate and transform events in a consumer.
- Write rolling aggregates to ClickHouse.
- Show event throughput, consumer lag, and recent trip-volume metrics.
- Clearly label synthetic stream data.
- Demonstrate replay/idempotency and duplicate handling.

Do not represent a simulated stream as live official TLC vehicle telemetry.

### FR-13 — Monitoring and observability
- Structured logs.
- Health checks for API, PostgreSQL, ClickHouse, and object storage.
- Job status and failure summaries.
- Data processing metrics: duration, input/output rows, bytes processed.
- Dashboard API response time measurements.
- Benchmark scripts with repeatable input data and documented machine specifications.

---

## 5. Non-Functional Requirements

### Performance
- Use ClickHouse for analytical dashboard queries rather than querying raw files on every request.
- Set server-side query limits and timeouts.
- Add appropriate partitioning, sorting keys, and indexes where supported.
- Use pagination and aggregation for large result sets.
- Establish targets only after measuring on the development machine. Record baseline and optimized results.

Suggested demo targets—not guarantees:
- Cached/optimized dashboard query: aim for under 2 seconds on the chosen benchmark dataset.
- Standard dashboard API: aim for p95 under 3 seconds in a local demo.
- AI response: depends on model/provider and query execution; show progress for longer requests.
- Large report generation should run asynchronously.

### Reliability
- Retry transient failures with limits.
- Make ingestion repeatable and idempotent.
- Keep raw data so transformations can be rerun.
- Show actionable errors without exposing credentials or internal secrets.

### Security
- Validate and sanitize inputs.
- Enforce authorization on every protected endpoint.
- Use parameterized queries and allowlisted dimensions/metrics.
- Use read-only ClickHouse credentials for AI query tools.
- Limit query time, output rows, and scanned date ranges.
- Protect against prompt injection in dataset contents.
- Keep API keys in environment variables or secret management; never commit `.env`.
- Apply upload size limits and safe file handling.
- Audit report downloads and administrative actions.
- Use secure cookie/session settings if cookie-based authentication is used.
- Avoid storing sensitive information not needed for the project.

### Usability and accessibility
- Responsive layout for desktop and tablet.
- Consistent labels and metric definitions.
- Keyboard-accessible controls and readable chart legends.
- Clear empty, loading, and error states.

---

## 6. Recommended Technical Architecture

### 6.1 Stack

**Frontend**
- React + TypeScript (Next.js or Vite)
- Tailwind CSS
- Apache ECharts or Recharts
- TanStack Query or an equivalent API-state library
- A reusable component system

**Backend**
- Python + FastAPI
- Pydantic for request/response validation
- SQLAlchemy/Alembic for PostgreSQL metadata, if desired
- Background task system: Celery + Redis, or a simpler worker for the first iteration
- PySpark for distributed batch processing

**Data**
- MinIO for S3-compatible object storage
- Parquet for raw/curated columnar files
- ClickHouse for analytical queries
- PostgreSQL for application metadata
- Redis only if needed for queueing/caching

**AI**
- Provider-agnostic LLM adapter.
- Prefer tool/function calling with explicit schemas.
- Start with one reliable model/provider; support local models later behind the same adapter.
- Keep deterministic calculations in SQL/Python/Spark, not in the language model.

**Deployment**
- Docker Compose for local development.
- Separate profiles/services for API, frontend, worker, Spark job runner, ClickHouse, PostgreSQL, MinIO, and optional Redis/Kafka.
- Document hardware and disk requirements.

### 6.2 Logical architecture

```text
Browser
  |
  v
React/TypeScript Frontend
  |
  v
FastAPI Application -------------------- LLM Provider / Local Model
  |                                           |
  |                                      Tool selection only
  |                                           |
  +--> PostgreSQL (users, metadata, jobs)     |
  |                                           v
  +--> Approved Analytics Service <--- Validated tool calls
  |          |
  |          +--> ClickHouse (bounded analytics queries)
  |          +--> Data-quality metadata
  |
  +--> Report Worker --> PDF/XLSX/CSV --> Report storage
  |
  +--> Job Controller --> Spark batch jobs
                              |
                              +--> MinIO / Parquet
                              +--> ClickHouse
```

The LLM should not directly connect to databases. All tool calls pass through backend validation and authorization.

---

## 7. Data Pipeline Specification

### 7.1 Pipeline stages

1. **Discover:** identify input files and source metadata.
2. **Ingest:** copy or register immutable source files.
3. **Inspect:** infer schema and compare against supported schema versions.
4. **Validate:** required fields, types, timestamp parse rates, and quality rules.
5. **Normalize:** map available source columns to canonical names and types.
6. **Clean:** apply documented rules; quarantine rejected records rather than silently losing them.
7. **Derive:** calculate trip duration and temporal features when inputs are valid.
8. **Write curated data:** compressed Parquet partitioned by an appropriate time period.
9. **Aggregate:** produce daily, hourly, zone, payment, and distribution summaries.
10. **Load analytics store:** insert or replace the correct data period in ClickHouse safely.
11. **Verify:** compare row counts and aggregate totals between pipeline stages.
12. **Publish:** mark the dataset period available to the dashboard only after successful validation.
13. **Record:** persist metrics, logs, schema version, and source lineage.

### 7.2 Cleaning rules

Rules must be configurable and documented. Suggested starting rules:
- Reject or quarantine rows with unparseable required timestamps.
- Flag negative trip durations and drop-off timestamps earlier than pickup.
- Flag zero/negative distances; do not assume every short trip is invalid.
- Flag negative or extreme amounts rather than automatically deleting all of them.
- Treat passenger count of zero as a quality flag, not automatically as proof of invalidity.
- Detect duplicates with a documented key strategy; do not assume a single universal unique trip ID exists.
- Keep unknown location IDs and report them as unmapped rather than guessing zone names.

### 7.3 Data quality report

For every run, store:
- `source_file_count`
- `input_row_count`
- `accepted_row_count`
- `quarantined_row_count`
- `duplicate_row_count`
- `missingness_by_column`
- `invalid_timestamp_count`
- `invalid_distance_count`
- `invalid_amount_count`
- `schema_version`
- `duration_seconds`
- `input_bytes`
- `output_bytes`
- `started_at`
- `completed_at`
- `status`
- `error_summary`

---

## 8. Storage and Schema Design

### 8.1 PostgreSQL metadata tables

Suggested tables:
- `users`
- `roles` or role enum
- `data_sources`
- `datasets`
- `dataset_versions`
- `ingestion_jobs`
- `processing_runs`
- `data_quality_metrics`
- `saved_dashboard_views`
- `reports`
- `report_sections` (optional)
- `audit_events`
- `ai_conversations` (store only what is needed; define retention policy)
- `ai_tool_runs` (tool name, validated arguments, result metadata, duration, status)

Include primary keys, timestamps, foreign keys, indexes, and migrations. Avoid storing large raw datasets in PostgreSQL.

### 8.2 ClickHouse tables

Use a curated trip fact table and pre-aggregated tables. Final types and fields must follow the selected TLC schema.

Possible logical tables:
- `taxi_trips`
- `trips_daily`
- `trips_hourly`
- `trips_by_pickup_zone_daily`
- `trips_by_dropoff_zone_daily`
- `fare_distance_buckets`
- `data_quality_daily`

Use a sensible partition key based on month/date and a sorting key aligned to common filters. Validate performance with EXPLAIN and benchmarks. Avoid excessive indexes and overly granular partitions.

### 8.3 Parquet layout

Example—not a mandatory exact layout:

```text
data-lake/
  raw/
    taxi_type=yellow/year=2024/month=01/source-file.parquet
  curated/
    taxi_type=yellow/year=2024/month=01/part-*.parquet
  quarantine/
    run_id=<run-id>/...
  aggregates/
    trips_daily/...
```

Keep source files immutable. Use run IDs and dataset version metadata to prevent confusing data from different runs.

---

## 9. API Design

Use `/api/v1` versioning. Return consistent error objects and request IDs.

### Authentication
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`

### Data sources and datasets
- `GET /api/v1/data-sources`
- `POST /api/v1/data-sources`
- `GET /api/v1/datasets`
- `GET /api/v1/datasets/{dataset_id}`
- `GET /api/v1/datasets/{dataset_id}/schema`
- `GET /api/v1/datasets/{dataset_id}/quality`
- `GET /api/v1/datasets/{dataset_id}/preview`

### Processing jobs
- `POST /api/v1/ingestion-jobs`
- `GET /api/v1/ingestion-jobs`
- `GET /api/v1/ingestion-jobs/{job_id}`
- `POST /api/v1/ingestion-jobs/{job_id}/retry`
- `POST /api/v1/ingestion-jobs/{job_id}/cancel`
- `GET /api/v1/processing-runs/{run_id}/logs`

### Dashboard and analytics
- `GET /api/v1/analytics/overview`
- `GET /api/v1/analytics/trips-over-time`
- `GET /api/v1/analytics/trips-by-hour`
- `GET /api/v1/analytics/trips-by-weekday`
- `GET /api/v1/analytics/top-pickup-zones`
- `GET /api/v1/analytics/top-dropoff-zones`
- `GET /api/v1/analytics/fare-distance-distribution`
- `GET /api/v1/analytics/payment-types`
- `POST /api/v1/analytics/query` (strictly allowlisted query specification; not arbitrary SQL)

All analytics endpoints should accept validated shared filter fields where applicable: `dataset_id`, `start_date`, `end_date`, `pickup_zone`, `dropoff_zone`, `payment_type`, `vendor_id`, `min_distance`, `max_distance`, and supported dimensions. Reject unsupported filters clearly.

### Reports
- `GET /api/v1/reports`
- `POST /api/v1/reports`
- `GET /api/v1/reports/{report_id}`
- `POST /api/v1/reports/{report_id}/generate`
- `GET /api/v1/reports/{report_id}/download`
- `POST /api/v1/exports` for direct bounded CSV/XLSX exports

### AI analyst
- `POST /api/v1/ai/chat`
- `POST /api/v1/ai/report-outline`
- `POST /api/v1/ai/report-draft`
- `GET /api/v1/ai/conversations/{conversation_id}`

### System
- `GET /health`
- `GET /ready`
- `GET /api/v1/system/metrics` (administrator only)

The exact endpoint list can be simplified for the MVP, but keep API responsibilities separate and documented.

---

## 10. AI Agent Design

### 10.1 Approved tools

Implement tools with explicit input/output schemas, for example:

1. `get_overview_metrics(filters)`
2. `get_time_series(metric, granularity, filters)`
3. `compare_periods(metric, period_a, period_b, filters)`
4. `get_top_zones(zone_type, metric, limit, filters)`
5. `get_distribution(metric, buckets, filters)`
6. `get_data_quality_summary(dataset_id, period)`
7. `run_anomaly_analysis(metric, grouping, filters)`
8. `create_chart_spec(chart_type, x_field, y_metric, filters)`
9. `create_report_draft(report_type, filters, selected_sections)`

Every tool must:
- Validate input against Pydantic schemas.
- Check the user's permissions.
- Allow only known metrics and dimensions.
- Apply limits and query timeouts.
- Return structured results plus metadata.
- Log status and duration.
- Fail safely with a clear message.

### 10.2 Query safety

Prefer predefined query builders and parameterized SQL. Never concatenate raw user text into SQL. If a controlled SQL generation feature is added later, parse and validate it, allow only SELECT against approved views, reject multiple statements and unsafe functions, enforce limits, and still use read-only credentials. An allowlisted analytics-tool approach is the recommended first release.

### 10.3 AI response format

Each answer should be able to include:
- Direct answer.
- Evidence table or values.
- Time period and applied filters.
- Definitions of metrics used.
- Caveats or missing data.
- Suggested follow-up questions.

For report generation, request structured JSON from the model and validate it before rendering. If the model returns invalid structure, retry once or show a controlled error; do not silently generate a misleading report.

### 10.4 Provider abstraction

Create a small interface such as `LLMProvider.generate_structured(...)` and `LLMProvider.respond_with_tools(...)`. Keep model selection and API credentials in environment configuration. A no-key development mode should still let the rest of the project run, with the AI page showing a helpful disabled/configuration state and deterministic demo questions available.

---

## 11. Frontend Information Architecture

Required navigation:
1. Overview
2. Explore Data
3. Dashboards
4. AI Analyst
5. Reports
6. Data Sources
7. Processing Jobs
8. Data Quality
9. Settings / Admin (role-gated)

### Design direction
- Professional analytics product.
- Clear typographic hierarchy and consistent spacing.
- Light theme initially; dark mode optional.
- Sidebar navigation, top bar with dataset and date selection, reusable filter panel.
- KPI cards with metric definitions and period comparison where valid.
- Charts should include units, legends, tooltips, and empty/loading states.
- AI analyst should show tool execution/loading states and distinguish evidence from narrative.
- Report preview should closely match the exported PDF.
- Do not fill the UI with fake data in production paths. Clearly label any seed/demo data.

### Important interaction details
- Global filters should synchronize across dashboard sections.
- Users can reset filters to defaults.
- Clicking a chart segment should apply a drill-down filter where meaningful.
- Long jobs should not block the entire UI.
- Downloads should be enabled only when an export is complete.
- AI-created chart specs must be validated and rendered using an allowlist.

---

## 12. Testing and Acceptance Criteria

### 12.1 Unit tests
- Canonical schema mapping across supported input schema versions.
- Timestamp parsing and duration calculations.
- Cleaning and quarantine rules.
- Metric calculations.
- Filter validation and SQL/query builder safety.
- Report numeric formatting and spreadsheet formula-injection protection.
- Authorization checks.

### 12.2 Integration tests
- Ingest a small known Parquet fixture.
- Run Spark transformation.
- Verify output row counts and quality metrics.
- Load a test ClickHouse table.
- Check dashboard endpoint results against known expected values.
- Generate CSV, XLSX, and PDF and verify files exist and are readable.
- Verify AI tools call only approved analytics operations.
- Verify a viewer cannot access unauthorized data or reports.

### 12.3 End-to-end test
1. Sign in as analyst.
2. Open an available dataset.
3. Apply a date filter.
4. Verify KPI and chart updates.
5. Ask an AI question about a metric.
6. Verify the answer includes correct returned values and filter context.
7. Generate an executive report.
8. Export PDF and Excel.
9. Confirm report history records the successful job.

### 12.4 Acceptance criteria
The project is ready for demonstration when:
- A TLC source file can be ingested through the documented process.
- Spark performs meaningful transformations and emits processing metrics.
- Curated Parquet and ClickHouse tables are populated.
- Dashboard values match backend query results for a known test sample.
- Filters work consistently across KPIs and charts.
- CSV, XLSX, and PDF exports can be generated and downloaded.
- AI answers are grounded in tool results, with clear limits and no fabricated figures.
- AI-generated reports include filters, period, source, methodology, and limitations.
- Jobs and failures are visible.
- Security tests confirm role checks and bounded analytics queries.
- The README allows a teammate or evaluator to run the project from a clean checkout.

---

## 13. Benchmarking and Academic Evaluation

Include a benchmark report comparing at least two approaches, where feasible:
- Direct query against a small raw sample versus ClickHouse analytical query.
- Single-machine transformation versus Spark transformation on a larger dataset, if the environment supports a fair comparison.
- Before/after query optimization.
- Data volume and row counts.
- Wall-clock processing time.
- Peak memory where measurable.
- Disk footprint of raw versus compressed Parquet.
- Dashboard API latency for representative queries.

Document:
- CPU, RAM, storage, OS, Docker version.
- Dataset months and row count.
- Warm/cold cache conditions if relevant.
- Number of repetitions and measurement method.
- Limitations of the benchmark.

Do not claim “real-time,” “massive scale,” or “highly accurate AI” without measurements and definitions. Use reproducible results in the project presentation.

---

## 14. Suggested Repository Structure

```text
datapilot-ai/
  apps/
    web/                         # React/TypeScript frontend
    api/                         # FastAPI backend
    worker/                      # report/background worker
  data-pipeline/
    spark_jobs/
      ingest_tlc.py
      normalize_trips.py
      build_aggregates.py
      load_clickhouse.py
    schemas/
    quality_rules/
  analytics/
    query_builders/
    metric_definitions/
    tools/
  ai/
    provider.py
    prompts/
    schemas/
    agent.py
  reports/
    templates/
    renderers/
      csv_export.py
      excel_export.py
      pdf_export.py
  infrastructure/
    docker/
    clickhouse/
    postgres/
    minio/
    kafka/                        # optional extension
  migrations/
  tests/
    unit/
    integration/
    e2e/
  scripts/
    download_manifest.py
    initialize_services.py
    run_pipeline.py
    benchmark.py
  docs/
    architecture.md
    data-dictionary.md
    api.md
    benchmark-results.md
  .env.example
  .gitignore
  compose.yaml
  Makefile
  README.md
```

Claude may adjust this structure if it has a clear engineering reason, but it must preserve separation between frontend, API, pipeline, analytics, AI, and report rendering.

---

## 15. Environment Configuration

Provide `.env.example` with placeholders only, for example:

```dotenv
APP_ENV=development
APP_SECRET_KEY=replace-with-a-long-random-secret
DATABASE_URL=postgresql+psycopg://app:app_password@postgres:5432/datapilot
CLICKHOUSE_HOST=clickhouse
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=datapilot
CLICKHOUSE_USER=analytics_readwrite
CLICKHOUSE_PASSWORD=replace-me
S3_ENDPOINT_URL=http://minio:9000
S3_ACCESS_KEY=replace-me
S3_SECRET_KEY=replace-me
S3_BUCKET=datapilot-data
REDIS_URL=redis://redis:6379/0
LLM_PROVIDER=disabled
LLM_API_KEY=
LLM_MODEL=
MAX_UPLOAD_MB=500
MAX_EXPORT_ROWS=100000
ANALYTICS_QUERY_TIMEOUT_SECONDS=30
```

Use different credentials for application writes and AI/read-only analytics where possible. Never use these example values as production credentials. Add `.env` to `.gitignore`.

---

## 16. Implementation Phases

### Phase 0 — Discovery and design
- Inspect official TLC schema for selected files.
- Choose initial months and estimate disk usage.
- Write a short architecture decision record.
- Define metric formulas and cleaning rules before coding.
- Create wireframes or a UI page map.

**Deliverable:** architecture document, schema mapping, metrics dictionary, and task breakdown.

### Phase 1 — Vertical slice
Build the smallest end-to-end path:
1. Start PostgreSQL, ClickHouse, and MinIO.
2. Ingest one small TLC Parquet file.
3. Run a Spark transform.
4. Load a curated ClickHouse table.
5. Return one KPI and one time-series endpoint.
6. Render one dashboard chart.

**Deliverable:** a working, testable path from source file to dashboard.

### Phase 2 — Complete batch pipeline
- Multiple files and periods.
- Schema normalization.
- Data-quality reports and quarantine.
- Repeatable/idempotent loads.
- Job history and logs.

### Phase 3 — Dashboard and explorer
- All required metrics and charts.
- Filters, drill-downs, pagination.
- Data quality and schema pages.

### Phase 4 — Exports and report center
- CSV, XLSX, PDF.
- Templates, status tracking, report history.
- Filter-consistent report output.

### Phase 5 — AI analyst
- Provider adapter.
- Approved tools and schemas.
- Evidence-grounded responses.
- AI report outlines/drafts.
- Safety tests and clear configuration behavior.

### Phase 6 — Hardening and presentation
- Authentication and role checks.
- Error states and logging.
- Integration/e2e tests.
- Benchmarks.
- README, architecture diagram, demo script, and screenshots.

### Phase 7 — Optional streaming
- Kafka producer/consumer.
- Simulated events clearly labeled.
- Rolling aggregates and stream metrics.

Do not start with the AI agent or Kafka. Complete the end-to-end batch vertical slice first.

---

## 17. Team Responsibilities (Adjust to Team Size)

For a team of 3–4:

**Member A — Data Engineering**
- TLC source discovery, Spark transformations, data-quality rules, Parquet layout.

**Member B — Backend and Storage**
- FastAPI, PostgreSQL metadata, ClickHouse schema/query builders, jobs and APIs.

**Member C — Frontend and UX**
- Dashboard, filters, data explorer, report center, responsive UI.

**Member D — AI, Reporting, and QA**
- Approved AI tools, report generation, export validation, tests, integration and demo.

All members should review interfaces and test the integrated application. If there are fewer members, combine responsibilities rather than duplicating modules.

---

## 18. Demonstration Script

A strong 5–8 minute demo:

1. Show the source dataset, period, and schema.
2. Start or open an ingestion job and show row counts and quality checks.
3. Explain how Spark transforms records and writes Parquet/ClickHouse.
4. Open the dashboard and select a date range.
5. Drill into trip demand by hour and pickup zone.
6. Open data quality and show how anomalies are flagged, not automatically called fraud.
7. Ask the AI: “Compare trip volume by weekday and weekend for this period. Explain the largest difference.”
8. Show the actual analytical tool results behind the response.
9. Ask for an executive report and preview the evidence-backed findings.
10. Export PDF and XLSX and open the generated files.
11. Present benchmark results and discuss limitations.

---

## 19. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| TLC schema changes over time | Inspect schema per file and maintain versioned mappings |
| Dataset is too large for local hardware | Start with one month, scale incrementally, measure disk use |
| Dashboard queries are slow | Pre-aggregate in Spark, use ClickHouse, optimize sorting/partitioning |
| LLM invents insights | Require approved analytics tools and ground all numbers in returned results |
| Generated SQL is unsafe | Use allowlisted query builders and parameterized queries |
| AI report conflicts with dashboard | Share metric definitions and filter parsing across both paths |
| Missing zone-name mapping | Show location IDs and clearly label unavailable mapping |
| Exports consume too much memory | Stream or batch large exports and impose row limits |
| Re-ingestion duplicates records | Use run metadata, idempotent replacement/inserts, and validation |
| Team integrates too late | Maintain API contracts and integrate a vertical slice in Phase 1 |
| Kafka distracts from core goals | Treat streaming as an optional final phase |
| Claims exceed evidence | Include benchmark methods, dataset caveats, and clear metric definitions |

---

## 20. Required Documentation Deliverables

The final repository must include:
- `README.md` with prerequisites, setup, configuration, dataset acquisition, startup, tests, and troubleshooting.
- `docs/architecture.md` with component responsibilities and data flow.
- `docs/data-dictionary.md` with field definitions and source-specific availability.
- `docs/metric-definitions.md` with formulas and caveats.
- `docs/api.md` or generated OpenAPI documentation.
- `docs/benchmark-results.md` with reproducible measurements.
- `docs/security.md` covering auth, query safety, data access, and secrets.
- `docs/demo-script.md` for the project presentation.
- `.env.example` without real secrets.
- Automated tests for the core pipeline and key user journeys.

---

## 21. Instructions for Claude Code

Paste the following prompt into Claude Code after placing this specification in the repository as `PROJECT_SPEC.md`.

> You are the lead software architect and implementation engineer for TripScope. Read `PROJECT_SPEC.md` fully before making changes. Build the project as a reliable, testable application that follows the specification.
>
> **Execution rules**
>
> 1. First inspect the current repository and environment. Do not overwrite existing work without understanding it.
> 2. Produce `docs/implementation-plan.md` with architecture decisions, dependencies, milestones, and a checklist mapped to the specification.
> 3. Identify any assumptions and schema uncertainties. Verify the actual TLC file schema from a local sample or documented source; do not invent columns or hardcode unavailable fields.
> 4. Implement in phases. Start with the smallest end-to-end vertical slice: one TLC Parquet file → Spark transform → Parquet output → ClickHouse → FastAPI endpoint → frontend chart.
> 5. After each phase, run relevant tests, fix failures, and update the checklist. Do not claim a feature works unless you have run a meaningful verification.
> 6. Keep modules separated: frontend, API, pipeline, analytics/query builders, AI tools, and report renderers.
> 7. Use typed interfaces, clear names, linting/formatting, migrations, structured logging, and useful error messages.
> 8. Never commit secrets. Provide `.env.example`; fail safely when credentials or optional services are missing.
> 9. Do not use fake data in real product flows. Seed data may be used only for tests or a clearly labeled demo mode.
> 10. Do not let the LLM run arbitrary SQL, shell commands, or generated Python. Implement allowlisted analytics tools with input validation, authorization, timeouts, and output limits.
> 11. AI-generated numeric claims must be grounded in backend results. Include the selected date range, filters, metric definitions, source, and caveats.
> 12. Make report generation reliable: PDF, XLSX, and CSV exports; asynchronous status for long jobs; authorization; formula-injection protection for spreadsheet exports.
> 13. Add tests for data transformation, filters, metrics, authorization, AI tool safety, and exports.
> 14. Provide Docker Compose setup, health checks, startup instructions, troubleshooting, and benchmark instructions.
> 15. Avoid unnecessary complexity. Kafka and real-time streaming are optional until the batch pipeline, dashboard, reports, and AI workflow are stable.
>
> **At the end of each phase, report:** files created/changed, functionality implemented, exact commands run, test outcomes, known limitations, and the next phase. If a command fails, diagnose and fix it rather than hiding the failure.
>
> Begin by inspecting the repository and creating the implementation plan. Then implement Phase 1 as a complete vertical slice before expanding the system.

---

## 22. Definition of Done

The project is complete when a clean checkout can be configured using documented steps, a real TLC dataset can be processed through Spark, curated results are queryable in ClickHouse, the dashboard and filters work, exports produce valid files, and the AI analyst answers questions from approved analytics results without inventing numbers. Automated tests, a reproducible benchmark, and a presentation-ready demo must also be included.

**Build principle:** prioritize correctness, traceability, and a working end-to-end system over the number of technologies or UI screens.
