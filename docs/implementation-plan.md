# TripScope — Implementation Plan

Status: living document. Updated at the end of every phase. **Phases 1–2 complete; Phase 3 in progress.**
Spec: [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) (section references below use `§`).

---

## 1. Starting point (inspected 2026-10-09)

| Item | Finding |
|---|---|
| Repository | Empty except the specification. GitHub remote `mr-shakib/TripScope` was empty. |
| Host | Linux, 12 cores, 14 GiB RAM (~7 GiB free in normal use), 88 GB free disk |
| Tooling | Docker 29.1 + Compose v5, OpenJDK 21, Python 3.12/3.13/3.14 via `uv`, Node 24, pnpm/npm |
| Port conflict | Host port `5432` is used by another project's PostgreSQL → TripScope PostgreSQL is published on `5433` |
| Extra local file | `2023_Yellow_Taxi_Trip_Data_*.csv` — an NYC Open Data CSV export (1,823,575 rows, `MM/DD/YYYY hh:mm:ss AM` timestamps). Not used in Phase 1; useful as a CSV/schema-variant test input in Phase 2. Never committed. |

### 1.1 Verified source schema (Phase 0, from a real file — not assumed)

File: `yellow_tripdata_2025-01.parquet`, downloaded from the URL listed on the TLC portal
(`https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2025-01.parquet`), sha256 `9af277e4…485488a`, 59,158,238 bytes, 4 row groups, written by `parquet-cpp-arrow 16.1.0`.

| Source column | Parquet type | Nulls (of 3,475,226) | Observed range / values |
|---|---|---|---|
| `VendorID` | int32 | 0 | 1, 2, 6, 7 |
| `tpep_pickup_datetime` | timestamp[us], `isAdjustedToUTC=false` | 0 | 2024-12-31 20:47:55 → 2025-02-01 00:00:44 |
| `tpep_dropoff_datetime` | timestamp[us], `isAdjustedToUTC=false` | 0 | 2024-12-18 07:52:40 → 2025-02-01 23:44:11 |
| `passenger_count` | int64 | 540,149 | 0–9 |
| `trip_distance` | double | 0 | 0 → 276,423.57 |
| `RatecodeID` | int64 | 540,149 | 1–6, 99 |
| `store_and_fwd_flag` | string | 540,149 | N, Y |
| `PULocationID` / `DOLocationID` | int32 | 0 | 1–265 |
| `payment_type` | int64 | 0 | 0–5 (0 = Flex Fare: 540,149 rows) |
| `fare_amount` | double | 0 | −900 → 863,372.12 |
| `extra`, `mta_tax`, `tip_amount`, `tolls_amount`, `improvement_surcharge` | double | 0 | include negatives |
| `total_amount` | double | 0 | −901 → 863,380.37 (63,037 negative) |
| `congestion_surcharge` | double | 540,149 | −2.5 → 2.5 |
| `Airport_fee` (**capitalised in this file**) | double | 540,149 | −1.75 → 6.75 |
| `cbd_congestion_fee` | double | 0 | −0.75 → 0.75 (exists from Jan 2025) |

Observed quality signals: 22 pickups outside January 2025, 124 drop-offs before pickup, 1,927 zero-duration trips,
90,893 zero/negative distances, 162 distances > 100 mi, 24,656 zero passenger counts, 9,521 pickups in zone 264/265, 0 exact duplicate rows.

Code meanings come from the official *Data Dictionary – Yellow Taxi Trip Records, March 18, 2025*:
VendorID 1 Creative Mobile Technologies, 2 Curb Mobility, 6 Myle Technologies, 7 Helix; RatecodeID 1 Standard, 2 JFK, 3 Newark,
4 Nassau/Westchester, 5 Negotiated, 6 Group ride, 99 Null/unknown; payment_type 0 Flex Fare, 1 Credit card, 2 Cash, 3 No charge,
4 Dispute, 5 Unknown, 6 Voided. `tip_amount` excludes cash tips; `total_amount` excludes cash tips.

Zone lookup (`taxi_zone_lookup.csv`, 265 rows): IDs 264 (`Unknown`/`N/A`) and 265 (`N/A`/`Outside of NYC`) are not real zones;
they are reported as **unmapped**, never assigned a guessed name.

---

## 2. Architecture decisions

| # | Decision | Reason |
|---|---|---|
| ADR-01 | **One Python package `tripscope` under `backend/`** with sub-packages `core`, `storage`, `metadata`, `pipeline`, `analytics`, `api` (later `ai`, `reports`). Frontend in `frontend/`. | The spec (§14, §19) requires the dashboard, AI tools and reports to share metric definitions and filter parsing. One importable package avoids `sys.path` hacks. Module boundaries are enforced by import direction: `api → analytics/metadata`, `ai → analytics`, `pipeline → storage/metadata`; `analytics` never imports `api`. |
| ADR-02 | **PySpark 4.2 in `local[*]` mode** with Python 3.12. Spark reads the immutable raw file from a local staging copy and writes curated/quarantine Parquet locally. The pipeline then publishes the outputs to MinIO, which is the system of record. | Spark → S3A needs `hadoop-aws` plus the AWS SDK v2 bundle (~600 MB of jars) and fragile version pinning. Local staging keeps Spark simple and reproducible. S3A can be added later without changing the lake layout. |
| ADR-03 | **ClickHouse loads curated Parquet straight from MinIO** with the `s3()` table function and a server-side named collection. | No Python bottleneck moves millions of rows. Storage credentials live in ClickHouse server config, never in query text or logs. ClickHouse loads from the lake objects whose lineage we record. |
| ADR-04 | **Idempotent loads via staging + `REPLACE PARTITION`**. `taxi_trips` is partitioned by `(taxi_type, toYYYYMM(pickup_date))`. Each run loads into a staging table, checks row counts and amount sums against Spark's metrics, then atomically replaces the partition. | Re-running a period replaces it instead of duplicating it (FR-03, §7.1 step 10, §19). Data is visible only after verification (§7.1 steps 11–12). |
| ADR-05 | **Rows outside the source file's month are quarantined** (`pickup_outside_source_period`). | Monthly files contain stray rows (22 in 2025-01, back to 2024-12). Quarantining keeps each partition = one source month, so `REPLACE PARTITION` is exact, and rows are preserved for audit. |
| ADR-06 | **Timestamps: NYC local wall-clock, no timezone conversion.** Spark session TZ = UTC, so `TIMESTAMP_NTZ` values pass through unchanged. ClickHouse stores them in `DateTime64(…,'UTC')` columns that are *documented* as local wall-clock. `pickup_date`, `pickup_hour` and `pickup_day_of_week` are derived in Spark. | The source Parquet marks timestamps `isAdjustedToUTC=false` (local time). Converting would require guessing around DST changes. Deriving calendar fields once in Spark keeps the dashboard independent of ClickHouse's timezone settings. |
| ADR-07 | **Two kinds of quality outcome.** *Quarantine* removes a row from curated data (missing required timestamp, drop-off before pickup, outside source period, exact duplicate). *Flags* keep the row and mark it (distance, duration, amount, passenger count, unmapped zone). Each metric states which flags exclude rows, and the API returns the excluded counts. | Implements §7.2: suspicious ≠ invalid; nothing is silently dropped; untrustworthy values are not shown as zero (FR-07). |
| ADR-08 | **Separate ClickHouse users.** `tripscope_writer` (pipeline: DDL/INSERT on the TripScope DBs + S3 source) and `tripscope_reader` (API/AI: `SELECT` only, readonly settings profile, `max_execution_time`, row/byte limits enforced server-side). | §5 Security: read-only credentials for analytics/AI tools; limits enforced even if application code has a bug. |
| ADR-09 | **Analytics = allowlisted query builder** with ClickHouse server-side typed parameters (`{name:Type}`). Filters are a strict Pydantic model (`extra="forbid"`) with bounded date ranges. Metric definitions live in one registry shared by the API, reports and AI tools. | §10.2 — no raw user text in SQL; no LLM-generated SQL in the first release. |
| ADR-10 | **Auth: argon2 password hashes in PostgreSQL; signed session JWT in an `HttpOnly`, `SameSite=Lax` cookie** (`Secure` outside development). Roles `admin`/`analyst`/`viewer` are checked by backend dependencies on every protected route. Login/logout/failed logins are written to `audit_events`. | FR-01. Cookies keep tokens away from JS; the JSON-only API plus SameSite=Lax blocks cross-site form CSRF. |
| ADR-11 | **AI provider = OpenAI-compatible adapter** (configurable `base_url`, model, key) covering **DeepSeek API and local servers** (Ollama / vLLM / llama.cpp). Capability flags (`supports_tools`, `supports_json_schema`) choose native tool calling or a validated JSON-plan fallback. `LLM_PROVIDER=disabled` is the default. | The team will use a local model or DeepSeek (§10.4). Phase 5 only — not built until the batch path, dashboard and exports are stable. |
| ADR-12 | **Frontend: Vite + React 19 + TypeScript + Tailwind v4 + ECharts (direct, tree-shaken) + TanStack Query + React Router.** Dev server proxies `/api` to FastAPI so cookies stay same-origin; in Docker, nginx serves the build and proxies `/api`. | §6.1 stack; same-origin avoids CORS-with-credentials complexity. |
| ADR-13 | **Kafka is out of scope** until the batch pipeline, dashboard, report exports and AI workflow are stable (user directive + §16 Phase 7). | — |
| ADR-14 | **Frontend moves to Next.js 16 (App Router)** (user directive, Phase 2). Pages are client components fed by TanStack Query through same-origin rewrites (`/api/*` → FastAPI); `proxy.ts` redirects signed-out visitors and sets a per-request CSP nonce; Docker runs the standalone server. **Light theme first**, dark theme later. | Same-origin keeps the HttpOnly session cookie first-party with no CORS; the API stays the only place authorization is enforced. |
| ADR-15 | **PostgreSQL-backed job queue + worker** (`SELECT … FOR UPDATE SKIP LOCKED`) instead of Celery/Redis. API creates `queued` jobs; a worker container claims, heartbeats and runs them; cancel = flag checked between stages plus Spark job cancellation; jobs on a dead worker are failed by heartbeat timeout. | One fewer service; job state already lives in PostgreSQL; spec allows a simpler worker first (§6.1). |
| ADR-16 | **Pre-aggregates built explicitly per partition** from the verified staging data, then swapped with `REPLACE PARTITION` together with the fact table. Queries use an aggregate only when every requested filter is covered by its dimensions; results must equal raw queries (tested). | Materialized views do not fire on `REPLACE PARTITION`; explicit builds keep aggregates consistent with the published run. |
| ADR-17 | **CSV sources are validated structurally before Spark** (header, consistent field count on every line, server-error payloads) and rejected if truncated. | A real NYC Open Data export in this workspace ends with a `{"error": true, "status": 500}` body after 1.82M rows (data stops 2023-01-20); publishing it would misstate coverage. |

### 2.1 Logical flow (Phase 1)

```text
manifests/sources.yaml ──► acquire (download/verify sha256, size, PAR1, required columns)
                                │
                                ├──► MinIO raw/taxi_type=yellow/year=2025/month=01/<file>   (immutable)
                                ▼
                     Spark local[*]: normalize → flag/quarantine → derive → quality metrics
                                │
                                ├──► MinIO curated/taxi_type=yellow/year=2025/month=01/run_id=<run>/part-*.parquet
                                ├──► MinIO quarantine/run_id=<run>/part-*.parquet
                                ▼
           ClickHouse: s3(named collection) → taxi_trips_stage_<run> → verify → REPLACE PARTITION → taxi_trips
                                │
   PostgreSQL: data_sources, ingestion_jobs, processing_runs, data_quality_metrics, dataset_periods (published), audit_events
                                ▼
         FastAPI (reader creds) /api/v1/analytics/overview, /trips-over-time ──► React KPI cards + ECharts line chart
```

---

## 3. Repository layout

```text
backend/
  pyproject.toml, uv.lock
  src/tripscope/
    core/        settings (pydantic-settings), JSON logging, errors
    storage/     S3/MinIO object store wrapper
    metadata/    SQLAlchemy models + repositories (PostgreSQL)
    pipeline/    manifest, acquire, schema mapping, quality rules, spark_transform, clickhouse_load, run (CLI)
    analytics/   filters, metric registry, query builder, ClickHouse reader client, service
    api/         FastAPI app, routers (auth, datasets, analytics, health), deps, error handlers
    ai/          (Phase 5) provider adapter, tools, prompts, schemas
    reports/     (Phase 4) csv/xlsx/pdf renderers, templates
  migrations/    Alembic
  tests/unit, tests/integration, tests/fixtures
frontend/        Vite React app (+ Playwright e2e smoke)
infrastructure/
  clickhouse/    config.d (named collection, timezone), init (users, DBs, tables)
  postgres/      init (app + test databases)
  docker/        Dockerfiles (api, pipeline, web)
manifests/       sources.yaml (source manifest: URLs/paths, period, expected checksum)
scripts/         helper scripts (bootstrap users, benchmarks later)
docs/            plan, architecture, data dictionary, metric definitions, security, api, benchmark
compose.yaml, Makefile, .env.example, README.md
```

---

## 4. Dependencies (pinned at implementation time)

Backend (Python 3.12): pyspark 4.2.0, pyarrow 26, fastapi 0.143, uvicorn 0.54, pydantic 2.14, pydantic-settings 2.15,
sqlalchemy 2.1, alembic 1.20, psycopg 3.3, clickhouse-connect 1.10, boto3, httpx, pyjwt 2.15, argon2-cffi, pyyaml;
dev: pytest 9, ruff, mypy.
Frontend (Phase 2+): next 16.4, react 19.3, typescript 5.9, tailwindcss 4.3, radix-ui 1.7, echarts 6.1, @tanstack/react-query 5, sonner, lucide-react, vitest, Playwright.
Services: postgres:16-alpine, clickhouse/clickhouse-server:25.8, minio/minio:RELEASE.2025-09-07T16-13-09Z
(the MinIO community image is pinned to a published release; S3-compatible alternatives are possible because the code uses the generic S3 API).

---

## 5. Canonical schema (FR-05) — Phase 1 mapping

Source columns are matched **case-insensitively** through an alias table (`Airport_fee`/`airport_fee` → `airport_fee`;
`tpep_pickup_datetime`/`lpep_pickup_datetime` → `pickup_datetime`, etc.). A canonical field missing from a source file is
written as NULL and listed in `processing_runs.unavailable_fields`; it is never fabricated. Unknown extra source columns are
recorded in the schema report and ignored.

Canonical: `vendor_id, pickup_datetime, dropoff_datetime, passenger_count, trip_distance, pickup_location_id,
dropoff_location_id, rate_code_id, store_and_fwd_flag, payment_type, fare_amount, extra, mta_tax, tip_amount, tolls_amount,
improvement_surcharge, total_amount, congestion_surcharge, airport_fee, cbd_congestion_fee` + lineage
`taxi_type, source_file, run_id, ingested_at, data_period` + derived `pickup_date, pickup_hour, pickup_day_of_week,
trip_duration_minutes, average_speed_mph, fare_per_mile` + validity flags.

## 6. Quality rules (configurable; defaults)

| Rule | Outcome | Default |
|---|---|---|
| Pickup or drop-off timestamp missing/unparseable | quarantine `missing_required_timestamp` | — |
| Drop-off earlier than pickup | quarantine `dropoff_before_pickup` | — |
| Pickup outside source month | quarantine `pickup_outside_source_period` | — |
| Exact duplicate of all source fields (keep first) | quarantine `duplicate_record` | key = all source columns |
| Distance ≤ 0 or > max | flag `invalid_distance` → `is_distance_valid=0` | max 200 mi |
| Duration ≤ 0 or > max | flag `invalid_duration` → `is_duration_valid=0` | max 720 min |
| total/fare amount < 0 or total > max | flag `invalid_amount` → `is_amount_valid=0` | max $1,000 |
| Speed > max (only when distance & duration valid) | flag `implausible_speed`, speed left NULL | max 80 mph |
| Passenger count NULL or 0 | flag `passenger_count_missing_or_zero` (not invalid) | — |
| Location ID not in lookup, or 264/265 | flag `pickup_zone_unmapped` / `dropoff_zone_unmapped` | — |

## 7. Phase 1 metric definitions (single registry, documented in `docs/metric-definitions.md`)

| Metric | Formula | Rows included |
|---|---|---|
| `total_trips` | `count()` | all accepted (curated) trips |
| `total_recorded_amount` | `sum(total_amount)` USD | `is_amount_valid = 1` (excludes refunds/disputes with negative amounts and > $1,000 outliers; cash tips are never included per TLC) |
| `avg_total_amount` | `avg(total_amount)` USD | `is_amount_valid = 1` |
| `avg_trip_distance` | `avg(trip_distance)` miles | `is_distance_valid = 1` |
| `avg_trip_duration_minutes` | `avg(trip_duration_minutes)` | `is_duration_valid = 1` |

Each response carries the applied filters, coverage dates, dataset/run lineage and the number of rows excluded by flags.

---

## 8. Milestones and checklist (mapped to the spec)

Legend: ☐ not started · ◐ in progress · ☑ done and verified by a run/test.

### Phase 0 — Discovery (§16)
- ☑ Inspect real TLC schema for the selected file (§2.1, §21.3) — see §1.1
- ☑ Choose the initial file (2025-01 Yellow); confirm code meanings from the official dictionary
- ☑ Architecture decisions, schema mapping, cleaning rules, metric formulas (this document)

### Phase 1 — Vertical slice (§16) — **complete (2026-10-09)**
- ☑ Docker Compose: PostgreSQL, ClickHouse, SeaweedFS (S3) with health checks; init scripts (DBs, users, buckets); `app` and `pipeline` profiles
- ☑ `.env.example`, `make env`, settings that refuse placeholder secrets, JSON structured logging
- ☑ Source manifest + acquire (allow-listed HTTPS, size cap, sha256 pin, PAR1 + footer, required columns, immutable raw upload) — FR-02
- ☑ Schema inspection + canonical mapping (case-insensitive, unavailable fields recorded, schema fingerprint) — FR-05
- ☑ Spark transform: normalise, quarantine, flag, derive; §7.3 quality metrics persisted — FR-04/05
- ☑ Curated + quarantine Parquet in the lake under run-scoped paths — FR-06, §8.3
- ☑ ClickHouse staging load from the lake, exact verification, `REPLACE PARTITION`, publish — §7.1 steps 10–12
- ☑ PostgreSQL metadata via Alembic 0001 (users, datasets, data_sources, dataset_periods, ingestion_jobs, processing_runs, data_quality_metrics, audit_events)
- ☑ FastAPI: `/health`, `/ready`, auth (login/logout/me), `/datasets`, `/analytics/overview`, `/analytics/trips-over-time`, `/ingestion-jobs`; request IDs; consistent errors
- ☑ Frontend: sign-in, Overview with date filters, KPI tiles with definitions, trips-over-time chart + table view, loading/empty/error states, light/dark
- ☑ Tests: 63 backend unit, 10 integration, 10 frontend unit, 2 Playwright e2e (dev server and container build)
- ☑ Real 2025-01 file processed on the host and in the container
- ☑ README quick start, architecture, data dictionary, metric definitions, security; PR merged

**Verification record (Phase 1)**

| Check | Result |
|---|---|
| Real file `yellow_tripdata_2025-01.parquet` | 3,475,226 in → 3,475,080 published, 146 quarantined (124 drop-off < pickup, 22 outside month), 0 duplicates |
| Pipeline time | host 33.4 s (Spark 30.4 s); container 44.8 s incl. 7.8 s download |
| Staging verification | ClickHouse row count and `sum(total_amount)` equal Spark's exactly (decimal) before and after `REPLACE PARTITION` |
| Idempotency | 3 runs of the same month → one run_id in ClickHouse, 3,475,080 rows; job attempts 1 (failed, recorded), 2, 3 |
| API vs independent SQL | JFK card pickups 6–12 Jan: 24,462 trips, $2,172,447.18, 16.7859 mi — identical |
| Timestamps | min/max pickup `2025-01-01 00:00:00` / `2025-01-31 23:59:59`: wall-clock preserved, no shift |
| Security checks | reader cannot write/DDL/`url()`/`s3()`/raise limits; lake ClickHouse identity cannot write; forged `alg=none` cookie → 401; viewer → 403 on jobs; unknown params → 422 |
| Lint / types | ruff, ruff format, mypy `--strict` (41 files), tsc strict, eslint: clean |

**Deviations from the spec's suggestions (with reasons)**
- *SeaweedFS instead of MinIO:* MinIO images are no longer pullable from Docker Hub or quay.io. SeaweedFS is S3-compatible, and all code uses the generic S3 API.
- *Spark writes locally, then the pipeline publishes to the lake* (ADR-02): avoids ~600 MB of S3A jars. ClickHouse still reads curated data from the lake.
- *Host Spark needs a full JDK 17+:* the machine's default `JAVA_HOME` (Android Studio JBR) lacks `jdk.incubator.vector`. `SPARK_JAVA_HOME` plus a preflight check handle this, and the container uses OpenJDK 21.
- *CI workflow not added yet:* the GitHub token in use lacks the `workflow` scope needed to push `.github/workflows/`.

**Known limitations carried forward**
- Ingestion is CLI-triggered. API-triggered jobs with retry/cancel and a worker arrive in Phase 2.
- Only the date filter is exposed in the UI; the API already validates the zone, payment, vendor, hour and distance filters.
- The login throttle is per process; sessions are stateless JWTs (revocation planned for Phase 6).
- The API image carries pyarrow and boto3, which only the pipeline needs (~700 MB image); slimming is planned.

### Phase 2 — Complete batch pipeline + operations UI (Next.js) — **complete (2026-10-10)**
- ☑ Six contiguous months (2025-01 → 2025-06) in the manifest with pinned checksums; all published
- ☑ CSV sources (NYC Open Data export format): strict structural validation, explicit timestamp format, truncated exports rejected
- ☑ Schema registry and drift report across files (`GET /datasets/{id}/schema`)
- ☑ API-triggered jobs: create / list / get / retry / cancel, run logs endpoint, audit events; PostgreSQL queue + worker container with heartbeat and cancellation
- ☑ Pre-aggregates (`trips_hourly_agg`, `trips_dropoff_daily_agg`, `fare_distance_buckets`, `data_quality_daily`) built per partition; query routing with raw-vs-aggregate equality tests; `build-aggregates` command
- ☑ Quality API (`GET /datasets/{id}/quality`): per-period metrics, quarantine reasons, flags, missingness, daily flag trends
- ☑ Analytics additions served from aggregates: trips by hour, by weekday, top pickup zones (zone names from the TLC lookup), weekday filter
- ☑ Next.js frontend (light theme): Overview with global filters and click-to-filter charts; Data sources; Processing jobs; Data quality
- ☑ Tests: 75 backend unit, 26 integration, 12 Vitest, 6 Playwright e2e (dev server and container build)
- ☑ Docs updated (README, architecture, API reference, security, metric definitions); PR merged

**Verification record (Phase 2)**

| Check | Result |
|---|---|
| Six months published | 24,083,384 rows read → 24,082,454 published, 930 quarantined (793 drop-off < pickup, 137 outside month); one schema version, no drift |
| Real truncated CSV in this workspace | Rejected in 2.3 s: "line 1823572: the export ends with a server response instead of data … after 1,823,570 rows" |
| Queue → worker | API-queued job claimed by the worker, live stage visible, completed; duplicate request → 409 |
| Cancel mid-Spark | Cancelled 1.1 s after the request; summary "nothing was published"; ClickHouse run_id and row count unchanged; retry linked as attempt 3 |
| Container worker | Real 2025-04 run in Docker (OpenJDK 21): 3,970,553 → 3,970,383, identical to the host run |
| Aggregates | Backfill of 6 months in 5.8 s, each verified exactly; overview 178 → 36 ms, daily series 29 → 12 ms, top zones 65 → 17 ms (24.1M → 2.2M rows read) |
| Raw vs aggregate equality tests | Caught and fixed two discrepancies before release: decimal-scale truncation of the average amount; aggregate total 0 where the fact table reports no data |
| UI ↔ API | Playwright: hero KPI equals the API after every filter interaction (weekday bar click, zone search, hour picker, chip removal, reset, month preset); table sum equals the KPI |
| Security | Admin-only job mutations; viewer 403 on sources/jobs; nonce CSP without `unsafe-eval` in production; no open redirect after login |

**Deviations and decisions**
- Frontend rebuilt in Next.js 16 at the user's request (ADR-14); light theme only for now.
- Job start/retry/cancel are admin-only, following spec §3.1; analysts can view jobs, sources and logs.
- The NYC Open Data CSV export in this workspace is truncated, so CSV ingestion is demonstrated with a fixture in the same format; the real file is used to prove rejection.

**Known limitations carried forward**
- File upload through the UI (FR-02 "uploaded file") is not built yet; sources come from the manifest.
- Drop-off zone and distance-range filters exist in the API but not yet in the UI (Phase 3), and they read the fact table.
- The login throttle is per process; sessions are stateless JWTs (revocation in Phase 6).
- The API image still carries pyarrow and boto3, which only the pipeline needs.

### Phase 3 — Dashboards, zone map and data explorer — **in progress**
- ☐ Every FR-07 KPI: add trips per day; period-over-period comparison when a date range is selected
- ☐ Every FR-07 chart: trip-distance and total-amount distributions (outliers capped and disclosed), payment types, top drop-off zones, pickup→drop-off flows, hour × weekday heatmap
- ☐ Every FR-07 filter in the UI: drop-off zone, vendor, trip-distance range, vehicle type (single published type shown, not hidden)
- ☐ Click-to-filter on every chart where meaningful (payment slice, distance bucket, heatmap cell, zone, flow)
- ☐ Taxi-zone choropleth from the official TLC shapefile (reprojected EPSG:2263 → WGS84, simplified, stored in the lake); click a zone to filter
- ☐ Source routing generalised: each table declares the filters, dimensions and metrics it can answer; the first that covers a request wins
- ☐ Metric-definitions panel available from every analytics page
- ☐ Data explorer (FR-08): field catalogue with types, descriptions and availability; bounded, paginated, sortable row preview with quality filters; applied filters and row count; CSV extract with row limit, formula-injection protection and audit
- ☐ Tests: backend unit + integration for every new query path (aggregate vs raw equality), export safety; Vitest; Playwright for dashboards, map and explorer
- ☐ Docs updated; PR merged

### Phase 4 — Exports and report center
- ☐ CSV/XLSX (formula-injection safe, frozen panes, formats)/PDF; templates 1–5; async status; authorized downloads; report history

### Phase 5 — AI analyst (local model or DeepSeek)
- ☐ OpenAI-compatible provider adapter + disabled mode; tool registry (§10.1) over the shared analytics service
- ☐ Evidence-grounded answers, numeric-claim verification, report outline/draft (template 6), ai_tool_runs logging
- ☐ Prompt-injection and tool-safety tests; evaluation set comparing providers

### Phase 6 — Hardening and presentation
- ☐ Dataset-level permissions, admin user management, audit views, rate limiting
- ☐ Full e2e test (§12.3), benchmarks (§13), all docs in §20, demo script

### Phase 7 — Optional streaming (only after Phases 1–6 are stable)
- ☐ Kafka producer/consumer with clearly labelled synthetic events

---

## 9. Assumptions and open uncertainties

1. Other months/years may differ in schema. The 2023 CSV uses lower-case `airport_fee` and lacks `cbd_congestion_fee`; earlier years may lack `airport_fee`/`congestion_surcharge`. Mapping is per-file and verified at runtime.
2. Plausibility thresholds (200 mi, 720 min, $1,000, 80 mph) are documented starting values, not facts. They are configurable and reported with every run.
3. `payment_type = 0` ("Flex Fare") rows have NULL passenger/rate/store-and-forward fields in 2025-01. They are kept (they are real trips), and the missingness is reported.
4. TLC timestamps are treated as NYC local wall-clock time; DST-ambiguous hours are not disambiguated.
5. Duplicate detection uses exact equality on all source fields; TLC provides no trip ID, so near-duplicates are out of scope.
6. Single-machine benchmark only; no claims about cluster scale.

## 10. Proposed extensions (not in the spec — pending team approval)

Platform: taxi-zone choropleth map + origin→destination flows; Green/FHV/HVFHV datasets for scale and market-share comparison;
congestion-pricing before/after study (CBD fee from 2025-01-05) with caveats; airport analytics; weather/holiday overlays
(labelled correlation-only); lineage drill-through from any number to its raw file; schema-drift/quality alerts; evaluated
demand forecasting labelled *predictive*; saved views as shareable URLs; scheduled reports.

AI (local model or DeepSeek): number-placeholder drafting (`{{metric:…}}` filled by the backend); hard numeric-claim verifier;
evidence panel linking each sentence to a tool run; plan → approve → execute investigations; natural-language → validated
dashboard filters; post-ingestion "what changed" digest; glossary Q&A over the data dictionary with local embeddings;
evaluation harness (tool-selection accuracy, grounding rate, hallucinated-number rate, prompt-injection suite) comparing
local vs DeepSeek. Privacy rule: only bounded aggregates are ever sent to an external provider; a local-only switch can enforce it.

## 11. Working agreements

- Small commits pushed after each completed unit; feature branches merged through pull requests.
- No AI co-author trailers in commits or PRs.
- A feature is marked ☑ only after a command or test demonstrated it; failures are reported with their output.
