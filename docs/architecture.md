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
| Worker | Claims jobs, heartbeats, cancels in-flight Spark jobs on every beat while a cancel is pending, fails jobs of dead workers | `pipeline/worker.py` |
| Zone geometry | Reads the TLC taxi-zone shapefile, reprojects NY State Plane (US feet) to WGS84, simplifies and stores GeoJSON in the lake | `pipeline/zone_geometry.py` |
| Pre-aggregates | Built from the verified staging rows and swapped with the fact table per month | `pipeline/aggregates.py` |
| Metadata | Users, datasets, sources, jobs, runs, quality metrics, published periods, audit events | `metadata/`, `migrations/` |
| Analytics | Shared filter model, metric registry, query builder that routes each request to the cheapest table able to answer it, period comparison, distributions, explorer rows and CSV extracts, schema and quality reports | `analytics/` |
| API | Auth, datasets, schema, quality, analytics, explorer and exports, jobs, data sources, health/readiness | `api/` |
| Web | Next.js App Router; Overview, Dashboards, Explore data, Data sources, Processing jobs, Data quality. Filters and the active tab live in the URL | `frontend/src/` |

## Lake layout

```text
tripscope-lake/
  raw/taxi_type=yellow/year=2025/month=01/yellow_tripdata_2025-01.parquet        (write-once, sha256 metadata)
  curated/taxi_type=yellow/year=2025/month=01/run_id=<uuid>/part-*.parquet       (zstd, ~1M rows per file)
  quarantine/run_id=<uuid>/part-*.parquet                                        (rows + reasons)
  reference/taxi_zones/sha256=<hash>/taxi_zone_lookup.csv
  reference/taxi_zone_shapes/sha256=<hash>/taxi_zones.zip                         (TLC shapefile, pinned checksum)
  reference/taxi_zone_geometry/current.geojson                                   (built from it: 263 zones, WGS84)
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

Each table the API can read declares, in `analytics/query_builder.py`, the filters it can apply, the
dimensions it can group by and the metrics it can compute. A request goes to the first table, cheapest first,
that covers all three:

| Table | Filters | Group by | Metrics |
|---|---|---|---|
| `trips_hourly_agg` (2.2M rows) | dates, pickup zone, payment type, vendor, hour, weekday | time, hour, weekday, pickup zone, payment type, vendor | all |
| `trips_dropoff_daily_agg` | dates, drop-off zone, payment type, vendor | time, drop-off zone, payment type, vendor | all except average duration |
| `taxi_trips` (24.1M rows) | everything | everything, including zone pairs | all |

So the heatmap, payment types, vendors and pickup-zone totals read the hourly aggregate, drop-off totals read
the drop-off aggregate, and a distance filter or a zone pair falls back to the fact table. Distributions read
`fare_distance_buckets` unless a filter it lacks is active. Averages are recomputed as sum ÷ count and "no valid
rows" stays NULL, so every path returns the same numbers. Integration tests assert this across filter
combinations; they caught two real discrepancies during development (decimal-scale truncation and 0 vs. no data).

## Zone map

The TLC publishes zone boundaries as a shapefile in NY State Plane Long Island (EPSG:2263, US survey feet). The
pipeline downloads it like any source (pinned checksum, size cap), reads only the expected members, reprojects
with the file's own projection definition, merges multi-part zones, simplifies to about 10 m and checks every
zone lies inside New York City. The result (317 KB, 80 KB gzipped) is stored in the lake; the API serves it
from memory after the first request, and the map joins it to `zone-totals` by location ID. Zones 264/265
("Unknown", "Outside of NYC") have no geometry and are listed separately.

## Jobs

The API inserts a `queued` job; a worker claims it atomically (`FOR UPDATE SKIP LOCKED`), heartbeats every
5 s and runs it. Cancel marks queued jobs cancelled immediately. For running jobs the worker sets the run's
cancel event and cancels Spark jobs on every heartbeat until the run stops; the transform checks before each
Spark action and the runner between stages and right before the partition swap, so a cancelled run never
publishes. Jobs whose worker stops heartbeating for 2 minutes are failed by
any live worker and can be retried.

## Deployment

`compose.yaml` runs PostgreSQL 16, ClickHouse 25.8 and SeaweedFS 4.48 by default. Profile `app` adds
`db-migrate`, `api`, `worker` (Spark image, OpenJDK 21) and `web` (Next.js standalone server on :8080, which
rewrites `/api` to the API container). Profile `pipeline` adds a one-off Spark runner. All ports bind to
`127.0.0.1`.

MinIO was the spec's suggested store, but its images can no longer be pulled from Docker Hub or quay.io
(checked 2026-10-09). SeaweedFS provides the same S3 API, and no code depends on the vendor.
