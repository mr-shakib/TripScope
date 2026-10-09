"""Thin wrapper over an S3-compatible object store (SeaweedFS locally; any S3 API in other environments)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from tripscope.core.errors import PipelineError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from tripscope.core.settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadedPrefix:
    prefix: str
    keys: list[str]
    total_bytes: int


class ObjectStore:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self.bucket = bucket

    @classmethod
    def from_settings(cls, settings: Settings, bucket: str | None = None) -> ObjectStore:
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 4, "mode": "standard"},
                connect_timeout=5,
                read_timeout=60,
            ),
        )
        return cls(client, bucket or settings.s3_bucket)

    def check_bucket(self) -> None:
        self._client.head_bucket(Bucket=self.bucket)

    def head(self, key: str) -> dict[str, Any] | None:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return dict(response)

    def put_file(self, key: str, path: Path, *, metadata: dict[str, str] | None = None) -> int:
        size = path.stat().st_size
        self._client.upload_file(str(path), self.bucket, key, ExtraArgs={"Metadata": metadata or {}})
        return size

    def put_immutable(self, key: str, path: Path, *, sha256: str, metadata: dict[str, str]) -> bool:
        """Upload a source file exactly once. Returns True if uploaded, False if the identical object exists.

        Raw inputs are immutable: an existing object with a different checksum is an error, never overwritten.
        """
        existing = self.head(key)
        if existing is not None:
            existing_sha = existing.get("Metadata", {}).get("sha256")
            if existing_sha == sha256:
                log.info("raw object already present", extra={"key": key})
                return False
            raise PipelineError(
                f"raw object {key} already exists with a different checksum; "
                "refusing to overwrite immutable input"
            )
        self.put_file(key, path, metadata={**metadata, "sha256": sha256})
        return True

    def upload_directory(self, local_dir: Path, prefix: str, *, suffix: str = ".parquet") -> UploadedPrefix:
        keys: list[str] = []
        total = 0
        for file in sorted(local_dir.rglob(f"*{suffix}")):
            key = f"{prefix.rstrip('/')}/{file.relative_to(local_dir).as_posix()}"
            total += self.put_file(key, file)
            keys.append(key)
        return UploadedPrefix(prefix=prefix, keys=keys, total_bytes=total)

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []) if "Key" in obj)
        return keys

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list_keys(prefix)
        for start in range(0, len(keys), 1000):
            chunk = keys[start : start + 1000]
            self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in chunk]})
        return len(keys)
