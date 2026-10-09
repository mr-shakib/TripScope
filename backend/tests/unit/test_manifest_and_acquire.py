from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from tests.fixtures.tlc_fixture import write_fixture_parquet
from tripscope.core.errors import SourceValidationError
from tripscope.pipeline.acquire import acquire, inspect_parquet
from tripscope.pipeline.manifest import Manifest, load_manifest
from tripscope.pipeline.zones import mapped_zone_ids, parse_zone_lookup

REPO_ROOT = Path(__file__).resolve().parents[3]

BASE = {
    "version": 1,
    "allowed_hosts": ["d37ci6vzurychx.cloudfront.net"],
    "datasets": {"nyc-tlc-yellow": {"name": "Yellow", "taxi_type": "yellow", "source_attribution": "TLC"}},
}


def _source(**overrides: object) -> dict[str, object]:
    source = {
        "key": "yellow-2025-01",
        "dataset": "nyc-tlc-yellow",
        "period": "2025-01",
        "format": "parquet",
        "uri": "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2025-01.parquet",
    }
    source.update(overrides)
    return source


def test_repository_manifest_is_valid() -> None:
    manifest = load_manifest(REPO_ROOT / "manifests" / "sources.yaml")
    source = manifest.get_source("yellow-2025-01")
    assert source.period_start == date(2025, 1, 1)
    assert source.period_end_exclusive == date(2025, 2, 1)
    assert source.file_name == "yellow_tripdata_2025-01.parquet"
    assert source.expected_sha256 is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"uri": "https://evil.example.com/yellow.parquet"},
        {"uri": "http://d37ci6vzurychx.cloudfront.net/trip-data/x.parquet"},
        {"uri": "ftp://d37ci6vzurychx.cloudfront.net/x.parquet"},
        {"period": "2025-13"},
        {"key": "Bad Key!"},
        {"expected_sha256": "not-a-sha"},
        {"dataset": "unknown"},
        {"format": "exe"},
        {"surprise": "field"},
    ],
)
def test_manifest_rejects_unsafe_or_invalid_sources(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate({**BASE, "sources": [_source(**overrides)]})


def test_local_file_uri_cannot_escape_base_dir(tmp_path: Path) -> None:
    manifest = Manifest.model_validate({**BASE, "sources": [], "base_dir": tmp_path})
    with pytest.raises(ValueError, match="escapes"):
        manifest.resolve_local_path("file://../../etc/passwd")
    assert manifest.resolve_local_path("file://data/x.parquet") == (tmp_path / "data/x.parquet").resolve()


def test_acquire_local_file_verifies_checksum_and_size(tmp_path: Path) -> None:
    path = tmp_path / "f.parquet"
    write_fixture_parquet(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    ok = acquire(
        "file://f.parquet",
        dest_dir=tmp_path,
        file_name=path.name,
        max_bytes=10**7,
        expected_sha256=digest,
        local_path=path,
    )
    assert ok.sha256 == digest and ok.size_bytes == path.stat().st_size

    with pytest.raises(SourceValidationError, match="checksum mismatch"):
        acquire(
            "file://f.parquet",
            dest_dir=tmp_path,
            file_name=path.name,
            max_bytes=10**7,
            expected_sha256="0" * 64,
            local_path=path,
        )
    with pytest.raises(SourceValidationError, match="limit"):
        acquire(
            "file://f.parquet",
            dest_dir=tmp_path,
            file_name=path.name,
            max_bytes=10,
            expected_sha256=None,
            local_path=path,
        )


def test_download_enforces_size_limit_and_checksum(tmp_path: Path) -> None:
    payload = b"PAR1" + b"x" * 5000 + b"PAR1"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    uri = "https://d37ci6vzurychx.cloudfront.net/trip-data/a.parquet"
    with pytest.raises(SourceValidationError, match="limit"):
        acquire(
            uri,
            dest_dir=tmp_path,
            file_name="a.parquet",
            max_bytes=1000,
            expected_sha256=None,
            http_client=client,
        )
    assert not (tmp_path / "a.parquet").exists() and not (tmp_path / "a.parquet.part").exists()

    with pytest.raises(SourceValidationError, match="checksum"):
        acquire(
            uri,
            dest_dir=tmp_path,
            file_name="a.parquet",
            max_bytes=10**6,
            expected_sha256="1" * 64,
            http_client=client,
        )
    assert not (tmp_path / "a.parquet").exists()

    good = acquire(
        uri,
        dest_dir=tmp_path,
        file_name="a.parquet",
        max_bytes=10**6,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        http_client=client,
    )
    assert good.downloaded and good.local_path.read_bytes() == payload


def test_download_rejects_http_errors(tmp_path: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(SourceValidationError, match="HTTP 404"):
        acquire(
            "https://d37ci6vzurychx.cloudfront.net/x.parquet",
            dest_dir=tmp_path,
            file_name="x.parquet",
            max_bytes=100,
            expected_sha256=None,
            http_client=client,
        )


def test_inspect_parquet_rejects_non_parquet(tmp_path: Path) -> None:
    fake = tmp_path / "fake.parquet"
    fake.write_text("VendorID,tpep_pickup_datetime\n1,2025-01-01\n")
    with pytest.raises(SourceValidationError, match="not Parquet"):
        inspect_parquet(fake)
    corrupt = tmp_path / "corrupt.parquet"
    corrupt.write_bytes(b"PAR1" + b"\x00" * 100 + b"PAR1")
    with pytest.raises(SourceValidationError, match="unreadable"):
        inspect_parquet(corrupt)


def test_inspect_parquet_reads_real_schema_from_fixture(tmp_path: Path) -> None:
    path = tmp_path / "f.parquet"
    rows = write_fixture_parquet(path)
    info = inspect_parquet(path)
    assert info.num_rows == len(rows)
    assert info.columns["Airport_fee"] == "double"
    assert info.columns["tpep_pickup_datetime"] == "timestamp[us]"


def test_zone_lookup_parsing(tmp_path: Path) -> None:
    lookup = tmp_path / "zones.csv"
    lookup.write_text(
        '"LocationID","Borough","Zone","service_zone"\n'
        '1,"EWR","Newark Airport","EWR"\n'
        '132,"Queens","JFK Airport","Airports"\n'
        '264,"Unknown","N/A","N/A"\n'
        '265,"N/A","Outside of NYC","N/A"\n'
    )
    zones = parse_zone_lookup(lookup)
    assert [z.location_id for z in zones] == [1, 132, 264, 265]
    assert mapped_zone_ids(zones) == [1, 132]

    lookup.write_text('"id","name"\n1,"x"\n')
    with pytest.raises(SourceValidationError, match="header"):
        parse_zone_lookup(lookup)
    lookup.write_text('"LocationID","Borough","Zone","service_zone"\n1,"a","b","c"\n1,"a","b","c"\n')
    with pytest.raises(SourceValidationError, match="duplicate"):
        parse_zone_lookup(lookup)
