"""Source manifest: which files to ingest, where they come from, and the checksums we expect (spec §2.2)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$")
_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetSpec(_Strict):
    name: str
    taxi_type: Literal["yellow"]  # other vehicle types arrive with their schema mappings in Phase 2
    description: str = ""
    source_attribution: str


class ReferenceFile(_Strict):
    uri: str
    expected_sha256: str | None = None
    retrieved_from: str | None = None


# CSV dialects we know how to parse. Timestamps use Spark datetime patterns.
CSV_PROFILES: dict[str, dict[str, str]] = {
    # NYC Open Data (Socrata) export: every value quoted, US 12-hour timestamps.
    "nyc_open_data": {"timestamp_format": "MM/dd/yyyy hh:mm:ss a"},
}


class SourceSpec(_Strict):
    key: str
    dataset: str
    period: str
    format: Literal["parquet", "csv"]
    csv_profile: Literal["nyc_open_data"] | None = None
    uri: str
    expected_sha256: str | None = None
    retrieved_from: str | None = None

    @model_validator(mode="after")
    def _csv_needs_profile(self) -> SourceSpec:
        if self.format == "csv" and self.csv_profile is None:
            raise ValueError("csv sources must name a csv_profile (e.g. nyc_open_data)")
        if self.format != "csv" and self.csv_profile is not None:
            raise ValueError("csv_profile only applies to csv sources")
        return self

    @property
    def timestamp_format(self) -> str | None:
        return CSV_PROFILES[self.csv_profile]["timestamp_format"] if self.csv_profile else None

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not _KEY.fullmatch(value):
            raise ValueError("source key must be lowercase letters, digits and hyphens")
        return value

    @field_validator("period")
    @classmethod
    def _period(cls, value: str) -> str:
        if not _PERIOD.fullmatch(value):
            raise ValueError("period must be YYYY-MM")
        return value

    @field_validator("expected_sha256")
    @classmethod
    def _sha(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("expected_sha256 must be 64 lowercase hex characters")
        return value

    @property
    def period_start(self) -> date:
        year, month = self.period.split("-")
        return date(int(year), int(month), 1)

    @property
    def period_end_exclusive(self) -> date:
        start = self.period_start
        return date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)

    @property
    def file_name(self) -> str:
        name = Path(urlparse(self.uri).path).name
        if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise ValueError(f"cannot derive a safe file name from {self.uri!r}")
        return name


class Manifest(_Strict):
    version: Literal[1]
    allowed_hosts: list[str] = Field(default_factory=list)
    datasets: dict[str, DatasetSpec]
    reference: dict[str, ReferenceFile] = Field(default_factory=dict)
    sources: list[SourceSpec]
    base_dir: Path = Path(".")

    @model_validator(mode="after")
    def _check(self) -> Manifest:
        keys = [s.key for s in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate source keys in manifest")
        for source in self.sources:
            if source.dataset not in self.datasets:
                raise ValueError(f"source {source.key} references unknown dataset {source.dataset}")
            self.check_uri(source.uri)
        for ref in self.reference.values():
            self.check_uri(ref.uri)
        return self

    def check_uri(self, uri: str) -> None:
        """Only HTTPS from allow-listed hosts, or local files inside the manifest directory tree."""
        parsed = urlparse(uri)
        if parsed.scheme == "https":
            if parsed.hostname not in self.allowed_hosts:
                raise ValueError(f"host {parsed.hostname!r} is not in allowed_hosts")
            return
        if parsed.scheme == "file":
            return  # resolved and confined to base_dir in resolve_local_path
        raise ValueError(f"unsupported URI scheme in {uri!r}; use https:// or file://")

    def resolve_local_path(self, uri: str) -> Path:
        parsed = urlparse(uri)
        base = self.base_dir.resolve()
        path = (base / (parsed.netloc + parsed.path).lstrip("/")).resolve()
        if not path.is_relative_to(base):
            raise ValueError(f"local source {uri!r} escapes the manifest directory")
        return path

    def get_source(self, key: str) -> SourceSpec:
        for source in self.sources:
            if source.key == key:
                return source
        raise KeyError(f"unknown source key {key!r}; known: {', '.join(s.key for s in self.sources)}")


def load_manifest(path: Path, *, base_dir: Path | None = None) -> Manifest:
    """Load a manifest. `file://` URIs are resolved relative to `base_dir` (default: the repository root)."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("manifest must be a YAML mapping")
    raw["base_dir"] = base_dir or path.resolve().parent.parent
    return Manifest.model_validate(raw)
