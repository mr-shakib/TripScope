-- Daily count of curated trips carrying each quality flag (quarantined rows are reported per run in PostgreSQL).
CREATE TABLE IF NOT EXISTS {database}.data_quality_daily
(
    taxi_type LowCardinality(String),
    pickup_date Date,
    flag LowCardinality(String),
    flagged_trips UInt64,
    run_id LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY (taxi_type, toYYYYMM(pickup_date))
ORDER BY (taxi_type, pickup_date, flag)
