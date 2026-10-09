-- Distance and total-amount histograms per day. Buckets: 1 mile (0-50, then 50+) and $5 ($0-200, then 200+).
-- Only rows whose value is valid are bucketed; flagged rows are counted separately by data_quality_daily.
CREATE TABLE IF NOT EXISTS {database}.fare_distance_buckets
(
    taxi_type LowCardinality(String),
    pickup_date Date,
    metric LowCardinality(String),
    bucket_start Float64,
    trips UInt64,
    run_id LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY (taxi_type, toYYYYMM(pickup_date))
ORDER BY (taxi_type, metric, pickup_date, bucket_start)
