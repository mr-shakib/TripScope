# TripScope

A Big Data analytics platform for NYC Taxi & Limousine Commission (TLC) trip records:
**PySpark** validates and transforms monthly TLC Parquet files, curated data lands in an **S3 data lake**
and **ClickHouse**, a **FastAPI** service serves bounded, parameterised analytics, and a **React** dashboard
renders it. Later phases add report exports and an AI analyst grounded in approved analytics tools
(see [`docs/implementation-plan.md`](docs/implementation-plan.md)).

> **Status: Phase 1 vertical slice complete.** One real TLC file flows end to end:
> source → validation → Spark → Parquet lake → ClickHouse → API → dashboard chart.
> Specification: [`PROJECT_SPEC.md`](PROJECT_SPEC.md).

## What works today

| Layer | Implemented |
|---|---|
| Ingestion | Source manifest with pinned SHA-256, HTTPS host allowlist, size cap, Parquet structure checks, immutable raw upload |
| Spark | Per-file canonical schema mapping, quarantine (4 reasons), quality flags (7), derived fields, run metrics |
| Storage | Raw / curated / quarantine Parquet in the lake (SeaweedFS, S3 API); ClickHouse fact table loaded from the lake with verified, idempotent partition replacement |
| Metadata | PostgreSQL: users, datasets, data sources, jobs, processing runs, quality metrics, published periods, audit log |
| API | Sign in/out, datasets, `/analytics/overview`, `/analytics/trips-over-time`, ingestion job history, `/health`, `/ready` |
| Dashboard | Sign-in, KPI tiles with definitions, trips-over-time chart (daily/hourly) with table view, date filters, light/dark |

Measured on `yellow_tripdata_2025-01.parquet` (3,475,226 rows):

| Stage | Result |
|---|---|
| Accepted / quarantined | 3,475,080 / 146 (124 drop-off before pickup, 22 outside the source month) |
| End-to-end pipeline | 33.4 s on the host, of which Spark 30.4 s; 44.8 s in the container including a 7.8 s download (AMD Ryzen 5 5500, 12 threads, 14 GiB RAM) |
| Storage | raw 59.2 MB → curated Parquet 87.4 MB (more columns, zstd) → ClickHouse 185 MiB compressed / 664 MiB uncompressed |
| Dashboard queries | overview ≈ 10–45 ms, daily series ≈ 10–35 ms (single run, warm, local) |

These are single-run local measurements, not a benchmark. The reproducible benchmark comes in Phase 6.

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
docker compose --profile pipeline run --rm db-migrate          # PostgreSQL schema
make pipeline-docker SOURCE=yellow-2025-01                     # download, validate, Spark, load, publish
docker compose --profile app run --rm --entrypoint tripscope-admin api \
  create-user --email you@example.org --name "You" --role analyst   # prompts for a password (12+ chars)
make app                      # API + web on http://127.0.0.1:8080
```

## Host development

```bash
make env && make up
cd backend && uv sync --extra pipeline && cd ..
cd frontend && npm ci && cd ..
make migrate                  # Alembic + ClickHouse migrations
make pipeline                 # SOURCE=yellow-2025-01 by default
make user EMAIL=you@example.org NAME="You" ROLE=analyst
make api                      # terminal 1: http://127.0.0.1:8000 (OpenAPI at /api/docs)
make web                      # terminal 2: http://127.0.0.1:5173
```

Roles: `admin`, `analyst`, `viewer`. All three can read published analytics. Only admins and analysts
can list ingestion jobs.

## Dataset acquisition

Sources are configuration, not code: [`manifests/sources.yaml`](manifests/sources.yaml) lists each monthly
file with its official TLC URL and the SHA-256 we validated. To add a month, copy the URL from the
[TLC trip record page](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page), add an entry
(the pin is optional but recommended), and run `make pipeline SOURCE=<key>`.
Re-running a source replaces its month in ClickHouse; it never duplicates it.

**Attribution:** data is from the NYC Taxi & Limousine Commission (TLC) Trip Record Data,
https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page. Source files are not redistributed in this
repository. TLC records are operational trip records: they can contain anomalies and revisions, and an unusual
record is not evidence of fraud.

## Tests

```bash
make lint               # ruff, ruff format --check, mypy --strict, tsc, eslint
make test-unit          # 63 backend unit tests incl. local Spark transform tests
make test-integration   # 10 tests against the running services (isolated tripscope_test DBs and bucket)
make test-frontend      # type check, lint, 10 Vitest tests
E2E_EMAIL=… E2E_PASSWORD=… make e2e                       # Playwright against the dev server
E2E_BASE_URL=http://127.0.0.1:8080 E2E_EMAIL=… E2E_PASSWORD=… make e2e   # against the container build
```

## Repository layout

```text
backend/          Python package `tripscope` (core, storage, metadata, pipeline, analytics, api) + tests + Alembic
frontend/         React + TypeScript dashboard (Vite, Tailwind, ECharts, TanStack Query) + Vitest + Playwright
infrastructure/   ClickHouse config/users, PostgreSQL init, SeaweedFS identities, Dockerfiles, nginx
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
| Dashboard shows "No published data yet" | Run the pipeline for at least one source |

## Documentation

- [Implementation plan and phase checklist](docs/implementation-plan.md)
- [Architecture](docs/architecture.md)
- [Data dictionary](docs/data-dictionary.md)
- [Metric definitions](docs/metric-definitions.md)
- [Security](docs/security.md)
