-- Hourly pre-aggregate for dashboard KPIs, time series, hour/weekday and pickup-zone views.
-- Averages are recomputed as sum / count, so results equal queries over taxi_trips (tested).
CREATE TABLE IF NOT EXISTS {database}.trips_hourly_agg
(
    taxi_type LowCardinality(String),
    pickup_date Date,
    pickup_hour UInt8,
    pickup_day_of_week UInt8,
    pickup_location_id Nullable(Int32),
    payment_type Nullable(Int32),
    vendor_id Nullable(Int32),
    trips UInt64,
    amount_valid_trips UInt64,
    total_amount_valid_sum Decimal(38, 2),
    distance_valid_trips UInt64,
    trip_distance_valid_sum Float64,
    duration_valid_trips UInt64,
    trip_duration_valid_sum Float64,
    run_id LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY (taxi_type, toYYYYMM(pickup_date))
ORDER BY (taxi_type, pickup_date, pickup_hour)
