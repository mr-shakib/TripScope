-- Daily pre-aggregate by drop-off zone (top drop-off zones, zone comparisons).
CREATE TABLE IF NOT EXISTS {database}.trips_dropoff_daily_agg
(
    taxi_type LowCardinality(String),
    pickup_date Date,
    dropoff_location_id Nullable(Int32),
    payment_type Nullable(Int32),
    vendor_id Nullable(Int32),
    trips UInt64,
    amount_valid_trips UInt64,
    total_amount_valid_sum Decimal(38, 2),
    distance_valid_trips UInt64,
    trip_distance_valid_sum Float64,
    run_id LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY (taxi_type, toYYYYMM(pickup_date))
ORDER BY (taxi_type, pickup_date)
