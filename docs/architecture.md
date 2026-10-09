# Architecture

Decisions and their reasons are recorded in [implementation-plan.md §2](implementation-plan.md#2-architecture-decisions).
This page describes the components and the data flow as built.

```text
                 manifests/sources.yaml (URL, period, pinned sha256)
                                │
┌──────────── pipeline (tripscope-pipeline, Spark local[*]) ─────────────┐
│ acquire ─► inspect ─► store_raw ─► zones ─► spark_transform ─►        │
│ store_curated ─► load_clickhouse (stage ▸ verify ▸ REPLACE PARTITION) ─► publish
└────────────────────────────────────────────────────────────────────────┘
      │ S3 API (app identity: read/write)        │ writer user          │ metadata
      ▼                                          ▼                      ▼
 SeaweedFS lake ◄── s3() named collection ── ClickHouse          PostgreSQL
 raw/ curated/ quarantine/ reference/      (read-only lake id)   jobs, runs, quality,
                                          taxi_trips, zones      periods, users, audit
                                                 ▲                      ▲
                                    reader user  │ (read-only,          │
                                    30 s, 100k rows, 2 GB)              │
                                                 │                      │
                           FastAPI  ─ analytics service (allowlisted query builder)
                              ▲  cookie session (HttpOnly JWT), roles from DB
                              │
                    nginx (prod) / Vite proxy (dev)  ─►  React dashboard
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
| Runner | Orchestrates stages, records timings and errors, publishes the period, holds a per-source lock | `pipeline/runner.py` |
| Metadata | Users, datasets, sources, jobs, runs, quality metrics, published periods, audit events | `metadata/`, `migrations/` |
| Analytics | Shared filter model, metric registry, parameterised query builder, result metadata | `analytics/` |
| API | Auth, datasets, analytics, job history, health/readiness; request IDs and consistent errors | `api/` |
| Dashboard | Sign-in, KPI tiles, trips-over-time chart and table, filters | `frontend/src/` |

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
3. `ALTER TABLE taxi_trips REPLACE PARTITION ('yellow', 202501) FROM stage` swaps the month atomically.
   Re-running replaces the month; it never appends to it.
4. `dataset_periods` then points the month at the new `run_id`. The API only queries published months.

## Deployment

`compose.yaml` runs PostgreSQL 16, ClickHouse 25.8 and SeaweedFS 4.48 by default. Profile `app` adds
`db-migrate`, `api` and `web` (nginx on :8080). Profile `pipeline` adds the Spark runner image
(OpenJDK 21). All ports bind to `127.0.0.1`.

MinIO was the spec's suggested store, but its images can no longer be pulled from Docker Hub or quay.io
(checked 2026-10-09). SeaweedFS provides the same S3 API, and no code depends on the vendor.
