"""The AI analyst service: conversations, background answering and the evidence the UI shows (FR-10, ADR-21).

A question is stored at once and answered on a small thread pool, so a slow local model never holds an HTTP
request open; the page polls the conversation. Each tool run is stored as it finishes (live progress, and the
`ai_tool_runs` log the spec asks for). Conversations are private to their owner and deleted after
AI_RETENTION_DAYS.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from tripscope.ai.agent import Agent, AgentResult
from tripscope.ai.demo import DEMO_QUESTIONS, run_demo
from tripscope.ai.provider import LLMProvider
from tripscope.ai.tools import ToolContext, ToolRun
from tripscope.analytics.filters import AnalyticsFilters
from tripscope.analytics.service import AnalyticsService
from tripscope.core.errors import (
    AIUnavailableError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    TripScopeError,
)
from tripscope.core.settings import Settings
from tripscope.metadata.audit import record_audit
from tripscope.metadata.models import AIConversation, AIMessage, AIToolRun, Report, Role
from tripscope.reports import service as reports
from tripscope.reports.builder import ReportBuilder
from tripscope.reports.narrative import TEMPLATE as NARRATIVE_TEMPLATE
from tripscope.reports.narrative import draft_narrative, fingerprint, propose_outline
from tripscope.reports.service import Actor
from tripscope.reports.templates import TEMPLATES

log = logging.getLogger(__name__)

SUGGESTIONS = [
    "Which pickup zones had the most trips in March 2025?",
    "How did average fares change from January to June?",
    "What share of trips were paid in cash on weekends?",
    "Which hours have the longest trips from JFK Airport?",
]


def _now() -> datetime:
    return datetime.now(UTC)


class AIAnalyst:
    def __init__(
        self,
        *,
        settings: Settings,
        analytics: AnalyticsService,
        session_factory: sessionmaker[Session],
        provider: LLMProvider,
        workers: int = 2,
    ) -> None:
        self.settings = settings
        self.analytics = analytics
        self.sessions = session_factory
        self.provider = provider
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai")
        # A running answer older than this was lost (timeout or restart) and is reported as failed on read.
        self.stale_after = timedelta(
            seconds=settings.llm_timeout_seconds * (settings.ai_max_tool_calls + 3) + 60
        )

    # ---- access ----------------------------------------------------------------------------------------

    def can_chat(self, user: Actor) -> bool:
        return user.role in (Role.ADMIN, Role.ANALYST) or self.settings.ai_allow_viewers

    def require_chat(self, user: Actor) -> None:
        if not self.can_chat(user):
            raise PermissionDeniedError(
                "viewers can use the AI analyst only when an administrator enables it"
            )

    def status(self, user: Actor) -> dict[str, Any]:
        provider = self.provider
        return {
            "enabled": provider.enabled,
            "provider": provider.name,
            "model": provider.model or None,
            "local": provider.local,
            "reason": provider.reason,
            "can_chat": self.can_chat(user),
            "tools": provider.supports_tools,
            "max_tool_calls": self.settings.ai_max_tool_calls,
            "retention_days": self.settings.ai_retention_days,
            "demo_questions": [{"id": q.id, "text": q.text} for q in DEMO_QUESTIONS.values()],
            "suggestions": SUGGESTIONS if provider.enabled else [],
        }

    # ---- asking ----------------------------------------------------------------------------------------

    def ask(
        self,
        user: Actor,
        *,
        question: str,
        conversation_id: uuid.UUID | None,
        page_filters: AnalyticsFilters | None,
        demo_id: str | None,
    ) -> dict[str, str]:
        self.require_chat(user)
        demo = DEMO_QUESTIONS.get(demo_id) if demo_id else None
        if demo_id and demo is None:
            raise NotFoundError("unknown demo question")
        if demo is None and not self.provider.enabled:
            raise AIUnavailableError(
                (self.provider.reason or "the AI analyst is not configured") + " Try a demo question instead."
            )
        text = demo.text if demo else question.strip()
        with self.sessions() as session, session.begin():
            self._purge(session)
            if conversation_id is None:
                conversation = AIConversation(user_id=user.id, title=text[:200])
                session.add(conversation)
                session.flush()
            else:
                conversation = self._owned(session, conversation_id, user)
                self._fail_stale(conversation)
                if any(m.status == "running" for m in conversation.messages):
                    raise ConflictError("the previous question is still being answered")
                conversation.updated_at = _now()
            history = [
                {"role": m.role, "content": m.content}
                for m in conversation.messages
                if m.status in ("sent", "answered", "clarification") and m.content
            ]
            applied = page_filters.applied() if page_filters else None
            session.add(
                AIMessage(
                    conversation=conversation, role="user", content=text, status="sent", page_filters=applied
                )
            )
            answer = AIMessage(
                conversation=conversation,
                role="assistant",
                status="running",
                mode="demo" if demo else "llm",
                provider=None if demo else self.provider.name,
                model=None if demo else self.provider.model,
                page_filters=applied,
            )
            session.add(answer)
            session.flush()
            ids = {"conversation_id": str(conversation.id), "message_id": str(answer.id)}
            message_id = answer.id
        self.executor.submit(
            self._answer, user, message_id, text, history, page_filters, demo.id if demo else None
        )
        return ids

    def _answer(
        self,
        user: Actor,
        message_id: uuid.UUID,
        question: str,
        history: list[dict[str, str]],
        page_filters: AnalyticsFilters | None,
        demo_id: str | None,
    ) -> None:
        dataset = page_filters.dataset_id if page_filters else "nyc-tlc-yellow"
        ctx = ToolContext(self.analytics, user, dataset, create_report=self._report_creator(user))

        def record(run: ToolRun) -> None:
            self._record_run(message_id, user, run)

        try:
            if demo_id:
                result = run_demo(DEMO_QUESTIONS[demo_id], ctx, page_filters, record)
            else:
                agent = Agent(
                    self.provider, ctx, max_tool_calls=self.settings.ai_max_tool_calls, on_tool_run=record
                )
                result = agent.ask(question, history=history, page_filters=page_filters)
        except Exception:  # never leave an answer "running" forever
            log.exception("ai answer failed", extra={"message_id": str(message_id)})
            result = AgentResult(
                "failed", "", [], [], None, [], _empty(), None, error="the analyst hit an internal error"
            )
        self._finish(message_id, result)

    def _report_creator(self, user: Actor) -> Any:
        def create(
            *, template: str, title: str | None, filters: AnalyticsFilters, sections: list[str] | None
        ) -> dict[str, Any]:
            with self.sessions() as session, session.begin():
                report = reports.create_report(
                    session,
                    template=template,
                    title=title,
                    filters=filters,
                    sections=sections,
                    visibility="private",
                    user=user,
                )
                record_audit(
                    session,
                    action="report.created",
                    outcome="success",
                    actor_user_id=user.id,
                    target_type="report",
                    target_id=str(report.id),
                    details={"template": template, "visibility": "private", "source": "ai_analyst"},
                )
                return {"report_id": str(report.id), "title": report.title}

        return create

    def _record_run(self, message_id: uuid.UUID, user: Actor, run: ToolRun) -> None:
        output = run.output
        with self.sessions() as session, session.begin():
            session.add(
                AIToolRun(
                    message_id=message_id,
                    user_id=user.id,
                    tool=run.tool,
                    arguments=run.arguments,
                    status=run.status,
                    duration_ms=run.duration_ms,
                    error=run.error,
                    result=_json(output.data) if output else None,
                    meta=_json(output.meta) if output else None,
                    chart=_json(output.chart) if output and output.chart else None,
                )
            )
        log.info("ai tool run", extra={"tool": run.tool, "status": run.status, "ms": run.duration_ms})

    def _finish(self, message_id: uuid.UUID, result: AgentResult) -> None:
        with self.sessions() as session, session.begin():
            message = session.get(AIMessage, message_id)
            if message is None:  # the conversation was deleted meanwhile
                return
            message.status = result.status
            message.content = (
                result.clarification if result.status == "clarification" else result.answer
            ) or ""
            message.payload = _json(result.payload())
            message.finished_at = _now()

    # ---- AI-assisted reports (template 6) -----------------------------------------------------------------

    def _require_author(self, user: Actor) -> None:
        if user.role not in (Role.ADMIN, Role.ANALYST):
            raise PermissionDeniedError("only analysts and administrators create reports")
        if not self.provider.enabled:
            raise AIUnavailableError(self.provider.reason or "the AI analyst is not configured")

    def outline(self, user: Actor, *, filters: AnalyticsFilters, focus: str) -> dict[str, Any]:
        self._require_author(user)
        coverage = self.analytics.coverage(filters.dataset_id)
        span = (
            f"{coverage.dataset_name}, {coverage.start} to {coverage.end}" if coverage.start else "none yet"
        )
        applied = {k: v for k, v in filters.applied().items() if k != "dataset_id"}
        scope = ", ".join(f"{k}={v}" for k, v in applied.items()) or "all published data, no filters"
        result = propose_outline(self.provider, coverage=span, scope=scope, focus=focus)
        library = TEMPLATES[NARRATIVE_TEMPLATE].sections
        return {
            "title": result.title,
            "sections": result.sections,
            "rationale": result.rationale,
            "library": [{"id": s.id, "title": s.title, "description": s.description} for s in library],
            "model": self.provider.model,
        }

    def draft_report(
        self,
        user: Actor,
        *,
        title: str | None,
        filters: AnalyticsFilters,
        sections: list[str],
        focus: str,
        visibility: str,
    ) -> dict[str, str]:
        self._require_author(user)
        with self.sessions() as session, session.begin():
            report = reports.create_report(
                session,
                template=NARRATIVE_TEMPLATE,
                title=title,
                filters=filters,
                sections=sections,
                visibility=visibility,
                user=user,
            )
            report.narrative = {
                "status": "drafting",
                "focus": focus,
                "basis": fingerprint(report.template, report.filters, report.sections),
            }
            record_audit(
                session,
                action="report.created",
                outcome="success",
                actor_user_id=user.id,
                target_type="report",
                target_id=str(report.id),
                details={
                    "template": NARRATIVE_TEMPLATE,
                    "visibility": visibility,
                    "source": "ai_report_draft",
                },
            )
            report_id = report.id
        self.executor.submit(self._draft, report_id, focus)
        return {"report_id": str(report_id)}

    def redraft(self, user: Actor, report_id: uuid.UUID, focus: str | None) -> dict[str, str]:
        self._require_author(user)
        with self.sessions() as session, session.begin():
            report = reports.get_report(session, report_id, user)
            reports.require(reports.can_edit(report, user), "redraft")
            if report.template != NARRATIVE_TEMPLATE:
                raise ConflictError("only AI-assisted reports (template 6) have an AI narrative")
            if (report.narrative or {}).get("status") == "drafting":
                raise ConflictError("the narrative is already being drafted")
            keep = focus if focus is not None else (report.narrative or {}).get("focus", "")
            report.narrative = {
                "status": "drafting",
                "focus": keep,
                "basis": fingerprint(report.template, report.filters, report.sections),
            }
        self.executor.submit(self._draft, report_id, keep)
        return {"report_id": str(report_id)}

    def _draft(self, report_id: uuid.UUID, focus: str) -> None:
        with self.sessions() as session:
            report = session.get(Report, report_id)
            if report is None:
                return
            template, title, sections = report.template, report.title, list(report.sections)
            filters, prepared_by = reports.report_filters(report), report.creator.display_name
            basis = fingerprint(report.template, report.filters, report.sections)
        try:
            doc = ReportBuilder(self.analytics).build(
                template=template, title=title, filters=filters, sections=sections, prepared_by=prepared_by
            )
            narrative = draft_narrative(self.provider, doc, basis=basis, focus=focus)
        except TripScopeError as exc:
            narrative = {"status": "failed", "error": exc.message, "basis": basis, "focus": focus}
        except Exception:
            log.exception("report narrative failed", extra={"report_id": str(report_id)})
            narrative = {"status": "failed", "error": "internal error", "basis": basis, "focus": focus}
        with self.sessions() as session, session.begin():
            report = session.get(Report, report_id)
            if report is None:
                return
            report.narrative = narrative
            record_audit(
                session,
                action="report.ai_drafted",
                outcome="success" if narrative["status"] == "ready" else "failure",
                actor_user_id=report.created_by,
                target_type="report",
                target_id=str(report_id),
                details={
                    k: narrative.get(k) for k in ("status", "model", "figures", "figures_verified", "error")
                }
                | {"dropped": len(narrative.get("dropped", []))},
            )

    # ---- reading ---------------------------------------------------------------------------------------

    def _owned(self, session: Session, conversation_id: uuid.UUID, user: Actor) -> AIConversation:
        conversation = session.scalar(
            select(AIConversation)
            .where(AIConversation.id == conversation_id)
            .options(selectinload(AIConversation.messages).selectinload(AIMessage.tool_runs))
        )
        if conversation is None or conversation.user_id != user.id:
            raise NotFoundError("conversation not found")  # private to its owner, whatever the role
        return conversation

    def _fail_stale(self, conversation: AIConversation) -> None:
        for message in conversation.messages:
            if message.status == "running" and message.created_at < _now() - self.stale_after:
                message.status, message.finished_at = "failed", _now()
                message.payload = {"error": "the answer took too long or the server restarted; ask again"}

    def _purge(self, session: Session) -> None:
        cutoff = _now() - timedelta(days=self.settings.ai_retention_days)
        session.execute(delete(AIConversation).where(AIConversation.updated_at < cutoff))

    def conversations(self, user: Actor, limit: int = 50) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.scalars(
                select(AIConversation)
                .where(AIConversation.user_id == user.id)
                .order_by(AIConversation.updated_at.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "conversation_id": str(c.id),
                    "title": c.title,
                    "created_at": c.created_at,
                    "updated_at": c.updated_at,
                }
                for c in rows
            ]

    def conversation(self, user: Actor, conversation_id: uuid.UUID) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            conversation = self._owned(session, conversation_id, user)
            self._fail_stale(conversation)
            return {
                "conversation_id": str(conversation.id),
                "title": conversation.title,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
                "messages": [_message(m) for m in conversation.messages],
            }

    def delete(self, user: Actor, conversation_id: uuid.UUID) -> None:
        with self.sessions() as session, session.begin():
            session.delete(self._owned(session, conversation_id, user))


def _message(m: AIMessage) -> dict[str, Any]:
    return {
        "message_id": str(m.id),
        "role": m.role,
        "content": m.content,
        "status": m.status,
        "mode": m.mode,
        "provider": m.provider,
        "model": m.model,
        "page_filters": m.page_filters,
        "payload": m.payload or {},
        "created_at": m.created_at,
        "finished_at": m.finished_at,
        "tool_runs": [
            {
                "tool_run_id": str(r.id),
                "tool": r.tool,
                "arguments": r.arguments,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "error": r.error,
                "result": r.result,
                "meta": r.meta,
                "chart": r.chart,
            }
            for r in m.tool_runs
        ],
    }


def _json(value: Any) -> Any:
    """JSON-safe copy for JSONB columns (dates and decimals as text/float)."""
    import json

    return json.loads(json.dumps(value, default=str))


def _empty() -> Any:
    from tripscope.ai.verify import Verification

    return Verification([])
