-- Curated trip fact table. One partition per (taxi_type, source month); loads replace whole partitions.
-- pickup/dropoff datetimes hold NYC local wall-clock time exactly as recorded by TLC (no tz conversion);
-- the 'UTC' type annotation only stops ClickHouse from shifting values (see ADR-06).
CREATE TABLE IF NOT EXISTS {database}.taxi_trips
(
    taxi_type LowCardinality(String),
    vendor_id Nullable(Int32),
    pickup_datetime DateTime64(6, 'UTC') COMMENT 'NYC local wall-clock time, not converted',
    dropoff_datetime DateTime64(6, 'UTC') COMMENT 'NYC local wall-clock time, not converted',
    passenger_count Nullable(Int32),
    trip_distance Nullable(Float64) COMMENT 'miles, taximeter',
    pickup_location_id Nullable(Int32),
    dropoff_location_id Nullable(Int32),
    rate_code_id Nullable(Int32),
    store_and_fwd_flag LowCardinality(Nullable(String)),
    payment_type Nullable(Int32),
    fare_amount Nullable(Decimal(14, 2)),
    extra Nullable(Decimal(14, 2)),
    mta_tax Nullable(Decimal(14, 2)),
    tip_amount Nullable(Decimal(14, 2)) COMMENT 'card tips only',
    tolls_amount Nullable(Decimal(14, 2)),
    improvement_surcharge Nullable(Decimal(14, 2)),
    total_amount Nullable(Decimal(14, 2)) COMMENT 'excludes cash tips',
    congestion_surcharge Nullable(Decimal(14, 2)),
    airport_fee Nullable(Decimal(14, 2)),
    cbd_congestion_fee Nullable(Decimal(14, 2)),
    pickup_date Date,
    pickup_hour UInt8,
    pickup_day_of_week UInt8 COMMENT 'ISO: 1=Monday … 7=Sunday',
    trip_duration_minutes Float64,
    average_speed_mph Nullable(Float64),
    fare_per_mile Nullable(Float64),
    is_distance_valid Bool,
    is_duration_valid Bool,
    is_amount_valid Bool,
    quality_flags Array(LowCardinality(String)),
    source_file LowCardinality(String),
    run_id LowCardinality(String),
    ingested_at DateTime64(3, 'UTC'),
    data_period Date
)
ENGINE = MergeTree
PARTITION BY (taxi_type, toYYYYMM(pickup_date))
ORDER BY (taxi_type, pickup_date, pickup_hour)
