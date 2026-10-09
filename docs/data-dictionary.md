# Data dictionary

Canonical schema of the curated trip table (`taxi_trips` in ClickHouse, curated Parquet in the lake).
Mapping code: [`pipeline/canonical.py`](../backend/src/tripscope/pipeline/canonical.py).
Code meanings follow the official *Data Dictionary – Yellow Taxi Trip Records* (TLC, 18 March 2025).

Source columns are matched **case-insensitively**. For example, the 2025 files use `Airport_fee` and older
files `airport_fee`. A canonical field absent from a source file is stored as NULL and listed in
`processing_runs.unavailable_fields`; it is never inferred.

## Source fields (Yellow)

| Canonical | Source column | Type | Meaning | Availability notes |
|---|---|---|---|---|
| `vendor_id` | `VendorID` | Int32 | TPEP provider: 1 Creative Mobile Technologies, 2 Curb Mobility, 6 Myle Technologies, 7 Helix | 6 and 7 appear in 2025 |
| `pickup_datetime` | `tpep_pickup_datetime` | DateTime64(6) | Meter engaged, NYC local wall-clock | required |
| `dropoff_datetime` | `tpep_dropoff_datetime` | DateTime64(6) | Meter disengaged, NYC local wall-clock | required |
| `passenger_count` | `passenger_count` | Int32 | Passengers (driver-entered) | NULL for Flex Fare rows in 2025-01 |
| `trip_distance` | `trip_distance` | Float64 | Taximeter miles | |
| `pickup_location_id` | `PULocationID` | Int32 | TLC Taxi Zone where the meter was engaged | 264/265 are not geographic |
| `dropoff_location_id` | `DOLocationID` | Int32 | TLC Taxi Zone where the meter was disengaged | 264/265 are not geographic |
| `rate_code_id` | `RatecodeID` | Int32 | 1 Standard, 2 JFK, 3 Newark, 4 Nassau/Westchester, 5 Negotiated, 6 Group ride, 99 Null/unknown | NULL for Flex Fare rows |
| `store_and_fwd_flag` | `store_and_fwd_flag` | String | Y = held in vehicle memory before sending; N = not | NULL for Flex Fare rows |
| `payment_type` | `payment_type` | Int32 | 0 Flex Fare, 1 Credit card, 2 Cash, 3 No charge, 4 Dispute, 5 Unknown, 6 Voided | |
| `fare_amount` | `fare_amount` | Decimal(14,2) | Time-and-distance fare, USD | |
| `extra` | `extra` | Decimal(14,2) | Miscellaneous extras and surcharges, USD | |
| `mta_tax` | `mta_tax` | Decimal(14,2) | MTA tax, USD | |
| `tip_amount` | `tip_amount` | Decimal(14,2) | Card tips, USD (cash tips not included) | |
| `tolls_amount` | `tolls_amount` | Decimal(14,2) | Tolls, USD | |
| `improvement_surcharge` | `improvement_surcharge` | Decimal(14,2) | Improvement surcharge, USD | |
| `total_amount` | `total_amount` | Decimal(14,2) | Total charged to passengers, excluding cash tips, USD | |
| `congestion_surcharge` | `congestion_surcharge` | Decimal(14,2) | NYS congestion surcharge, USD | NULL for Flex Fare rows |
| `airport_fee` | `Airport_fee` / `airport_fee` | Decimal(14,2) | LaGuardia/JFK pickup fee, USD | casing differs by year |
| `cbd_congestion_fee` | `cbd_congestion_fee` | Decimal(14,2) | MTA Congestion Relief Zone fee (from 2025-01-05), USD | absent before 2025 |

Monetary values are rounded to cents (`Decimal(14,2)`) so totals reconcile exactly between Spark and ClickHouse.

## Derived fields

| Field | Type | Definition |
|---|---|---|
| `pickup_date` | Date | Calendar date of `pickup_datetime` (local) |
| `pickup_hour` | UInt8 | Hour 0–23 of `pickup_datetime` (local) |
| `pickup_day_of_week` | UInt8 | ISO day: 1 = Monday … 7 = Sunday |
| `trip_duration_minutes` | Float64 | `(dropoff − pickup)` in minutes |
| `average_speed_mph` | Nullable(Float64) | `distance / (duration / 60)`; NULL unless distance and duration are valid and the speed is ≤ 80 mph |
| `fare_per_mile` | Nullable(Float64) | `fare_amount / trip_distance`; NULL unless distance and amount are valid |
| `is_distance_valid`, `is_duration_valid`, `is_amount_valid` | Bool | See [metric-definitions.md](metric-definitions.md) |
| `quality_flags` | Array(String) | Every flag raised on the row |

## Lineage fields

| Field | Meaning |
|---|---|
| `taxi_type` | `yellow` (more types in Phase 2) |
| `source_file` | Original TLC file name |
| `run_id` | Processing run that produced the row (joins `processing_runs.id` in PostgreSQL) |
| `ingested_at` | UTC start time of that run |
| `data_period` | First day of the source month |

## Reference: taxi zones

`taxi_zones` (ClickHouse) holds TLC's `taxi_zone_lookup.csv`: 265 IDs, of which 263 are geographic.
ID 264 (`Unknown`) and 265 (`Outside of NYC`) are kept but marked `is_geographic = false`.

## Pre-aggregates (ClickHouse)

Built per month from the verified staging rows of each run and swapped together with `taxi_trips`.

| Table | Grain | Measures |
|---|---|---|
| `trips_hourly_agg` | date × hour × weekday × pickup zone × payment type × vendor | trips; count and sum of valid total amount, distance and duration |
| `trips_dropoff_daily_agg` | date × drop-off zone × payment type × vendor | trips; count and sum of valid total amount and distance |
| `fare_distance_buckets` | date × metric × bucket (1 mile up to 50+, $5 up to $200+) | trips with a valid value in the bucket |
| `data_quality_daily` | date × flag | accepted trips carrying the flag |
