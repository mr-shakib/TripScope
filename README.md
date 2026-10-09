# TripScope

A Big Data analytics platform for NYC Taxi & Limousine Commission (TLC) trip records:
**PySpark** validates and transforms monthly TLC Parquet files, curated data lands in an **S3 data lake**
and **ClickHouse**, a **FastAPI** service serves bounded, parameterised analytics, and a **React** dashboard
renders it. Later phases add report exports and an AI analyst grounded in approved analytics tools
(see [`docs/implementation-plan.md`](docs/implementation-plan.md)).

> **Status: Phase 2 complete** — six months of Yellow Taxi data (24.1M trips) published through a queued,
> cancellable batch pipeline, with pre-aggregates and a Next.js operations UI. Plan and checklist:
> [`docs/implementation-plan.md`](docs/implementation-plan.md). Specification: [`PROJECT_SPEC.md`](PROJECT_SPEC.md).

## What works today

| Layer | Implemented |
|---|---|
| Ingestion | Source manifest (pinned SHA-256, HTTPS host allowlist), Parquet and CSV (NYC Open Data export) sources, size caps, structural validation that rejects truncated exports, immutable raw upload |
| Spark | Per-file canonical schema mapping, quarantine (4 reasons), quality flags (7), derived fields, run metrics |
| Storage | Raw / curated / quarantine Parquet in the lake (SeaweedFS, S3 API); ClickHouse fact table + 4 pre-aggregates, all swapped per month after exact verification |
| Jobs | PostgreSQL queue + worker: queue from the UI/API, live stage progress, cancel (also mid-Spark), retry, run logs, stale-worker recovery |
| API | Auth, datasets, schema registry and drift, quality reports, analytics (overview, time series, hour, weekday, top zones), jobs, data sources, health |
| Web | Next.js: Overview with global, URL-synced filters and click-to-filter charts; Data sources; Processing jobs; Data quality (light theme) |

Measured on this machine (AMD Ryzen 5 5500, 12 threads, 14 GiB RAM):

| Item | Result |
|---|---|
| Published data | 2025-01 → 2025-06: 24,083,384 rows read, 24,082,454 published, 930 quarantined |
| Pipeline per month | 35–41 s on the host (Spark ~30 s); ~62 s in the worker container incl. download |
| Aggregate backfill | 6 months in 5.8 s, each verified to the exact trip count and dollar total |
| Six-month overview query | 178 ms on `taxi_trips` (24.1M rows read) vs 36 ms on `trips_hourly_agg` (2.2M rows) |
| Daily series / top zones | 29 → 12 ms / 65 → 17 ms (fact table → aggregate) |

Single-machine, warm-cache medians of 5 runs; the reproducible benchmark is Phase 6.

## Prerequisites

- Docker with Compose v2 (tested: Docker 29.1, Compose 5.5)
- For host development: Python 3.12 via [uv](https://docs.astral.sh/uv/), Node 24, and a **full JDK 17+**
  for Spark. Trimmed runtimes such as Android Studio's JBR lack `jdk.incubator.vector`; set
  `SPARK_JAVA_HOME` in `.env` to a full JDK. The pipeline checks this and prints a clear error.
- About 2 GB of free disk per month of Yellow data (raw + curated + ClickHouse), plus about 3 GB for images.

## Quick start (everything in containers)

```bash
make env                      # writes .env with random local secrets (never commit it)
make up                       # PostgreSQL, ClickHouse, SeaweedFS (+ bucket init)
docker compose --profile app run --rm --entrypoint tripscope-admin api \
  create-user --email you@example.org --name "You" --role admin     # prompts for a password (12+ chars)
make app                      # API, worker and web on http://127.0.0.1:8080
```

Sign in, open **Data sources** and queue the months you want (or run
`make pipeline-docker SOURCE=yellow-2025-01` for a one-off container run). The worker publishes each month
once every check passes; progress and logs are on **Processing jobs**.

## Host development

```bash
make env && make up
cd backend && uv sync --extra pipeline && cd ..
cd frontend && npm ci && cd ..
make migrate                  # Alembic + ClickHouse migrations
make pipeline                 # SOURCE=yellow-2025-01 by default (or: cd backend && uv run tripscope-pipeline run --all)
make user EMAIL=you@example.org NAME="You" ROLE=admin
make api                      # terminal 1: http://127.0.0.1:8000 (OpenAPI at /api/docs)
make worker                   # terminal 2: processes jobs queued from the UI
make web                      # terminal 3: http://127.0.0.1:3000
```

Roles: `admin`, `analyst`, `viewer`. All three read published analytics and data quality. Admins and analysts
see data sources and jobs; only admins start, cancel or retry runs.

## Dataset acquisition

Sources are configuration, not code: [`manifests/sources.yaml`](manifests/sources.yaml) lists each monthly
file with its official TLC URL and the SHA-256 we validated (pinned on first retrieval). To add a month, copy
the URL from the [TLC trip record page](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page), add an
entry, and queue it from **Data sources**. Re-running a source replaces its month; it never duplicates it.

CSV exports from NYC Open Data are supported with `format: csv` and `csv_profile: nyc_open_data`. Check a file
before adding it: `cd backend && uv run tripscope-pipeline inspect-file PATH --format csv`. A truncated export
(one that ends with a server error instead of data) is rejected with the line and row count.

**Attribution:** data is from the NYC Taxi & Limousine Commission (TLC) Trip Record Data,
https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page. Source files are not redistributed in this
repository. TLC records are operational trip records: they can contain anomalies and revisions, and an unusual
record is not evidence of fraud.

## Tests

```bash
make lint               # ruff, ruff format --check, mypy --strict, tsc, eslint
make test-unit          # 75 backend unit tests incl. local Spark transform tests
make test-integration   # 26 tests against the running services (isolated tripscope_test DBs and bucket)
make test-frontend      # type check, lint, 12 Vitest tests
E2E_ADMIN_EMAIL=… E2E_ADMIN_PASSWORD=… E2E_VIEWER_EMAIL=… E2E_VIEWER_PASSWORD=… make e2e   # Playwright, dev server
E2E_BASE_URL=http://127.0.0.1:8080 … make e2e                                                  # container build
```

## Repository layout

```text
backend/          Python package `tripscope` (core, storage, metadata, jobs, pipeline, analytics, api) + tests + Alembic
frontend/         Next.js 16 app (Tailwind v4, Radix, ECharts, TanStack Query) + Vitest + Playwright
infrastructure/   ClickHouse config/users, PostgreSQL init, SeaweedFS identities, Dockerfiles
manifests/        Source manifest (URLs, periods, pinned checksums)
docs/             Implementation plan, architecture, data dictionary, metric definitions, security
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `JAVA_GATEWAY_EXITED` or "lacks modules Spark 4 needs" | Set `SPARK_JAVA_HOME` to a full JDK 17+, or use `make pipeline-docker` |
| `placeholder values must be replaced` | Run `make env` (or edit `.env`); placeholders are rejected on purpose |
| Port 5432 in use | TripScope's PostgreSQL publishes on `POSTGRES_HOST_PORT` (default 5433) |
| `checksum mismatch` | TLC revised the file. Review the change, then update `expected_sha256` deliberately |
| ClickHouse init errors after editing users | Init scripts run only on an empty volume: `make reset` (deletes all data) |
| Dashboard shows "No published data yet" | Queue a source on Data sources (and make sure a worker is running) |
| Jobs stay "Queued" | No worker is running: `make worker` (host) or `make app` (container) |
| A job failed with "worker … stopped responding" | The worker died mid-run; retry the job |
| `failed to bind host port … 8000` | Another API (e.g. `make api` with reload) is running; stop it before `make app` |

## Documentation

- [Implementation plan and phase checklist](docs/implementation-plan.md)
- [Architecture](docs/architecture.md)
- [API reference](docs/api.md) (interactive OpenAPI at `/api/docs` outside production)
- [Data dictionary](docs/data-dictionary.md)
- [Metric definitions](docs/metric-definitions.md)
- [Security](docs/security.md)
