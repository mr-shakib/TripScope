"""Application settings loaded from environment variables (and a local `.env` during development).

Secrets are typed as `SecretStr` so they never appear in reprs or logs. Placeholder values from
`.env.example` are rejected at startup so the system fails safely instead of running with known credentials.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRET = "replace-me"  # noqa: S105 - the placeholder we refuse, not a credential


def find_repo_root(start: Path | None = None) -> Path | None:
    """The checkout root (holds compose.yaml and manifests/), searched upward from `start` or the CWD."""
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "compose.yaml").is_file() and (candidate / "manifests").is_dir():
            return candidate
    return None


def _env_files() -> tuple[Path, ...]:
    """`.env` at the repository root, whether we run from the repo root or from `backend/`."""
    cwd = Path.cwd()
    return (cwd.parent / ".env", cwd / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_env_files(), extra="ignore", frozen=True)

    app_env: Literal["development", "test", "production"] = "development"
    app_secret_key: SecretStr
    app_cors_origins: str = ""
    session_ttl_minutes: int = Field(default=480, ge=5, le=24 * 60)

    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str = "tripscope"
    postgres_host: str = "localhost"
    postgres_port: int = 5433

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_database: str = "tripscope"
    clickhouse_writer_user: str = "tripscope_writer"
    clickhouse_writer_password: SecretStr = SecretStr("")
    clickhouse_reader_user: str = "tripscope_reader"
    clickhouse_reader_password: SecretStr

    s3_endpoint_url: str = "http://localhost:8333"
    s3_bucket: str = "tripscope-lake"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_region: str = "us-east-1"

    manifest_path: Path | None = None  # default: <repo>/manifests/sources.yaml
    pipeline_work_dir: Path = Path("./data/work")
    pipeline_max_source_mb: int = Field(default=500, ge=1, le=10_000)
    spark_driver_memory: str = "4g"
    spark_master: str = "local[*]"
    spark_java_home: Path | None = None  # full JDK 17+; defaults to the JVM on PATH / JAVA_HOME

    @field_validator("pipeline_work_dir")
    @classmethod
    def _absolute_work_dir(cls, value: Path) -> Path:
        """Relative paths are relative to the checkout root, so host runs work from any directory."""
        if value.is_absolute():
            return value
        root = find_repo_root()
        return (root / value).resolve() if root else value.resolve()

    @field_validator("spark_java_home", mode="before")
    @classmethod
    def _empty_java_home(cls, value: object) -> object:
        return None if value == "" else value

    analytics_query_timeout_seconds: int = Field(default=30, ge=1, le=300)
    analytics_max_range_days: int = Field(default=1100, ge=1, le=5000)
    max_export_rows: int = Field(default=100_000, ge=1, le=1_000_000)

    # AI analyst (Phase 5, ADR-11/20/21). `disabled` keeps everything else working (demo questions only).
    llm_provider: Literal["disabled", "openai_compatible"] = "disabled"
    llm_base_url: str = ""  # e.g. https://api.deepseek.com/v1 or http://127.0.0.1:11434/v1 (Ollama)
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: int = Field(default=120, ge=5, le=600)
    llm_reasoning_effort: str = ""  # sent only when set (e.g. "none" skips a local model's thinking phase)
    llm_supports_tools: bool = (
        True  # False: the model plans tool calls as JSON instead (servers without tools)
    )
    llm_local_only: bool = False  # refuse providers outside loopback/private networks
    ai_allow_viewers: bool = False  # spec §3.1: viewers ask the AI only when an administrator enables it
    ai_max_tool_calls: int = Field(default=6, ge=1, le=12)
    ai_retention_days: int = Field(default=30, ge=1, le=3650)

    @field_validator("clickhouse_database")
    @classmethod
    def _identifier(cls, value: str) -> str:
        from tripscope.core.identifiers import validate_identifier

        return validate_identifier(value)

    @model_validator(mode="after")
    def _reject_placeholders(self) -> Settings:
        secrets = {
            "APP_SECRET_KEY": self.app_secret_key,
            "POSTGRES_PASSWORD": self.postgres_password,
            "CLICKHOUSE_READER_PASSWORD": self.clickhouse_reader_password,
            "CLICKHOUSE_WRITER_PASSWORD": self.clickhouse_writer_password,
            "S3_ACCESS_KEY": self.s3_access_key,
            "S3_SECRET_KEY": self.s3_secret_key,
        }
        bad = [name for name, value in secrets.items() if value.get_secret_value() == PLACEHOLDER_SECRET]
        if bad:
            raise ValueError(f"placeholder values must be replaced: {', '.join(bad)}")
        if len(self.app_secret_key.get_secret_value()) < 32:
            raise ValueError("APP_SECRET_KEY must be at least 32 characters")
        return self

    @property
    def database_url(self) -> str:
        from sqlalchemy.engine import URL

        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def resolved_manifest_path(self) -> Path:
        if self.manifest_path is not None:
            return self.manifest_path
        root = find_repo_root()
        return (root / "manifests" / "sources.yaml") if root else Path("manifests/sources.yaml")

    @property
    def secure_cookies(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.app_cors_origins.split(",") if o.strip()]

    def secret_values(self) -> list[str]:
        """All configured secret strings, used to redact error messages before they are stored or logged."""
        values = [
            self.app_secret_key,
            self.postgres_password,
            self.clickhouse_reader_password,
            self.clickhouse_writer_password,
            self.s3_access_key,
            self.s3_secret_key,
        ]
        return [v.get_secret_value() for v in values if len(v.get_secret_value()) >= 4]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # populated from the environment
