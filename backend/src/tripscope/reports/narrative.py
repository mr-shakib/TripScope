"""AI-assisted reports (template 6, FR-11): outline, narrative drafting and verification, and applying the
narrative to a report document.

The model never writes tables or values. It proposes sections from a fixed library, then drafts sentences from
the report's own KPIs and findings; each finding must cite the KPI or rule-based finding it rests on (its
evidence is copied from there, not from the model). Every figure is checked against the document's values: one
retry with feedback, then sentences whose figures still do not match are removed and listed. The narrative is
tied to a fingerprint of the report's filters and sections, so it is withheld — not silently reused — after
they change.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tripscope.ai.agent import parse_json_object
from tripscope.ai.provider import LLMProvider
from tripscope.ai.verify import verify
from tripscope.core.errors import AIProviderError
from tripscope.reports.document import ChartBlock, Finding, NarrativeInfo, ReportDocument, TableBlock
from tripscope.reports.templates import TEMPLATES

TEMPLATE = "custom_ai"


def fingerprint(template: str, filters: dict[str, Any], sections: list[str]) -> str:
    """Identity of what a narrative was drafted for (the title may change freely)."""
    text = json.dumps({"t": template, "f": filters, "s": sections}, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---- outline ---------------------------------------------------------------------------------------------


class Outline(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=3, max_length=120)
    sections: list[str] = Field(min_length=2, max_length=8)
    rationale: str = Field(default="", max_length=600)


OUTLINE_PROMPT = """You plan reports for TripScope, an analytics platform for NYC TLC Yellow Taxi trip
records. Published data: {coverage}. The report covers: {scope}.
Choose 3 to 6 sections from this list only (use the ids exactly):
{library}
Reply with JSON only: {{"title": "a short report title", "sections": ["id", ...], "rationale": "one or two
sentences on why these sections answer the request"}}"""


def propose_outline(provider: LLMProvider, *, coverage: str, scope: str, focus: str) -> Outline:
    library = TEMPLATES[TEMPLATE].sections
    allowed = [s.id for s in library]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": OUTLINE_PROMPT.format(
                coverage=coverage,
                scope=scope,
                library="\n".join(f"- {s.id}: {s.title}. {s.description}" for s in library),
            ),
        },
        {"role": "user", "content": focus.strip() or "A general overview report for this period."},
    ]
    problem = ""
    for _attempt in (1, 2):
        if problem:
            messages.append({"role": "user", "content": f"{problem} Reply with the JSON outline only."})
        reply = provider.chat(messages, json_mode=True, max_tokens=500)
        parsed = parse_json_object(reply.content)
        messages.append({"role": "assistant", "content": reply.content})
        if parsed is None:
            problem = "That was not JSON."
            continue
        known = [s for s in dict.fromkeys(parsed.get("sections") or []) if s in allowed]
        unknown = [s for s in parsed.get("sections") or [] if s not in allowed]
        try:
            outline = Outline.model_validate(parsed | {"sections": known})
        except ValidationError:
            problem = f"Use 3 to 6 of these section ids: {', '.join(allowed)}; give a title."
            continue
        if unknown and len(known) < 2:
            problem = f"Unknown section ids {unknown}; use only: {', '.join(allowed)}."
            continue
        outline.sections = [s for s in allowed if s in outline.sections]  # report order
        return outline
    raise AIProviderError("the model did not return a usable outline; choose the sections yourself")


# ---- narrative -------------------------------------------------------------------------------------------


class DraftFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    statement: str = Field(min_length=5, max_length=600)
    based_on: str
    kind: Literal["descriptive", "hypothesis"] = "descriptive"


class Draft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: list[str] = Field(min_length=1, max_length=6)
    findings: list[DraftFinding] = Field(default_factory=list, max_length=8)
    recommendations: list[str] = Field(default_factory=list, max_length=4)


NARRATIVE_PROMPT = """You write the executive summary and key findings of a TripScope report about NYC TLC
Yellow Taxi trip records. Use only the facts below. Copy every number exactly as written in the facts; do not
compute, round differently, add up or invent numbers. Each finding must name the fact it rests on in
"based_on" (an id such as kpi:total_trips or finding:trend-1). Mark a statement "hypothesis" when it suggests
a possible reason, and phrase it as a possibility; never claim causes, and never call unusual records fraud.
Recommendations are next analysis steps without numbers.
Reply with JSON only: {{"summary": ["3 to 5 sentences"], "findings": [{{"statement": "...", "based_on":
"...", "kind": "descriptive"}}], "recommendations": ["..."]}}

Facts for "{title}" ({period}; filters: {filters}):
{facts}"""


def _facts(doc: ReportDocument) -> tuple[str, dict[str, Finding | None]]:
    lines: list[str] = []
    refs: dict[str, Finding | None] = {}
    for kpi in doc.kpis:
        change = (
            f"; previous period {kpi.previous_display}; change {kpi.change_display}"
            if kpi.change_display
            else ""
        )
        lines.append(f"kpi:{kpi.id} | {kpi.label}: {kpi.display}{change}")
        refs[f"kpi:{kpi.id}"] = None
    for finding in doc.findings:
        lines.append(f"finding:{finding.id} | {finding.statement}")
        refs[f"finding:{finding.id}"] = finding
    return "\n".join(lines), refs


def _numeric(values: Any) -> list[float]:
    return [float(v) for v in values if isinstance(v, int | float) and not isinstance(v, bool)]


def _document_numbers(doc: ReportDocument) -> tuple[list[float], list[list[float]]]:
    """Every value in the document, grouped by like quantity (a KPI's value and previous value, one finding's
    evidence, one table column, one chart series) for the verifier's derived comparisons."""
    groups: list[list[float]] = []
    extra: list[float] = []
    for kpi in doc.kpis:
        groups.append(_numeric([kpi.value, kpi.previous]))
        extra += _numeric([kpi.change])
    groups += [_numeric(f.evidence.values.values()) for f in doc.findings]
    for section in doc.sections:
        for block in section.blocks:
            if isinstance(block, TableBlock):
                groups += [_numeric(r[c] for r in block.rows) for c in range(len(block.columns))]
            elif isinstance(block, ChartBlock):
                groups += [_numeric(series.values) for series in block.series]
    return [v for g in groups for v in g] + extra, groups


def draft_narrative(
    provider: LLMProvider, doc: ReportDocument, *, basis: str, focus: str = ""
) -> dict[str, Any]:
    facts, refs = _facts(doc)
    filters = "; ".join(f"{i.label}: {i.value}" for i in doc.filters[1:]) or "none"
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": NARRATIVE_PROMPT.format(
                title=doc.title, period=doc.period.label, filters=filters, facts=facts
            ),
        },
        {"role": "user", "content": focus.strip() or "Write the summary and findings for a general reader."},
    ]
    values, groups = _document_numbers(doc)
    draft: Draft | None = None
    unverified: list[str] = []
    for _attempt in (1, 2):
        reply = provider.chat(messages, json_mode=True, max_tokens=1200)
        parsed = parse_json_object(reply.content)
        messages.append({"role": "assistant", "content": reply.content})
        try:
            draft = Draft.model_validate(parsed or {})
        except ValidationError:
            draft = None
            messages.append({"role": "user", "content": "Reply with the JSON object described above only."})
            continue
        unverified = [text for text in _sentences(draft) if verify(text, values, groups).unverified]
        missing = [f.based_on for f in draft.findings if f.based_on not in refs]
        if not unverified and not missing:
            break
        problems = []
        if unverified:
            problems.append(
                "These sentences have numbers that are not in the facts: " + " | ".join(unverified[:6])
            )
        if missing:
            problems.append(f"Unknown based_on ids: {missing[:6]}; use ids from the facts.")
        messages.append(
            {"role": "user", "content": " ".join(problems) + " Fix them and reply with the full JSON."}
        )
    if draft is None:
        raise AIProviderError("the model did not return a usable narrative; try again")
    checked_figures = verified_figures = 0
    dropped: list[str] = []

    def keep(text: str) -> bool:
        nonlocal checked_figures, verified_figures
        result = verify(text, values, groups)
        checked_figures += result.total
        verified_figures += result.verified
        if result.unverified:
            dropped.append(text)
            checked_figures -= result.total
            verified_figures -= result.verified
            return False
        return True

    summary = [s for s in draft.summary if keep(s)]
    findings = [
        {"statement": f.statement, "based_on": f.based_on, "kind": f.kind}
        for f in draft.findings
        if f.based_on in refs and keep(f.statement)
    ]
    dropped += [f.statement for f in draft.findings if f.based_on not in refs]
    recommendations = [r for r in draft.recommendations if not verify(r, [], []).claims]
    return {
        "status": "ready",
        "summary": summary,
        "findings": findings,
        "recommendations": recommendations,
        "dropped": dropped,
        "figures": checked_figures,
        "figures_verified": verified_figures,
        "model": provider.model,
        "provider": provider.name,
        "generated_at": datetime.now(UTC).isoformat(),
        "basis": basis,
        "focus": focus,
    }


def _sentences(draft: Draft) -> list[str]:
    return [*draft.summary, *(f.statement for f in draft.findings), *draft.recommendations]


# ---- applying --------------------------------------------------------------------------------------------


def apply_narrative(doc: ReportDocument, narrative: dict[str, Any] | None, basis: str) -> ReportDocument:
    """Swap the rule-based summary and findings for a current, verified AI narrative (template 6 only)."""
    if doc.template != TEMPLATE:
        return doc
    status = (narrative or {}).get("status", "none")
    if narrative and status == "ready" and narrative.get("basis") != basis:
        status = "stale"
    info = NarrativeInfo(
        source="ai" if status == "ready" else "rules",
        status=status,
        model=(narrative or {}).get("model"),
        provider=(narrative or {}).get("provider"),
        generated_at=(narrative or {}).get("generated_at"),
        figures=int((narrative or {}).get("figures", 0)),
        figures_verified=int((narrative or {}).get("figures_verified", 0)),
        dropped=list((narrative or {}).get("dropped", [])),
        error=(narrative or {}).get("error"),
    )
    if status != "ready" or narrative is None:
        note = {
            "none": "No AI narrative has been drafted for this report; the summary and findings are rules.",
            "drafting": "The AI narrative is still being drafted; the summary and findings are rule-based.",
            "stale": "The AI narrative was drafted for other filters or sections, so it is not shown.",
            "failed": "Drafting the AI narrative failed; the summary and findings are rule-based.",
        }[status]
        return doc.model_copy(update={"narrative": info, "limitations": [*doc.limitations, note]})
    by_id = {f"finding:{f.id}": f for f in doc.findings}
    kpis = {f"kpi:{k.id}": k for k in doc.kpis}
    findings = []
    for n, item in enumerate(narrative["findings"], start=1):
        source = by_id.get(item["based_on"])
        if source is not None:
            evidence, section, caveat = source.evidence, source.section, source.caveat
        else:
            kpi = kpis[item["based_on"]]
            evidence = source_kpi_evidence(kpi, doc)
            section, caveat = "kpis", kpi.note
        findings.append(
            Finding(
                id=f"ai-{n}",
                section=section,
                statement=item["statement"],
                kind=item["kind"],
                evidence=evidence,
                caveat=caveat,
            )
        )
    model = narrative.get("model") or "the configured model"
    dropped = len(info.dropped)
    method = (
        f"The executive summary, findings and recommendations were drafted by {model} from this report's own "
        f"KPIs and findings. Every figure in them was checked against the report's values "
        f"({info.figures_verified} of {info.figures} matched)"
        + (f"; {dropped} sentence(s) whose figures did not match were removed." if dropped else ".")
    )
    return doc.model_copy(
        update={
            "summary": narrative["summary"] or doc.summary,
            "findings": findings or doc.findings,
            "recommendations": narrative.get("recommendations", []),
            "narrative": info,
            "methodology": [*doc.methodology, method],
            "limitations": [
                *doc.limitations,
                "AI-drafted text can leave things out or stress some results over others; the tables and "
                "charts hold the authoritative figures. Statements marked hypothesis are possibilities, not "
                "findings.",
            ],
        }
    )


def source_kpi_evidence(kpi: Any, doc: ReportDocument) -> Any:
    from tripscope.reports.document import Evidence

    values: dict[str, Any] = {"value": kpi.value}
    if kpi.previous is not None:
        values |= {"previous": kpi.previous, "change": kpi.change}
    return Evidence(source=f"KPI: {kpi.label}", source_table=None, values=values)
