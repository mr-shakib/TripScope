"""Sign in / sign out / current user (FR-01)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from tripscope.api.security import SESSION_COOKIE, AuthenticatedUser, create_session_token
from tripscope.core.errors import AuthenticationError
from tripscope.core.passwords import hash_password, needs_rehash, verify_password
from tripscope.metadata.audit import record_audit
from tripscope.metadata.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[str, Field(min_length=3, max_length=320)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, request: Request, response: Response) -> UserOut:
    state = request.app.state
    email, ip = body.email.strip().lower(), _client_ip(request)
    state.login_throttle.check(ip, email)
    out: UserOut | None = None
    with state.session_factory() as session, session.begin():
        user = session.scalar(select(User).where(func.lower(User.email) == email))
        valid = verify_password(user.password_hash if user else None, body.password)
        if not valid or user is None or not user.is_active:
            state.login_throttle.record_failure(ip, email)
            record_audit(session, action="auth.login", outcome="failure", actor_label=email, client_ip=ip)
        else:
            state.login_throttle.reset(ip, email)
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(body.password)
            user.last_login_at = datetime.now(UTC)
            record_audit(
                session,
                action="auth.login",
                outcome="success",
                actor_user_id=user.id,
                actor_label=email,
                client_ip=ip,
            )
            out = UserOut(
                id=str(user.id), email=user.email, display_name=user.display_name, role=user.role.value
            )
            user_id = user.id
    if out is None:
        # Raised after the audit row is committed. Same message for unknown email, wrong password, disabled.
        raise AuthenticationError("email or password is incorrect")
    token = create_session_token(
        user_id,
        secret=state.settings.app_secret_key.get_secret_value(),
        ttl_minutes=state.settings.session_ttl_minutes,
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=state.settings.session_ttl_minutes * 60,
        httponly=True,
        secure=state.settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return out


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: AuthenticatedUser) -> Response:
    with request.app.state.session_factory() as session, session.begin():
        record_audit(
            session,
            action="auth.logout",
            outcome="success",
            actor_user_id=user.id,
            actor_label=user.email,
            client_ip=_client_ip(request),
        )
    response.status_code = 204
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=request.app.state.settings.secure_cookies,
        samesite="lax",
    )
    return response


@router.get("/me", response_model=UserOut)
def me(user: AuthenticatedUser) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, display_name=user.display_name, role=user.role.value)
