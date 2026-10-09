# Architecture

Decisions and their reasons are recorded in [implementation-plan.md §2](implementation-plan.md#2-architecture-decisions).
This page describes the components and the data flow as built.

```text
                       manifests/sources.yaml (URL, period, format, pinned sha256)
                                      │
 Next.js web ──/api rewrites──► FastAPI ──POST /ingestion-jobs──► PostgreSQL queue (queued → running → done)
 (proxy.ts: CSP nonce,             │                                        │ SELECT … FOR UPDATE SKIP LOCKED
  sign-in redirect)                │ reader user (read-only, bounded)       ▼
                                   │                              worker (Spark local[*]) — heartbeat, cancel
                                   ▼                                        │
                     ClickHouse: taxi_trips + trips_hourly_agg,   acquire ─► inspect (Parquet / CSV) ─► store_raw ─►
                     trips_dropoff_daily_agg, fare_distance_      zones ─► spark_transform ─► store_curated ─►
                     buckets, data_quality_daily, taxi_zones      load_clickhouse (stage ▸ verify ▸ aggregates ▸
                                   ▲                              REPLACE PARTITION ×5) ─► publish
                                   │ s3() named collection (read-only lake identity)        │
                                   └──────────── SeaweedFS lake: raw/ curated/ quarantine/ reference/ ◄─┘
```

## Components

| Component | Responsibility | Code |
|---|---|---|
| Source manifest | Lists the files to ingest, their official URLs and expected checksums | `manifests/`, `pipeline/manifest.py` |
| Acquire / inspect | Download with size cap; verify checksum and Parquet structure; read the schema from the footer | `pipeline/acquire.py` |
| Canonical mapping | Per-file, case-insensitive mapping to the canonical schema; records unavailable fields and a schema fingerprint | `pipeline/canonical.py` |
| Spark transform | Normalise, deduplicate, quarantine, flag, derive, write Parquet, compute quality metrics | `pipeline/spark_transform.py` |
| Lake | Immutable raw files, run-scoped curated and quarantine outputs, reference data | `pipeline/lake.py`, `storage/object_store.py` |
| ClickHouse load | Stage from the lake, verify rows and exact decimal sums against Spark, atomically replace the month | `pipeline/clickhouse_load.py` |
| Runner | Orchestrates stages, records timings, errors and the run's own log records, checks for cancellation between stages and before publishing, publishes the period, holds a per-source lock | `pipeline/runner.py` |
| Job queue | Enqueue/retry/cancel/claim/stale detection in PostgreSQL; one active job per source enforced by a partial unique index | `jobs/service.py` |
| Worker | Claims jobs, heartbeats, cancels in-flight Spark jobs on request, fails jobs of dead workers | `pipeline/worker.py` |
| Pre-aggregates | Built from the verified staging rows and swapped with the fact table per month | `pipeline/aggregates.py` |
| Metadata | Users, datasets, sources, jobs, runs, quality metrics, published periods, audit events | `metadata/`, `migrations/` |
| Analytics | Shared filter model, metric registry, query builder that routes to the hourly aggregate when every filter is one of its dimensions, schema and quality reports | `analytics/` |
| API | Auth, datasets, schema, quality, analytics, jobs, data sources, health/readiness | `api/` |
| Web | Next.js App Router; Overview, Data sources, Processing jobs, Data quality | `frontend/src/` |

## Lake layout

```text
tripscope-lake/
  raw/taxi_type=yellow/year=2025/month=01/yellow_tripdata_2025-01.parquet        (write-once, sha256 metadata)
  curated/taxi_type=yellow/year=2025/month=01/run_id=<uuid>/part-*.parquet       (zstd, ~1M rows per file)
  quarantine/run_id=<uuid>/part-*.parquet                                        (rows + reasons)
  reference/taxi_zones/sha256=<hash>/taxi_zone_lookup.csv
```

## Idempotency and publication

1. Each run writes curated output under its own `run_id`, so runs never overwrite each other's files.
2. ClickHouse loads into `taxi_trips_stage_<run>`. The row count and `sum(total_amount)` must equal Spark's
   numbers exactly, and every row must belong to the source month and taxi type.
3. The four pre-aggregates are built from the same staging rows and checked to account for every trip and
   every valid dollar exactly.
4. A last cancellation check, then `ALTER TABLE … REPLACE PARTITION ('yellow', 202501)` swaps the month in the
   fact table and each aggregate. Re-running replaces the month; it never appends to it.
5. `dataset_periods` then points the month at the new `run_id`. The API only queries published months.

## Query routing

`trips_hourly_agg` keeps pickup date, hour, weekday, pickup zone, payment type and vendor with trip counts and
sums/counts of every valid value. When a request filters only on those dimensions (the common dashboard case)
the API reads it: 2.2M rows instead of 24.1M for six months. Drop-off zone and distance filters fall back to
`taxi_trips`. Averages are recomputed as sum ÷ count and "no valid rows" stays NULL, so both paths return
the same numbers. Integration tests assert this across filter combinations, and they caught two real
discrepancies during development (decimal-scale truncation and 0 vs. no data).

## Jobs

The API inserts a `queued` job; a worker claims it atomically (`FOR UPDATE SKIP LOCKED`), heartbeats every
5 s and runs it. Cancel marks queued jobs cancelled immediately; for running jobs the worker sets the run's
cancel event and cancels Spark jobs, and the runner checks between stages and right before the partition
swap, so a cancelled run never publishes. Jobs whose worker stops heartbeating for 2 minutes are failed by
any live worker and can be retried.

## Deployment

`compose.yaml` runs PostgreSQL 16, ClickHouse 25.8 and SeaweedFS 4.48 by default. Profile `app` adds
`db-migrate`, `api`, `worker` (Spark image, OpenJDK 21) and `web` (Next.js standalone server on :8080, which
rewrites `/api` to the API container). Profile `pipeline` adds a one-off Spark runner. All ports bind to
`127.0.0.1`.

MinIO was the spec's suggested store, but its images can no longer be pulled from Docker Hub or quay.io
(checked 2026-10-09). SeaweedFS provides the same S3 API, and no code depends on the vendor.
