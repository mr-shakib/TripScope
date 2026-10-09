"""Acquire and validate source files before any processing (spec §7.1 steps 1-4, FR-02).

Files are treated strictly as data: they are size-capped, checksummed and parsed by pyarrow's metadata
reader. Nothing in a file is executed or interpreted as an instruction.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pyarrow.parquet as pq

from tripscope.core.errors import SourceValidationError

log = logging.getLogger(__name__)

PARQUET_MAGIC = b"PAR1"
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class AcquiredFile:
    local_path: Path
    source_uri: str
    sha256: str
    size_bytes: int
    retrieved_at: datetime
    downloaded: bool


@dataclass(frozen=True)
class SourceInspection:
    num_rows: int
    num_row_groups: int
    columns: dict[str, str]  # column -> arrow type string
    created_by: str | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksum(actual: str, expected: str | None, label: str) -> None:
    if expected is not None and actual != expected:
        raise SourceValidationError(
            f"checksum mismatch for {label}: expected {expected[:12]}…, got {actual[:12]}…. "
            "The upstream file may have been revised; review it and update the manifest pin deliberately."
        )


def acquire(
    uri: str,
    *,
    dest_dir: Path,
    file_name: str,
    max_bytes: int,
    expected_sha256: str | None,
    local_path: Path | None = None,
    http_client: httpx.Client | None = None,
) -> AcquiredFile:
    """Return a verified local copy of `uri`. HTTPS downloads are cached in `dest_dir` and re-verified."""
    scheme = urlparse(uri).scheme
    if scheme == "file":
        if local_path is None or not local_path.is_file():
            raise SourceValidationError(f"local source file not found: {uri}")
        size = local_path.stat().st_size
        if size > max_bytes:
            raise SourceValidationError(f"source is {size} bytes; limit is {max_bytes}")
        checksum = sha256_file(local_path)
        _verify_checksum(checksum, expected_sha256, file_name)
        mtime = datetime.fromtimestamp(local_path.stat().st_mtime, UTC)
        return AcquiredFile(local_path, uri, checksum, size, mtime, downloaded=False)

    if scheme != "https":
        raise SourceValidationError(f"unsupported source scheme: {scheme}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / file_name
    if target.is_file() and expected_sha256 is not None and sha256_file(target) == expected_sha256:
        log.info("using cached source file", extra={"path": str(target)})
        mtime = datetime.fromtimestamp(target.stat().st_mtime, UTC)
        return AcquiredFile(target, uri, expected_sha256, target.stat().st_size, mtime, downloaded=False)

    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    client = http_client or httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False)
    try:
        with client.stream("GET", uri) as response:
            if response.status_code != 200:
                raise SourceValidationError(f"download failed with HTTP {response.status_code}")
            declared = int(response.headers.get("content-length", "0") or 0)
            if declared > max_bytes:
                raise SourceValidationError(f"source declares {declared} bytes; limit is {max_bytes}")
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(_CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise SourceValidationError(f"source exceeds the {max_bytes}-byte limit")
                    digest.update(chunk)
                    handle.write(chunk)
        actual = digest.hexdigest()
        _verify_checksum(actual, expected_sha256, file_name)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
        if http_client is None:
            client.close()
    retrieved_at = datetime.now(UTC)
    log.info("downloaded source file", extra={"uri": uri, "bytes": size})
    return AcquiredFile(target, uri, actual, size, retrieved_at, downloaded=True)


def inspect_parquet(path: Path) -> SourceInspection:
    """Validate Parquet framing and read the schema from the footer without loading data."""
    size = path.stat().st_size
    if size < 12:
        raise SourceValidationError("file too small to be Parquet")
    with path.open("rb") as handle:
        head = handle.read(4)
        handle.seek(-4, os.SEEK_END)
        tail = handle.read(4)
    if head != PARQUET_MAGIC or tail != PARQUET_MAGIC:
        raise SourceValidationError("file is not Parquet (missing PAR1 magic bytes)")
    try:
        parquet = pq.ParquetFile(path)
    except Exception as exc:  # pyarrow raises several error types for corrupt footers
        raise SourceValidationError(f"unreadable Parquet footer: {type(exc).__name__}") from exc
    metadata = parquet.metadata
    if metadata.num_rows <= 0:
        raise SourceValidationError("Parquet file contains no rows")
    schema = parquet.schema_arrow
    columns = {name: str(schema.field(name).type) for name in schema.names}
    return SourceInspection(
        num_rows=metadata.num_rows,
        num_row_groups=metadata.num_row_groups,
        columns=columns,
        created_by=metadata.created_by,
    )


def inspect_csv(path: Path) -> SourceInspection:
    """Validate a CSV export line by line before Spark sees it (ADR-17).

    Every row must have exactly the header's field count. Exports that end with a server error body (JSON or
    HTML instead of data) are reported as truncated, because publishing them would misstate coverage.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if not header or any(not name.strip() for name in header):
                raise SourceValidationError("CSV header is missing or has empty column names")
            if len({h.lower() for h in header}) != len(header):
                raise SourceValidationError("CSV header has duplicate column names")
            rows = 0
            for row in reader:
                if len(row) != len(header):
                    line = reader.line_num
                    first = (row[0] if row else "").lstrip()
                    if first.startswith(("{", "<")):
                        raise SourceValidationError(
                            f"line {line}: the export ends with a server response instead of data "
                            f"({first[:40]!r}…) after {rows:,} rows; "
                            "the download is truncated, re-export the file"
                        )
                    raise SourceValidationError(
                        f"line {line} has {len(row)} fields; the header has {len(header)}"
                    )
                rows += 1
    except UnicodeDecodeError as exc:
        raise SourceValidationError(f"CSV is not valid UTF-8 near byte {exc.start}") from exc
    except csv.Error as exc:
        raise SourceValidationError(f"CSV is malformed: {exc}") from exc
    if rows == 0:
        raise SourceValidationError("CSV contains a header but no rows")
    return SourceInspection(
        num_rows=rows, num_row_groups=0, columns=dict.fromkeys(header, "string"), created_by="csv"
    )
