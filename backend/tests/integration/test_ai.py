"""Phase 5 against the real services: AI status and role gates, demo answers grounded in the analytics API,
chat with a scripted model (history, privacy, stale answers, retention, ai_tool_runs), and AI-assisted reports
(outline, verified narrative, files, staleness after edits, redraft). No real model is needed."""

from __future__ import annotations

import io
import json
import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import text

from tests.integration.conftest import PASSWORD
from tests.unit.test_ai import Scripted, call, final
from tripscope.ai.provider import ChatResult
from tripscope.api.app import create_app
from tripscope.metadata.models import AIConversation, AIMessage
from tripscope.pipeline.runner import run_source
from tripscope.reports.worker import ReportWorker

pytestmark = [pytest.mark.integration, pytest.mark.spark]

JANUARY = {"start_date": "2025-01-01", "end_date": "2025-01-31"}


@pytest.fixture(scope="module")
def app(env: dict[str, Any]) -> FastAPI:
    return create_app(env["settings"].model_copy(update={"llm_provider": "disabled"}))


@pytest.fixture(scope="module")
def published(env: dict[str, Any]) -> Any:
    return run_source(env["deps"], "fixture-2025-01")


@pytest.fixture(scope="module")
def as_(app: FastAPI, published: Any) -> dict[str, TestClient]:
    clients = {}
    for name in ("admin", "analyst", "viewer"):
        client = TestClient(app)
        assert (
            client.post(
                "/api/v1/auth/login", json={"email": f"{name}@test.local", "password": PASSWORD}
            ).status_code
            == 200
        )
        clients[name] = client
    return clients


def wait_answer(client: TestClient, conversation_id: str, timeout: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        convo = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()
        if convo["messages"][-1]["status"] != "running":
            return dict(convo["messages"][-1])
        time.sleep(0.1)
    raise AssertionError("the answer did not finish")


def with_provider(app: FastAPI, provider: Any) -> None:
    app.state.ai.provider = provider


def test_status_and_role_gates(app: FastAPI, as_: dict[str, TestClient]) -> None:
    status = as_["analyst"].get("/api/v1/ai/status").json()
    assert status["enabled"] is False and status["can_chat"] is True and "LLM_PROVIDER" in status["reason"]
    assert [q["id"] for q in status["demo_questions"]][:2] == ["month_over_month", "top_pickup_zones"]
    assert as_["viewer"].get("/api/v1/ai/status").json()["can_chat"] is False
    assert as_["viewer"].post("/api/v1/ai/chat", json={"demo_id": "top_pickup_zones"}).status_code == 403
    response = as_["analyst"].post("/api/v1/ai/chat", json={"message": "How many trips?"})
    assert response.status_code == 503 and "demo question" in response.json()["error"]["message"]
    assert as_["analyst"].post("/api/v1/ai/chat", json={"message": "  "}).status_code == 422
    assert as_["analyst"].post("/api/v1/ai/chat", json={"demo_id": "nope"}).status_code == 404
    assert as_["analyst"].post("/api/v1/ai/chat", json={"message": "x", "sql": "1"}).status_code == 422


def test_viewers_can_chat_when_an_admin_enables_it(env: dict[str, Any], published: Any) -> None:
    open_app = create_app(
        env["settings"].model_copy(update={"ai_allow_viewers": True, "llm_provider": "disabled"})
    )
    viewer = TestClient(open_app)
    viewer.post("/api/v1/auth/login", json={"email": "viewer@test.local", "password": PASSWORD})
    response = viewer.post("/api/v1/ai/chat", json={"demo_id": "weekday_vs_weekend"})
    assert response.status_code == 202
    assert wait_answer(viewer, response.json()["conversation_id"])["status"] == "answered"


def test_demo_answers_equal_the_analytics_api(as_: dict[str, TestClient], env: dict[str, Any]) -> None:
    analyst = as_["analyst"]
    started = analyst.post("/api/v1/ai/chat", json={"demo_id": "top_pickup_zones", "filters": JANUARY}).json()
    answer = wait_answer(analyst, started["conversation_id"])
    assert answer["status"] == "answered" and answer["mode"] == "demo" and answer["model"] is None
    top = analyst.get("/api/v1/analytics/top-pickup-zones", params=JANUARY | {"limit": 5}).json()["groups"]
    run = answer["tool_runs"][0]
    assert run["tool"] == "get_top_zones" and run["status"] == "ok"
    assert run["arguments"] == {"start_date": "2025-01-01", "end_date": "2025-01-31", "limit": 5}
    assert [z["trips"] for z in run["result"]["zones"]] == [g["trips"] for g in top]
    assert run["meta"]["period"] == "Jan 1 – Jan 31, 2025" and run["meta"]["source_table"]
    assert f"{top[0]['trips']:,}" in answer["content"]
    verification = answer["payload"]["verification"]
    assert verification["total"] > 0 and verification["verified"] == verification["total"]
    with env["factory"]() as session:
        logged = session.execute(
            text("SELECT tool, status, arguments FROM ai_tool_runs WHERE message_id = :m"),
            {"m": answer["message_id"]},
        ).all()
    assert [(r[0], r[1]) for r in logged] == [("get_top_zones", "ok")] and logged[0][2]["limit"] == 5


def test_chat_with_a_model_keeps_history_and_privacy(app: FastAPI, as_: dict[str, TestClient]) -> None:
    analyst, admin = as_["analyst"], as_["admin"]
    total = analyst.get("/api/v1/analytics/overview", params=JANUARY).json()["kpis"]["total_trips"]["value"]
    provider = Scripted(
        [
            call("get_overview_metrics", **JANUARY),
            final(f"January had {total:,} trips.", follow_ups=["And by weekday?"]),
            ChatResult(json.dumps({"answer": "", "clarification": "Which weekday do you mean?"})),
        ]
    )
    original = app.state.ai.provider
    with_provider(app, provider)
    try:
        first = analyst.post(
            "/api/v1/ai/chat", json={"message": "How many trips in January?", "filters": JANUARY}
        ).json()
        answer = wait_answer(analyst, first["conversation_id"])
        assert answer["status"] == "answered" and answer["model"] == "test-model"
        verification = answer["payload"]["verification"]
        assert verification["verified"] == verification["total"]
        assert answer["payload"]["follow_ups"] == ["And by weekday?"]
        second = analyst.post(
            "/api/v1/ai/chat",
            json={"message": "And on the busiest one?", "conversation_id": first["conversation_id"]},
        ).json()
        clarification = wait_answer(analyst, second["conversation_id"])
        assert (
            clarification["status"] == "clarification"
            and clarification["content"] == "Which weekday do you mean?"
        )
        history = provider.sent[-1]
        assert [m["role"] for m in history[1:4]] == ["user", "assistant", "user"]
        assert history[2]["content"] == f"January had {total:,} trips."
        assert (
            "start_date=2025-01-01" in provider.sent[0][0]["content"]
        )  # dashboard filters offered as context
    finally:
        with_provider(app, original)
    # Conversations are private, whatever the role.
    assert admin.get(f"/api/v1/ai/conversations/{first['conversation_id']}").status_code == 404
    listed = [c["conversation_id"] for c in analyst.get("/api/v1/ai/conversations").json()["conversations"]]
    assert first["conversation_id"] in listed
    assert analyst.delete(f"/api/v1/ai/conversations/{first['conversation_id']}").status_code == 204
    assert analyst.get(f"/api/v1/ai/conversations/{first['conversation_id']}").status_code == 404


def test_stale_answers_fail_and_old_conversations_expire(
    app: FastAPI, as_: dict[str, TestClient], env: dict[str, Any]
) -> None:
    analyst = as_["analyst"]
    started = analyst.post("/api/v1/ai/chat", json={"demo_id": "distance_by_hour"}).json()
    wait_answer(analyst, started["conversation_id"])
    with env["factory"]() as session, session.begin():
        convo = session.get(AIConversation, uuid.UUID(started["conversation_id"]))
        assert convo is not None
        session.add(
            AIMessage(
                conversation=convo,
                role="assistant",
                status="running",
                content="",
                created_at=convo.created_at - timedelta(hours=3),
            )
        )
    assert (
        analyst.post(
            "/api/v1/ai/chat",
            json={"demo_id": "distance_by_hour", "conversation_id": started["conversation_id"]},
        ).status_code
        == 202
    )  # the lost answer no longer blocks the conversation
    messages = analyst.get(f"/api/v1/ai/conversations/{started['conversation_id']}").json()["messages"]
    assert any(m["status"] == "failed" and "server restarted" in m["payload"]["error"] for m in messages)

    with env["factory"]() as session, session.begin():
        session.execute(
            text("UPDATE ai_conversations SET updated_at = now() - interval '400 days' WHERE id = :id"),
            {"id": started["conversation_id"]},
        )
    analyst.post("/api/v1/ai/chat", json={"demo_id": "weekday_vs_weekend"})
    assert analyst.get(f"/api/v1/ai/conversations/{started['conversation_id']}").status_code == 404


def test_ai_assisted_report_outline_draft_files_and_staleness(
    app: FastAPI, as_: dict[str, TestClient], env: dict[str, Any]
) -> None:
    analyst = as_["analyst"]
    assert analyst.post("/api/v1/ai/report-outline", json={"filters": JANUARY}).status_code == 503  # disabled
    overview = analyst.get("/api/v1/analytics/overview", params=JANUARY).json()["kpis"]
    total = overview["total_trips"]["value"]
    narrative = {
        "summary": [f"January had {total:,} trips.", "A made-up claim of 98,765 trips."],
        "findings": [
            {"statement": f"Trips totalled {total:,}.", "based_on": "kpi:total_trips", "kind": "descriptive"},
            {
                "statement": "Busy days may follow holidays.",
                "based_on": "finding:trend-1",
                "kind": "hypothesis",
            },
            {"statement": "Unsupported.", "based_on": "finding:nope", "kind": "descriptive"},
        ],
        "recommendations": ["Compare weekday patterns.", "Expect 5,000 more trips."],
    }
    provider = Scripted(
        [
            ChatResult(
                json.dumps(
                    {"title": "January review", "sections": ["trend", "weekday", "bogus"], "rationale": "r"}
                )
            ),
            ChatResult(json.dumps(narrative)),
            ChatResult(
                json.dumps(narrative)
            ),  # the correction round repeats the fabricated sentence; it is dropped
            ChatResult(
                json.dumps(
                    {"summary": [f"January had {total:,} trips."], "findings": [], "recommendations": []}
                )
            ),
        ]
    )
    original = app.state.ai.provider
    with_provider(app, provider)
    try:
        _report_flow(app, as_, env, provider, total)
    finally:
        with_provider(app, original)


def _report_flow(
    app: FastAPI, as_: dict[str, TestClient], env: dict[str, Any], provider: Any, total: int
) -> None:
    analyst = as_["analyst"]
    outline = analyst.post("/api/v1/ai/report-outline", json={"filters": JANUARY, "focus": "demand"}).json()
    assert outline["title"] == "January review" and outline["sections"] == ["trend", "weekday"]
    assert len(outline["library"]) == 15
    assert as_["viewer"].post("/api/v1/ai/report-outline", json={}).status_code == 403

    rid = analyst.post(
        "/api/v1/ai/report-draft",
        json={"title": outline["title"], "filters": JANUARY, "sections": outline["sections"]},
    ).json()["report_id"]
    deadline = time.monotonic() + 15
    while analyst.get(f"/api/v1/reports/{rid}").json()["narrative"]["status"] == "drafting":
        assert time.monotonic() < deadline
        time.sleep(0.1)
    detail = analyst.get(f"/api/v1/reports/{rid}").json()
    assert detail["template"] == "custom_ai" and detail["narrative"]["status"] == "ready"
    assert detail["narrative"]["dropped"] == 2  # the fabricated summary sentence and the unknown reference
    doc = analyst.get(f"/api/v1/reports/{rid}/preview").json()
    assert doc["summary"] == [f"January had {total:,} trips."]
    assert [f["kind"] for f in doc["findings"]] == ["descriptive", "hypothesis"]
    assert (
        doc["findings"][0]["evidence"]["values"]["value"] == total
    )  # evidence copied from the KPI, not the model
    assert doc["recommendations"] == ["Compare weekday patterns."]  # recommendations carry no figures
    assert (
        doc["narrative"]["source"] == "ai"
        and doc["narrative"]["figures_verified"] == doc["narrative"]["figures"]
    )

    run_id = analyst.post(f"/api/v1/reports/{rid}/generate", json={"format": "pdf"}).json()["run_id"]
    worker = ReportWorker(
        session_factory=env["factory"], analytics=app.state.analytics, store_factory=lambda: env["store"]
    )
    while worker.run_once() is not None:
        pass
    pdf = analyst.get(f"/api/v1/reports/{rid}/download?run_id={run_id}")
    pdf_text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert (
        "drafted by test-model" in pdf_text
        and "(hypothesis)" in pdf_text
        and "Suggested next steps" in pdf_text
    )

    # Changing sections makes the narrative stale: it is withheld, not reused.
    analyst.patch(f"/api/v1/reports/{rid}", json={"sections": ["trend"]})
    assert analyst.get(f"/api/v1/reports/{rid}").json()["narrative"]["status"] == "stale"
    stale = analyst.get(f"/api/v1/reports/{rid}/preview").json()
    assert stale["narrative"]["source"] == "rules" and stale["recommendations"] == []
    assert any("other filters or sections" in line for line in stale["limitations"])
    assert analyst.post(f"/api/v1/ai/reports/{rid}/redraft", json={}).status_code == 202
    deadline = time.monotonic() + 15
    while analyst.get(f"/api/v1/reports/{rid}").json()["narrative"]["status"] == "drafting":
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert analyst.get(f"/api/v1/reports/{rid}").json()["narrative"]["status"] == "ready"
    with env["factory"]() as session:
        drafted = session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'report.ai_drafted' AND target_id = :t"),
            {"t": rid},
        ).scalar_one()
    assert drafted == 2
