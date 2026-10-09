# Metric definitions

The single source of truth is [`backend/src/tripscope/analytics/metrics.py`](../backend/src/tripscope/analytics/metrics.py).
Every API response includes these definitions with its numbers. Later phases will reuse this registry for
exports, reports and AI tools, so a metric means the same thing everywhere.

## Row populations

| Population | Meaning |
|---|---|
| **Accepted (curated) trips** | Source rows that passed every quarantine rule (see below). Only these reach ClickHouse. |
| **Flagged trips** | Accepted trips with a suspect value. They stay in the data, and only the metrics that depend on the flagged value exclude them. Every KPI response returns how many rows its flag excluded. |
| **Quarantined rows** | Removed from curated data, preserved in `quarantine/run_id=…/` in the lake with their reasons. |
| **Published periods** | Months whose load passed verification. The API never queries anything else. |

## Phase 1 metrics

| ID | Label | Formula (ClickHouse) | Unit | Rows included |
|---|---|---|---|---|
| `total_trips` | Total trips | `count()` | trips | all accepted trips |
| `total_recorded_amount` | Total recorded amount | `sumIf(total_amount, is_amount_valid)` | USD | `0 ≤ total_amount ≤ 1000` and `fare_amount ≥ 0` |
| `avg_total_amount` | Average total amount per trip | `avgIf(total_amount, is_amount_valid)` | USD | as above |
| `avg_trip_distance` | Average trip distance | `avgIf(trip_distance, is_distance_valid)` | miles | `0 < trip_distance ≤ 200` |
| `avg_trip_duration_minutes` | Average trip duration | `avgIf(trip_duration_minutes, is_duration_valid)` | minutes | `0 < duration ≤ 720` |

Time series and breakdowns group the same expressions by pickup date, hour (range ≤ 62 days), month, hour of
day, ISO weekday or pickup zone.

When a query is answered from `trips_hourly_agg`, each metric is recomputed from stored counts and sums of valid
values — e.g. average distance = Σ valid distance ÷ number of trips with a valid distance — and a total over zero
valid rows is NULL, never 0. Responses name the table that answered (`meta.source_table`).

## Caveats that apply to every result

- **Cash tips are not recorded.** TLC's `tip_amount` covers card tips only, and `total_amount` excludes
  cash tips. Amounts understate what cash-paying passengers paid.
- **Negative amounts are excluded from amount metrics.** These are refunds, disputes and voids, typically
  `payment_type` 4/6. They remain in the data and are counted in `excluded_rows`.
- **Thresholds are documented starting values, not facts.** 200 miles, 720 minutes, $1,000 and 80 mph are
  configurable. Each run stores the exact values it used in `processing_runs.quality_rules`.
- **Local time.** Dates and hours are NYC local wall-clock time as recorded by TLC. No timezone conversion is
  applied, and hours around daylight-saving transitions are not disambiguated.
- **No data is not zero.** An average over zero valid rows, or a query outside published coverage, returns
  `null` / `data_state: "empty"`, and the dashboard shows "—".
- **Descriptive only.** These metrics describe recorded trips. They do not establish causes, and an unusual
  trip is not evidence of misconduct.

## Quality rules behind the populations

| Rule | Outcome | Default |
|---|---|---|
| Pickup or drop-off timestamp missing | quarantine `missing_required_timestamp` | — |
| Drop-off earlier than pickup | quarantine `dropoff_before_pickup` | — |
| Pickup outside the file's month | quarantine `pickup_outside_source_period` | — |
| Exact duplicate of all canonical fields (first kept) | quarantine `duplicate_record` | — |
| Distance ≤ 0 or > max | flag `invalid_distance` | 200 mi |
| Duration ≤ 0 or > max | flag `invalid_duration` | 720 min |
| Total < 0, fare < 0, or total > max | flag `invalid_amount` | $1,000 |
| Speed > max (when distance and duration are valid; speed then left NULL) | flag `implausible_speed` | 80 mph |
| Passenger count NULL or 0 | flag `passenger_count_missing_or_zero` | — |
| Zone ID not in the lookup, or 264/265 | flag `pickup_zone_unmapped` / `dropoff_zone_unmapped` | — |

### Observed on 2025-01 → 2025-06 (published runs)

24,083,384 rows read; 24,082,454 accepted; 930 quarantined (793 drop-off before pickup, 137 outside the file's
month, 0 duplicates). Among accepted trips:

| Flag | Trips | Share |
|---|---|---|
| `passenger_count_missing_or_zero` | 5,556,829 | 23.07% (mostly Flex Fare, `payment_type = 0`, where TLC leaves the field NULL) |
| `invalid_amount` | 1,325,538 | 5.50% |
| `invalid_distance` | 663,092 | 2.75% |
| `invalid_duration` | 203,010 | 0.84% |
| `dropoff_zone_unmapped` | 153,948 | 0.64% |
| `pickup_zone_unmapped` | 54,664 | 0.23% |
| `implausible_speed` | 5,570 | 0.02% |
