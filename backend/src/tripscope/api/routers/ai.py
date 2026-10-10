"""AI analyst endpoints (spec §9): status, chat (answered in the background), conversations.

Admins and analysts may ask; viewers only when AI_ALLOW_VIEWERS is on (spec §3.1). Conversations are private
to their owner. Questions are plain text: they never become SQL, and the model can act only through the
allowlisted tools in `tripscope.ai.tools`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tripscope.analytics.filters import AnalyticsFilters
from tripscope.api.security import AuthenticatedUser

router = APIRouter(prefix="/ai", tags=["ai"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=2000)
    conversation_id: uuid.UUID | None = None
    demo_id: str | None = Field(default=None, pattern=r"^[a-z_]{1,40}$")
    # The dashboard filters on screen, offered to the model as context for the question.
    filters: AnalyticsFilters | None = None

    @model_validator(mode="after")
    def _something_to_ask(self) -> ChatRequest:
        if not self.demo_id and not self.message.strip():
            raise ValueError("ask a question (message) or pick a demo question (demo_id)")
        return self


class OutlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: AnalyticsFilters = Field(default_factory=AnalyticsFilters)
    focus: str = Field(default="", max_length=600)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    filters: AnalyticsFilters = Field(default_factory=AnalyticsFilters)
    sections: list[Annotated[str, Field(pattern=r"^[a-z_]{1,40}$")]] = Field(min_length=1, max_length=15)
    focus: str = Field(default="", max_length=600)
    visibility: Literal["private", "shared"] = "private"


class RedraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: str | None = Field(default=None, max_length=600)


class ListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=200)


@router.get("/status")
def status(request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    return dict(request.app.state.ai.status(user))


@router.post("/chat", status_code=202)
def chat(body: ChatRequest, request: Request, user: AuthenticatedUser) -> dict[str, str]:
    """Store the question and answer it in the background; poll the conversation for steps and the answer."""
    return dict(
        request.app.state.ai.ask(
            user,
            question=body.message,
            conversation_id=body.conversation_id,
            page_filters=body.filters,
            demo_id=body.demo_id,
        )
    )


@router.post("/report-outline")
def report_outline(body: OutlineRequest, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    """FR-11 step 2: the model proposes a title and sections from the template-6 library."""
    return dict(request.app.state.ai.outline(user, filters=body.filters, focus=body.focus))


@router.post("/report-draft", status_code=202)
def report_draft(body: DraftRequest, request: Request, user: AuthenticatedUser) -> dict[str, str]:
    """FR-11 steps 3 to 6: create the report now and draft its verified narrative in the background."""
    return dict(
        request.app.state.ai.draft_report(
            user,
            title=body.title,
            filters=body.filters,
            sections=body.sections,
            focus=body.focus,
            visibility=body.visibility,
        )
    )


@router.post("/reports/{report_id}/redraft", status_code=202)
def redraft(
    report_id: uuid.UUID, body: RedraftRequest, request: Request, user: AuthenticatedUser
) -> dict[str, str]:
    return dict(request.app.state.ai.redraft(user, report_id, body.focus))


@router.get("/conversations")
def conversations(
    request: Request, user: AuthenticatedUser, query: Annotated[ListQuery, Query()]
) -> dict[str, Any]:
    request.app.state.ai.require_chat(user)
    return {"conversations": request.app.state.ai.conversations(user, query.limit)}


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: uuid.UUID, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    request.app.state.ai.require_chat(user)
    return dict(request.app.state.ai.conversation(user, conversation_id))


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, request: Request, user: AuthenticatedUser) -> Response:
    request.app.state.ai.require_chat(user)
    request.app.state.ai.delete(user, conversation_id)
    return Response(status_code=204)
