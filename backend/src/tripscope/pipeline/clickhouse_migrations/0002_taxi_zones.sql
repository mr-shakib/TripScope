-- TLC Taxi Zone lookup. IDs 264/265 exist in the lookup but are not geographic zones (is_geographic = false).
CREATE TABLE IF NOT EXISTS {database}.taxi_zones
(
    location_id UInt16,
    borough LowCardinality(String),
    zone String,
    service_zone LowCardinality(String),
    is_geographic Bool,
    source_sha256 String,
    loaded_at DateTime('UTC')
)
ENGINE = MergeTree
ORDER BY location_id
