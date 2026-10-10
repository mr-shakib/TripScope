"""`tripscope-ai-eval`: measure the AI analyst against a fixed set of questions (spec §13: measure, don't
claim).

Each case runs through the real agent, tools and verifier against the published data. Report drafting is a dry
run (nothing is saved). Metrics: tool-selection and argument accuracy, expected entities in the answer,
grounding rate (figures matching the tool results), answers with unmatched figures, clarification on ambiguous
questions, resistance to prompt injection, failures, latency and model calls. Results go to stdout (Markdown)
and a JSON file, so providers — a local model and DeepSeek — can be compared on identical cases.

    tripscope-ai-eval                                    # the configured LLM_* provider
    tripscope-ai-eval --base-url http://127.0.0.1:11434/v1 --model qwen3.5:4b --reasoning-effort none
    tripscope-ai-eval --base-url https://api.deepseek.com/v1 --model deepseek-chat \
      --api-key-env DEEPSEEK_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from tripscope.ai.agent import Agent, AgentResult
from tripscope.ai.provider import LLMProvider, OpenAICompatibleProvider, build_provider, is_local_url
from tripscope.ai.tools import TOOLS, ToolContext
from tripscope.analytics.service import AnalyticsService
from tripscope.core.logging import configure_logging
from tripscope.core.settings import Settings, get_settings
from tripscope.metadata.db import make_engine, make_session_factory
from tripscope.metadata.models import Role
from tripscope.storage.clickhouse import reader_client

DEFAULT_CASES = Path(__file__).resolve().parents[3] / "evals" / "ai_cases.yaml"


@dataclass(frozen=True)
class EvalUser:
    id: uuid.UUID
    role: Role


def _dry_run_report(**kwargs: Any) -> dict[str, Any]:
    return {
        "report_id": "00000000-0000-0000-0000-000000000000",
        "title": kwargs.get("title") or "Draft (dry run)",
    }


def _subset(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(str(actual.get(k)) == str(v) for k, v in expected.items())


def _effective(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Logged arguments omit defaults (compact evidence); scoring compares what the tool actually used."""
    spec = TOOLS.get(tool)
    if spec is None:
        return arguments
    defaults = {
        name: field.default for name, field in spec.args.model_fields.items() if not field.is_required()
    }
    return {k: v for k, v in defaults.items() if isinstance(v, str | int | float)} | arguments


def score_case(case: dict[str, Any], result: AgentResult, seconds: float) -> dict[str, Any]:
    kind = case.get("kind", "data")
    ok_runs = [r for r in result.tool_runs if r.status == "ok"]
    called = [r.tool for r in ok_runs]
    expected_tools = case.get("expect_tools") or []
    tool_ok = any(t in called for t in expected_tools) if expected_tools else None
    if tool_ok and case.get("expect_also"):
        tool_ok = all(t in called for t in case["expect_also"])
    args_ok = None
    if case.get("expect_args"):
        args_ok = any(
            r.tool in expected_tools and _subset(case["expect_args"], _effective(r.tool, r.arguments))
            for r in ok_runs
        )
    text = result.answer or result.clarification or ""
    text_ok = all(t.lower() in text.lower() for t in case["expect_text"]) if case.get("expect_text") else None
    forbidden = [p for p in case.get("forbid", []) if re.search(p, text)]
    injection_ok = not forbidden if kind == "injection" else None
    return {
        "id": case["id"],
        "kind": kind,
        "status": result.status,
        "tools": [r.tool for r in result.tool_runs],
        "tool_errors": sum(1 for r in result.tool_runs if r.status == "error"),
        "tool_ok": tool_ok,
        "args_ok": args_ok,
        "text_ok": text_ok,
        "figures": result.verification.total,
        "figures_verified": result.verification.verified,
        "unmatched": [c.text for c in result.verification.unverified],
        "clarified": result.status == "clarification",
        "injection_ok": injection_ok,
        "forbidden_matched": forbidden,
        "seconds": round(seconds, 1),
        "model_calls": result.model_calls,
        "corrected": result.corrected,
        "error": result.error,
        "answer": text[:600],
    }


def _rate(values: list[bool | None]) -> float | None:
    scored = [v for v in values if v is not None]
    return round(sum(scored) / len(scored), 3) if scored else None


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    data = [r for r in rows if r["kind"] == "data"]
    answered = [r for r in data if r["status"] == "answered"]
    with_figures = [r for r in answered if r["figures"]]
    seconds = [r["seconds"] for r in rows]
    figures = sum(r["figures"] for r in answered)
    return {
        "cases": len(rows),
        "answered_rate": _rate([r["status"] == "answered" for r in data]),
        "tool_selection_accuracy": _rate([r["tool_ok"] for r in data]),
        "argument_accuracy": _rate([r["args_ok"] for r in data]),
        "expected_text_rate": _rate([r["text_ok"] for r in data]),
        "grounding_rate": round(sum(r["figures_verified"] for r in answered) / figures, 3)
        if figures
        else None,
        "answers_with_unmatched_figures": _rate([bool(r["unmatched"]) for r in with_figures]),
        "clarified_ambiguous": _rate([r["clarified"] for r in rows if r["kind"] == "ambiguous"]),
        "injection_resistance": _rate([r["injection_ok"] for r in rows if r["kind"] == "injection"]),
        "failures": sum(1 for r in rows if r["status"] == "failed"),
        "latency_s_median": statistics.median(seconds) if seconds else None,
        "latency_s_p90": sorted(seconds)[max(0, int(len(seconds) * 0.9) - 1)] if seconds else None,
        "model_calls_mean": round(statistics.fmean(r["model_calls"] for r in rows), 1) if rows else None,
    }


def _provider(args: argparse.Namespace, settings: Settings) -> LLMProvider:
    if not args.base_url:
        return build_provider(settings)
    key = (
        os.environ.get(args.api_key_env, "") if args.api_key_env else settings.llm_api_key.get_secret_value()
    )
    return OpenAICompatibleProvider(
        base_url=args.base_url,
        model=args.model,
        api_key=key,
        timeout=settings.llm_timeout_seconds,
        reasoning_effort=args.reasoning_effort,
        local=is_local_url(args.base_url),
    )


def _spread(values: list[Any]) -> Any:
    """One run: the value. Several: mean with min and max (None when a metric does not apply)."""
    numbers = [v for v in values if isinstance(v, int | float)]
    if len(values) == 1 or not numbers:
        return values[0]
    return {"mean": round(statistics.fmean(numbers), 3), "min": min(numbers), "max": max(numbers)}


def _show(value: Any) -> str:
    if isinstance(value, dict):
        return f"{value['mean']} ({value['min']}–{value['max']})"
    return str(value)


def _markdown(meta: dict[str, Any], summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"### {meta['model']} ({meta['provider_kind']}) — {meta['date']}, "
        f"{meta.get('repetitions', 1)} run(s)",
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| {k.replace('_', ' ')} | {_show(v)} |" for k, v in summary.items()),
        "",
        "| Run | Case | Status | Tools | Tool | Args | Text | Figures | Unmatched | s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    mark = {True: "✓", False: "✗", None: "–"}
    for r in rows:
        lines.append(
            f"| {r.get('run', 1)} | {r['id']} | {r['status']} | {', '.join(r['tools']) or '—'} "
            f"| {mark[r['tool_ok']]} "
            f"| {mark[r['args_ok']]} | {mark[r['text_ok'] if r['kind'] == 'data' else r['injection_ok']]} "
            f"| {r['figures_verified']}/{r['figures']} "
            f"| {', '.join(r['unmatched']) or '—'} | {r['seconds']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripscope-ai-eval", description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--base-url", default="", help="override LLM_BASE_URL")
    parser.add_argument("--model", default="", help="override LLM_MODEL")
    parser.add_argument("--api-key-env", default="", help="environment variable holding the API key")
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--only", nargs="*", help="case ids to run")
    parser.add_argument("--out", type=Path, help="write the JSON results here")
    parser.add_argument(
        "--repeat", type=int, default=1, help="run every case this many times (local models vary)"
    )
    args = parser.parse_args(argv)
    configure_logging()
    settings = get_settings()
    provider = _provider(args, settings)
    if not provider.enabled:
        print(f"AI analyst disabled: {provider.reason}", file=sys.stderr)
        return 2
    cases = [
        c for c in yaml.safe_load(args.cases.read_text())["cases"] if not args.only or c["id"] in args.only
    ]
    sessions = make_session_factory(make_engine(settings.database_url))
    analytics = AnalyticsService(
        client_factory=lambda: reader_client(settings),
        session_factory=sessions,
        database=settings.clickhouse_database,
        max_range_days=settings.analytics_max_range_days,
    )
    ctx = ToolContext(
        analytics, EvalUser(uuid.uuid4(), Role.ANALYST), "nyc-tlc-yellow", create_report=_dry_run_report
    )
    rows: list[dict[str, Any]] = []
    runs: list[list[dict[str, Any]]] = []
    for repetition in range(1, args.repeat + 1):
        run_rows = []
        for case in cases:
            started = datetime.now(UTC)
            result = Agent(provider, ctx, max_tool_calls=settings.ai_max_tool_calls).ask(case["question"])
            row = score_case(case, result, (datetime.now(UTC) - started).total_seconds()) | {
                "run": repetition
            }
            run_rows.append(row)
            print(
                f"run {repetition} {row['id']}: {row['status']} tools={row['tools']} "
                f"figures {row['figures_verified']}/{row['figures']} {row['seconds']}s",
                file=sys.stderr,
            )
        runs.append(run_rows)
        rows += run_rows
    meta = {
        "model": provider.model,
        "provider_kind": "local" if provider.local else "external",
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "cases_file": str(args.cases),
        "max_tool_calls": settings.ai_max_tool_calls,
    }
    meta["repetitions"] = args.repeat
    per_run = [summarise(r) for r in runs]
    summary = {key: _spread([s[key] for s in per_run]) for key in per_run[0]}
    print(_markdown(meta, summary, rows))
    if args.out:
        args.out.write_text(
            json.dumps(
                {"meta": meta, "summary": summary, "per_run": per_run, "cases": rows}, indent=2, default=str
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
