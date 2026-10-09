/** Human descriptions of pipeline quality rules (mirrors backend/src/tripscope/pipeline/rules.py). */
export const QUARANTINE_REASONS: Record<string, { label: string; description: string }> = {
  missing_required_timestamp: { label: "Missing or unreadable timestamp", description: "Pickup or drop-off time absent or unparseable." },
  dropoff_before_pickup: { label: "Drop-off before pickup", description: "The meter stopped before it started; duration cannot be computed." },
  pickup_outside_source_period: { label: "Outside the file’s month", description: "Stray records from other months in a monthly file." },
  duplicate_record: { label: "Exact duplicate", description: "Identical to an earlier record in every field; the first copy is kept." },
};

export const FLAGS: Record<string, { label: string; description: string }> = {
  invalid_distance: { label: "Implausible distance", description: "Distance ≤ 0 or above 200 miles. Excluded from distance metrics." },
  invalid_duration: { label: "Implausible duration", description: "Duration ≤ 0 or above 12 hours. Excluded from duration metrics." },
  invalid_amount: { label: "Implausible amount", description: "Negative fare/total (refunds, disputes) or total above $1,000. Excluded from amount metrics." },
  implausible_speed: { label: "Implausible speed", description: "Valid distance and duration but faster than 80 mph; speed left blank." },
  passenger_count_missing_or_zero: { label: "No passenger count", description: "Blank or zero passengers — mostly Flex Fare trips, where TLC leaves it empty." },
  pickup_zone_unmapped: { label: "Pickup zone unknown", description: "Zone 264/265 or an ID not in the TLC lookup." },
  dropoff_zone_unmapped: { label: "Drop-off zone unknown", description: "Zone 264/265 or an ID not in the TLC lookup." },
};

export const STAGES: { id: string; label: string }[] = [
  { id: "acquire", label: "Acquire & verify checksum" },
  { id: "inspect", label: "Inspect schema" },
  { id: "store_raw", label: "Store raw file" },
  { id: "zones", label: "Load zone lookup" },
  { id: "spark_transform", label: "Spark transform" },
  { id: "store_curated", label: "Write curated Parquet" },
  { id: "load_clickhouse", label: "Load & verify ClickHouse" },
  { id: "publish", label: "Publish period" },
];
