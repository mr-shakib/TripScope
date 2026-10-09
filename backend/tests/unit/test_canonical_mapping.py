from __future__ import annotations

import pytest

from tripscope.core.errors import SourceValidationError
from tripscope.pipeline.canonical import CANONICAL_NAMES, map_source_schema, schema_fingerprint

# Exact column/type list observed in the real yellow_tripdata_2025-01.parquet.
REAL_2025_01 = {
    "VendorID": "int32",
    "tpep_pickup_datetime": "timestamp[us]",
    "tpep_dropoff_datetime": "timestamp[us]",
    "passenger_count": "int64",
    "trip_distance": "double",
    "RatecodeID": "int64",
    "store_and_fwd_flag": "large_string",
    "PULocationID": "int32",
    "DOLocationID": "int32",
    "payment_type": "int64",
    "fare_amount": "double",
    "extra": "double",
    "mta_tax": "double",
    "tip_amount": "double",
    "tolls_amount": "double",
    "improvement_surcharge": "double",
    "total_amount": "double",
    "congestion_surcharge": "double",
    "Airport_fee": "double",
    "cbd_congestion_fee": "double",
}


def test_real_2025_schema_maps_every_canonical_field() -> None:
    mapping = map_source_schema("yellow", REAL_2025_01)
    assert set(mapping.mapping) == set(CANONICAL_NAMES)
    assert mapping.unavailable == ()
    assert mapping.unexpected == ()
    assert mapping.mapping["airport_fee"] == "Airport_fee"
    assert mapping.mapping["pickup_location_id"] == "PULocationID"


def test_older_schema_without_cbd_fee_marks_it_unavailable() -> None:
    columns = {
        k if k != "Airport_fee" else "airport_fee": v
        for k, v in REAL_2025_01.items()
        if k != "cbd_congestion_fee"
    }
    mapping = map_source_schema("yellow", columns)
    assert mapping.unavailable == ("cbd_congestion_fee",)
    assert mapping.mapping["airport_fee"] == "airport_fee"


def test_unknown_columns_are_recorded_not_mapped() -> None:
    mapping = map_source_schema("yellow", {**REAL_2025_01, "mystery_column": "double"})
    assert mapping.unexpected == ("mystery_column",)


def test_missing_required_timestamp_fails() -> None:
    columns = {k: v for k, v in REAL_2025_01.items() if k != "tpep_pickup_datetime"}
    with pytest.raises(SourceValidationError, match="pickup_datetime"):
        map_source_schema("yellow", columns)


def test_columns_differing_only_by_case_are_rejected() -> None:
    with pytest.raises(SourceValidationError, match="ambiguous"):
        map_source_schema("yellow", {**REAL_2025_01, "airport_fee": "double"})


def test_fingerprint_is_stable_and_type_sensitive() -> None:
    same = schema_fingerprint("yellow", dict(reversed(list(REAL_2025_01.items()))))
    assert schema_fingerprint("yellow", REAL_2025_01) == same
    changed = schema_fingerprint("yellow", {**REAL_2025_01, "passenger_count": "double"})
    assert changed != same
    assert same.startswith("yellow-")
