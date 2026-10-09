"""Session tokens, the current-user dependency, role checks and a small login throttle (FR-01)."""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, Request

from tripscope.core.errors import AuthenticationError, PermissionDeniedError, TripScopeError
from tripscope.metadata.models import Role, User

SESSION_COOKIE = "tripscope_session"
_ISSUER = "tripscope"
_AUDIENCE = "tripscope-web"
_ALGORITHM = "HS256"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    display_name: str
    role: Role


def create_session_token(user_id: uuid.UUID, *, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=ttl_minutes),
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)


def decode_session_token(token: str, *, secret: str) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            audience=_AUDIENCE,
            issuer=_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthenticationError("session is invalid or expired; sign in again") from exc


def get_current_user(request: Request) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AuthenticationError("sign in required")
    state = request.app.state
    user_id = decode_session_token(token, secret=state.settings.app_secret_key.get_secret_value())
    with state.session_factory() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("account is not active; sign in again")
        # Role comes from the database on every request, so role changes apply immediately.
        return CurrentUser(user.id, user.email, user.display_name, user.role)


AuthenticatedUser = Annotated[CurrentUser, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[[CurrentUser], CurrentUser]:
    allowed = frozenset(roles)

    def dependency(user: AuthenticatedUser) -> CurrentUser:
        if user.role not in allowed:
            raise PermissionDeniedError("your role does not permit this action")
        return user

    return dependency


class TooManyAttemptsError(TripScopeError):
    status_code = 429
    code = "too_many_attempts"


class LoginThrottle:
    """In-process sliding window of failed logins per (client IP, email). Adequate for a single API process;
    a shared store (e.g. Redis) replaces it when the API is scaled out."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300) -> None:
        self._max, self._window = max_failures, window_seconds
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: tuple[str, str], now: float) -> deque[float]:
        attempts = self._failures[key]
        while attempts and now - attempts[0] > self._window:
            attempts.popleft()
        return attempts

    def check(self, ip: str, email: str) -> None:
        with self._lock:
            if len(self._prune((ip, email), time.monotonic())) >= self._max:
                raise TooManyAttemptsError("too many failed sign-in attempts; wait a few minutes and retry")

    def record_failure(self, ip: str, email: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune((ip, email), now).append(now)

    def reset(self, ip: str, email: str) -> None:
        with self._lock:
            self._failures.pop((ip, email), None)
