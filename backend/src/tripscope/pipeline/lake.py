"""Data-lake object key layout (spec §8.3). Keys are built only from validated components."""

from __future__ import annotations

import re
import uuid
from datetime import date

_TAXI_TYPE = re.compile(r"^[a-z]{2,16}$")
_FILE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")


def _check(pattern: re.Pattern[str], value: str, label: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _period(period: date) -> str:
    return f"year={period.year:04d}/month={period.month:02d}"


def raw_key(taxi_type: str, period: date, file_name: str) -> str:
    taxi = _check(_TAXI_TYPE, taxi_type, "taxi type")
    return f"raw/taxi_type={taxi}/{_period(period)}/{_check(_FILE, file_name, 'file')}"


def curated_prefix(taxi_type: str, period: date, run_id: str) -> str:
    return (
        f"curated/taxi_type={_check(_TAXI_TYPE, taxi_type, 'taxi type')}/{_period(period)}/"
        f"run_id={uuid.UUID(run_id)}"
    )


def quarantine_prefix(run_id: str) -> str:
    return f"quarantine/run_id={uuid.UUID(run_id)}"


def reference_key(name: str, sha256: str, file_name: str) -> str:
    _check(re.compile(r"^[a-z_]{1,40}$"), name, "reference name")
    _check(re.compile(r"^[0-9a-f]{64}$"), sha256, "sha256")
    return f"reference/{name}/sha256={sha256}/{_check(_FILE, file_name, 'file')}"


def s3_glob(bucket: str, prefix: str) -> str:
    """Path for ClickHouse's s3() named collection, whose URL is the lake endpoint root."""
    _check(_BUCKET, bucket, "bucket")
    if not re.fullmatch(r"[A-Za-z0-9=/_.-]+", prefix) or ".." in prefix:
        raise ValueError(f"unsafe lake prefix: {prefix!r}")
    return f"{bucket}/{prefix.rstrip('/')}/*.parquet"
