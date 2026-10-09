"""Hand-built TLC fixture with the exact Parquet schema of the real `yellow_tripdata_2025-01.parquet`.

Every row states the outcome the pipeline must produce, so expectations are derived independently of the code
under test. This is test data only; it never enters a product flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

# Physical schema copied from the real 2025-01 file (note the capitalised `Airport_fee`).
TLC_2025_SCHEMA = pa.schema(
    [
        ("VendorID", pa.int32()),
        ("tpep_pickup_datetime", pa.timestamp("us")),
        ("tpep_dropoff_datetime", pa.timestamp("us")),
        ("passenger_count", pa.int64()),
        ("trip_distance", pa.float64()),
        ("RatecodeID", pa.int64()),
        ("store_and_fwd_flag", pa.large_string()),
        ("PULocationID", pa.int32()),
        ("DOLocationID", pa.int32()),
        ("payment_type", pa.int64()),
        ("fare_amount", pa.float64()),
        ("extra", pa.float64()),
        ("mta_tax", pa.float64()),
        ("tip_amount", pa.float64()),
        ("tolls_amount", pa.float64()),
        ("improvement_surcharge", pa.float64()),
        ("total_amount", pa.float64()),
        ("congestion_surcharge", pa.float64()),
        ("Airport_fee", pa.float64()),
        ("cbd_congestion_fee", pa.float64()),
    ]
)

PERIOD_START = date(2025, 1, 1)
ZONE_IDS = [*range(1, 264)]  # 1..263 are geographic zones; 264/265 are "Unknown"/"Outside of NYC"


@dataclass
class FixtureRow:
    label: str
    pickup: datetime | None
    dropoff: datetime | None
    distance: float
    total: float
    fare: float
    pu: int = 161
    do: int = 236
    passengers: int | None = 1
    payment: int = 1
    vendor: int = 2
    flex_nulls: bool = False
    reasons: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)

    @property
    def accepted(self) -> bool:
        return not self.reasons

    def as_source(self) -> dict[str, Any]:
        return {
            "VendorID": self.vendor,
            "tpep_pickup_datetime": self.pickup,
            "tpep_dropoff_datetime": self.dropoff,
            "passenger_count": None if self.flex_nulls else self.passengers,
            "trip_distance": self.distance,
            "RatecodeID": None if self.flex_nulls else 1,
            "store_and_fwd_flag": None if self.flex_nulls else "N",
            "PULocationID": self.pu,
            "DOLocationID": self.do,
            "payment_type": self.payment,
            "fare_amount": self.fare,
            "extra": 1.0,
            "mta_tax": 0.5,
            "tip_amount": 0.0,
            "tolls_amount": 0.0,
            "improvement_surcharge": 1.0,
            "total_amount": self.total,
            "congestion_surcharge": None if self.flex_nulls else 2.5,
            "Airport_fee": None if self.flex_nulls else 0.0,
            "cbd_congestion_fee": 0.75,
        }

    @property
    def duration_minutes(self) -> float | None:
        if self.pickup is None or self.dropoff is None:
            return None
        return (self.dropoff - self.pickup).total_seconds() / 60.0


def dt(day: int, hour: int, minute: int = 0, second: int = 0, month: int = 1, year: int = 2025) -> datetime:
    return datetime(year, month, day, hour, minute, second)


def fixture_rows() -> list[FixtureRow]:
    rows = [
        FixtureRow("normal-mon-8", dt(6, 8), dt(6, 8, 20), 5.0, 30.0, 22.0),
        FixtureRow(
            "normal-mon-9-cash",
            dt(6, 9),
            dt(6, 9, 30),
            3.0,
            70.0,
            60.0,
            pu=132,
            do=161,
            passengers=2,
            payment=2,
        ),
        FixtureRow("normal-tue-23-cross-midnight", dt(7, 23, 30), dt(8, 0, 10), 10.0, 50.0, 40.0),
        FixtureRow("zero-distance", dt(7, 10), dt(7, 10, 5), 0.0, 5.0, 3.0, flags={"invalid_distance"}),
        FixtureRow(
            "negative-amount",
            dt(7, 11),
            dt(7, 11, 10),
            1.0,
            -12.5,
            -10.0,
            payment=4,
            flags={"invalid_amount"},
        ),
        FixtureRow(
            "dropoff-before-pickup",
            dt(8, 12),
            dt(8, 11, 50),
            2.0,
            15.0,
            10.0,
            reasons={"dropoff_before_pickup"},
        ),
        FixtureRow(
            "pickup-previous-month",
            dt(31, 23, 50, month=12, year=2024),
            dt(1, 0, 5),
            2.0,
            15.0,
            10.0,
            reasons={"pickup_outside_source_period"},
        ),
        FixtureRow("zero-duration", dt(9, 14), dt(9, 14), 2.0, 10.0, 7.0, flags={"invalid_duration"}),
        FixtureRow(
            "implausible-speed", dt(9, 15), dt(9, 15, 1), 10.0, 40.0, 30.0, flags={"implausible_speed"}
        ),
        FixtureRow(
            "unmapped-zones",
            dt(10, 16),
            dt(10, 16, 15),
            4.0,
            25.0,
            18.0,
            pu=264,
            do=265,
            flags={"pickup_zone_unmapped", "dropoff_zone_unmapped"},
        ),
        FixtureRow(
            "flex-fare-nulls",
            dt(11, 17),
            dt(11, 17, 12),
            2.5,
            20.0,
            14.0,
            payment=0,
            flex_nulls=True,
            flags={"passenger_count_missing_or_zero"},
        ),
        FixtureRow(
            "extreme-total", dt(12, 18), dt(12, 18, 30), 8.0, 5000.0, 4990.0, flags={"invalid_amount"}
        ),
        FixtureRow(
            "extreme-distance", dt(12, 19), dt(12, 23), 300.0, 400.0, 380.0, flags={"invalid_distance"}
        ),
        FixtureRow("missing-pickup", None, dt(13, 9), 1.0, 9.0, 6.0, reasons={"missing_required_timestamp"}),
        FixtureRow(
            "zero-passengers",
            dt(13, 10),
            dt(13, 10, 10),
            1.5,
            12.0,
            8.0,
            passengers=0,
            flags={"passenger_count_missing_or_zero"},
        ),
        FixtureRow(
            "very-long-duration", dt(14, 1), dt(14, 15, 0), 20.0, 90.0, 80.0, flags={"invalid_duration"}
        ),
        FixtureRow("last-second-of-month", dt(31, 23, 59, 59), dt(1, 0, 10, month=2), 2.0, 14.0, 10.0),
        FixtureRow(
            "first-second-of-next-month",
            dt(1, 0, 0, month=2),
            dt(1, 0, 9, month=2),
            2.0,
            14.0,
            10.0,
            reasons={"pickup_outside_source_period"},
        ),
        FixtureRow("normal-sun-12", dt(12, 12), dt(12, 12, 45), 6.0, 35.0, 28.0, vendor=1),
    ]
    # An exact duplicate of the first row: the copy (later in read order) is quarantined.
    first = rows[0]
    rows.append(
        FixtureRow(
            "duplicate-of-normal-mon-8",
            first.pickup,
            first.dropoff,
            first.distance,
            first.total,
            first.fare,
            reasons={"duplicate_record"},
        )
    )
    return rows


def write_fixture_parquet(path: Path, rows: list[FixtureRow] | None = None) -> list[FixtureRow]:
    rows = rows if rows is not None else fixture_rows()
    table = pa.Table.from_pylist([r.as_source() for r in rows], schema=TLC_2025_SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return rows


def expected_overview(rows: list[FixtureRow]) -> dict[str, Any]:
    """Independent computation of the Phase 1 KPIs from the fixture definitions."""
    accepted = [r for r in rows if r.accepted]
    amount_ok = [r for r in accepted if "invalid_amount" not in r.flags]
    distance_ok = [r for r in accepted if "invalid_distance" not in r.flags]
    duration_ok = [r for r in accepted if "invalid_duration" not in r.flags]
    total_amount = sum(Decimal(str(r.total)) for r in amount_ok)
    return {
        "total_trips": len(accepted),
        "total_recorded_amount": float(total_amount),
        "avg_total_amount": float(total_amount) / len(amount_ok),
        "avg_trip_distance": sum(r.distance for r in distance_ok) / len(distance_ok),
        "avg_trip_duration_minutes": sum(r.duration_minutes or 0 for r in duration_ok) / len(duration_ok),
        "excluded_amount_rows": len(accepted) - len(amount_ok),
        "excluded_distance_rows": len(accepted) - len(distance_ok),
        "excluded_duration_rows": len(accepted) - len(duration_ok),
    }
