from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tripscope.api.app import create_app
from tripscope.api.security import (
    CurrentUser,
    LoginThrottle,
    TooManyAttemptsError,
    create_session_token,
    decode_session_token,
    get_current_user,
)
from tripscope.core.errors import AuthenticationError
from tripscope.core.passwords import hash_password, verify_password
from tripscope.core.settings import Settings
from tripscope.metadata.models import Role

SECRET = "unit-test-secret-" + "x" * 32


def test_password_hash_roundtrip_and_policy() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "wrong password!!")
    assert not verify_password(None, "anything at all")
    assert not verify_password("not-a-hash", "anything at all")
    with pytest.raises(ValueError):
        hash_password("short")


def test_session_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    assert (
        decode_session_token(create_session_token(user_id, secret=SECRET, ttl_minutes=5), secret=SECRET)
        == user_id
    )


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda uid: create_session_token(uid, secret="another-secret-" + "y" * 32, ttl_minutes=5),
        lambda uid: jwt.encode(
            {
                "sub": str(uid),
                "iat": datetime.now(UTC) - timedelta(hours=2),
                "exp": datetime.now(UTC) - timedelta(hours=1),
                "iss": "tripscope",
                "aud": "tripscope-web",
            },
            SECRET,
            algorithm="HS256",
        ),
        lambda uid: jwt.encode(
            {
                "sub": str(uid),
                "iss": "tripscope",
                "aud": "other",
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            SECRET,
            algorithm="HS256",
        ),
        lambda uid: jwt.encode({"sub": str(uid)}, None, algorithm="none"),
        lambda uid: "garbage",
    ],
    ids=["wrong-secret", "expired", "wrong-audience", "alg-none", "garbage"],
)
def test_invalid_tokens_are_rejected(token_factory: Any) -> None:
    with pytest.raises(AuthenticationError):
        decode_session_token(token_factory(uuid.uuid4()), secret=SECRET)


def test_login_throttle_blocks_after_repeated_failures() -> None:
    throttle = LoginThrottle(max_failures=3, window_seconds=60)
    for _ in range(3):
        throttle.check("1.2.3.4", "a@b.c")
        throttle.record_failure("1.2.3.4", "a@b.c")
    with pytest.raises(TooManyAttemptsError):
        throttle.check("1.2.3.4", "a@b.c")
    throttle.check("5.6.7.8", "a@b.c")  # other clients are unaffected
    throttle.reset("1.2.3.4", "a@b.c")
    throttle.check("1.2.3.4", "a@b.c")


def _settings() -> Settings:
    return Settings(
        app_secret_key=SecretStr(SECRET),
        postgres_user="u",
        postgres_password=SecretStr("p" * 20),
        clickhouse_reader_password=SecretStr("r" * 20),
        _env_file=None,  # type: ignore[call-arg]
    )


class _FakeAnalytics:
    def overview(self, filters: Any) -> dict[str, Any]:
        return {"kpis": {}, "data_state": "ok", "meta": {"filters": filters.applied()}}

    def time_series(self, query: Any) -> dict[str, Any]:
        return {"points": [], "data_state": "empty", "meta": {"metric": query.metric}}


@pytest.fixture
def client_for() -> Any:
    def make(role: Role | None) -> TestClient:
        app = create_app(_settings())
        app.state.analytics = _FakeAnalytics()
        if role is not None:
            user = CurrentUser(uuid.uuid4(), f"{role.value}@example.org", role.value, role)
            app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    return make


def test_protected_endpoints_require_authentication(client_for: Any) -> None:
    client = client_for(None)
    for path in [
        "/api/v1/analytics/overview",
        "/api/v1/analytics/trips-over-time",
        "/api/v1/auth/me",
        "/api/v1/ingestion-jobs",
        "/api/v1/datasets",
    ]:
        response = client.get(path)
        assert response.status_code == 401, path
        body = response.json()["error"]
        assert body["code"] == "not_authenticated" and body["request_id"]


def test_viewer_cannot_list_jobs_but_can_read_analytics(client_for: Any) -> None:
    viewer = client_for(Role.VIEWER)
    assert viewer.get("/api/v1/ingestion-jobs").status_code == 403
    assert viewer.get("/api/v1/analytics/overview").status_code == 200


def test_unknown_query_parameters_are_rejected(client_for: Any) -> None:
    response = client_for(Role.ANALYST).get("/api/v1/analytics/overview?pickup_zone=132&sql=SELECT+1")
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "sql"


def test_filters_reach_the_service_validated(client_for: Any) -> None:
    response = client_for(Role.ANALYST).get(
        "/api/v1/analytics/overview?start_date=2025-01-06&pickup_zone=132&pickup_zone=161"
    )
    assert response.status_code == 200
    assert response.json()["meta"]["filters"] == {
        "start_date": "2025-01-06",
        "pickup_zone": [132, 161],
        "dataset_id": "nyc-tlc-yellow",
    }


def test_security_headers_and_request_id(client_for: Any) -> None:
    response = client_for(Role.ANALYST).get(
        "/api/v1/analytics/overview", headers={"X-Request-ID": "abcdef123456"}
    )
    assert response.headers["x-request-id"] == "abcdef123456"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["server-timing"].startswith("app;dur=")


def test_settings_reject_placeholder_secrets() -> None:
    with pytest.raises(ValueError, match="placeholder"):
        Settings(
            app_secret_key=SecretStr(SECRET),
            postgres_user="u",
            postgres_password=SecretStr("replace-me"),
            clickhouse_reader_password=SecretStr("r" * 20),
            _env_file=None,  # type: ignore[call-arg]
        )
