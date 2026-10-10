"use client";

import { AlertTriangle, BadgeCheck, ChevronDown, CircleX, Database, FileText, LoaderCircle, Wrench } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import type { AIChart, AIClaim, AIMessage, AIToolRun, ReportChartBlock } from "@/api/types";
import { ReportChart } from "@/components/reports/report-chart";
import { Tooltip } from "@/components/ui/overlays";
import { Pill } from "@/components/ui/status";
import { cn } from "@/lib/cn";
import { formatDuration, MISSING } from "@/lib/format";

export const TOOL_LABELS: Record<string, string> = {
  get_overview_metrics: "Overview metrics",
  get_time_series: "Time series",
  compare_periods: "Period comparison",
  get_top_zones: "Top zones",
  get_breakdown: "Breakdown",
  get_distribution: "Distribution",
  get_data_quality_summary: "Data quality summary",
  run_anomaly_analysis: "Unusual days",
  create_chart_spec: "Chart",
  create_report_draft: "Report draft",
  find_zones: "Zone lookup",
};

const toolLabel = (tool: string) => TOOL_LABELS[tool] ?? tool;

/** The answer with every figure marked: matched against the tool results, or not. */
export function AnswerText({ text, claims }: { text: string; claims: AIClaim[] }) {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  [...claims]
    .sort((a, b) => a.start - b.start)
    .forEach((claim, i) => {
      const start = Math.max(claim.start, cursor);
      if (start > cursor) parts.push(text.slice(cursor, start));
      const shown = text.slice(start, claim.end);
      parts.push(
        <Tooltip key={i} content={claim.verified ? "Matches a value in the tool results" : "Not found in the tool results; treat with care"}>
          <span
            data-verified={claim.verified}
            className={cn(
              "rounded-sm px-0.5 tabular",
              claim.verified ? "underline decoration-good/60 decoration-2 underline-offset-[3px]" : "bg-warning-soft text-warning-ink",
            )}
          >
            {shown}
          </span>
        </Tooltip>,
      );
      cursor = claim.end;
    });
  parts.push(text.slice(cursor));
  return <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-ink" data-testid="ai-answer-text">{parts}</p>;
}

export function VerificationBadge({ total, verified }: { total: number; verified: number }) {
  if (!total) return <Pill>No figures to check</Pill>;
  const all = verified === total;
  return (
    <span data-testid="ai-verification" data-total={total} data-verified={verified}>
      <Pill tone={all ? "good" : "warning"}>
        {all ? <BadgeCheck className="size-3" /> : <AlertTriangle className="size-3" />}
        {verified} of {total} figures match the tool results
      </Pill>
    </span>
  );
}

export function ToolSteps({ runs, running }: { runs: AIToolRun[]; running: boolean }) {
  return (
    <ol className="space-y-1" data-testid="ai-steps">
      {runs.map((run) => (
        <li key={run.tool_run_id} className="flex items-center gap-2 text-[12.5px]">
          {run.status === "ok" ? <Wrench className="size-3.5 text-good-ink" /> : <CircleX className="size-3.5 text-critical-ink" />}
          <span className="font-medium text-ink">{toolLabel(run.tool)}</span>
          <span className="text-ink-muted">{formatDuration(run.duration_ms / 1000)}</span>
          {run.status === "error" ? <span className="truncate text-critical-ink">{run.error}</span> : null}
        </li>
      ))}
      {running ? (
        <li className="flex items-center gap-2 text-[12.5px] text-ink-2">
          <LoaderCircle className="size-3.5 animate-spin" /> {runs.length ? "Working with the results…" : "Choosing tools…"}
        </li>
      ) : null}
    </ol>
  );
}

function cell(key: string, value: unknown): string {
  if (value === null || value === undefined) return MISSING;
  if (typeof value === "number") {
    if (/share|change/.test(key)) return `${(value * 100).toFixed(2)}%`;
    return Number.isInteger(value) ? value.toLocaleString("en-US") : value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const ROW_KEYS = ["zones", "groups", "points", "unusual_days", "buckets"];

/** A compact table of a tool's (bounded) result. */
function ResultTable({ result }: { result: Record<string, unknown> }) {
  const listKey = ROW_KEYS.find((k) => Array.isArray(result[k]));
  let columns: string[] = [];
  let rows: Record<string, unknown>[] = [];
  if (listKey) {
    rows = (result[listKey] as Record<string, unknown>[]).slice(0, 12);
    columns = Object.keys(rows[0] ?? {}).filter((k) => rows.some((r) => typeof r[k] !== "object" || r[k] === null));
    // "value" repeats "trips" when the metric is the trip count; show it once.
    if (columns.includes("value") && columns.includes("trips") && rows.every((r) => r.value === r.trips)) {
      columns = columns.filter((c) => c !== "value");
    }
  } else if (result.kpis && typeof result.kpis === "object") {
    columns = ["metric", "value", "excluded_rows"];
    rows = Object.entries(result.kpis as Record<string, { value: number | null; excluded_rows: number }>).map(([metric, k]) => ({ metric, value: k.value, excluded_rows: k.excluded_rows }));
  } else if (result.metrics && typeof result.metrics === "object") {
    columns = ["metric", "period_a", "period_b", "change_b_vs_a"];
    rows = Object.entries(result.metrics as Record<string, Record<string, unknown>>).map(([metric, v]) => ({ metric, ...v }));
  } else {
    columns = ["field", "value"];
    rows = Object.entries(result).map(([field, value]) => ({ field, value }));
  }
  const total = listKey ? (result[listKey] as unknown[]).length : rows.length;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11.5px]">
        <thead>
          <tr className="border-b border-line text-left text-ink-muted">
            {columns.map((c) => (
              <th key={c} className="px-1.5 py-1 font-medium">{c.replaceAll("_", " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-line/70 last:border-0">
              {columns.map((c) => (
                <td key={c} className={cn("px-1.5 py-1", typeof row[c] === "number" && "text-right tabular")}>{cell(c, row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {total > rows.length ? <p className="mt-1 text-[11px] text-ink-muted">{total - rows.length} more rows in the result</p> : null}
    </div>
  );
}

export function Evidence({ runs }: { runs: AIToolRun[] }) {
  const [open, setOpen] = useState(false);
  if (!runs.length) return null;
  return (
    <div className="rounded-lg border border-line bg-surface-2" data-testid="ai-evidence">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px] font-medium text-ink-2 hover:text-ink">
        <Database className="size-3.5" /> Evidence · {runs.length} tool run{runs.length === 1 ? "" : "s"}
        <ChevronDown className={cn("ml-auto size-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <ul className="space-y-3 border-t border-line px-3 py-3">
          {runs.map((run) => (
            <li key={run.tool_run_id} className="space-y-1.5" data-testid={`evidence-${run.tool}`}>
              <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                <span className="font-semibold text-ink">{toolLabel(run.tool)}</span>
                <code className="rounded bg-surface-3 px-1 text-[11px] text-ink-2">{run.tool}</code>
                <span className="text-ink-muted">{formatDuration(run.duration_ms / 1000)}</span>
                {run.meta?.source_table ? <Pill>{run.meta.source_table}</Pill> : null}
              </div>
              {run.status === "error" ? (
                <p className="text-[12px] text-critical-ink">Failed: {run.error}</p>
              ) : (
                <>
                  <p className="text-[11.5px] text-ink-2">
                    {run.meta?.period ? <>Period: {run.meta.period}. </> : null}
                    Arguments:{" "}
                    {Object.entries(run.arguments).length
                      ? Object.entries(run.arguments).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`).join("; ")
                      : "defaults"}
                  </p>
                  {run.result && "report_id" in run.result ? (
                    <Link href={`/reports/${String(run.result.report_id)}`} className="inline-flex items-center gap-1 text-[12.5px] font-medium text-accent-strong hover:underline">
                      <FileText className="size-3.5" /> Open the saved report
                    </Link>
                  ) : run.result ? (
                    <ResultTable result={run.result} />
                  ) : null}
                  {run.meta?.metrics.length ? (
                    <ul className="text-[11px] text-ink-muted">
                      {run.meta.metrics.map((m) => (
                        <li key={m.id}>
                          <span className="font-medium text-ink-2">{m.label}:</span> {m.description} {m.rows_included}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** An AI chart spec rendered through the report chart component (allowlisted kinds only). */
export function AIChartView({ chart }: { chart: AIChart }) {
  const block: ReportChartBlock = {
    type: "chart",
    id: "ai-chart",
    title: chart.title,
    kind: chart.type === "line" ? "line" : chart.type === "bar" ? "bar" : "column",
    unit: chart.unit === "count" ? "trips" : chart.unit,
    categories: chart.categories,
    series: chart.series,
    rows: [],
    matrix: [],
    markers: [],
    note: null,
  };
  return (
    <figure className="rounded-lg border border-line bg-surface p-3" data-testid="ai-chart">
      <figcaption className="mb-1 text-[12.5px] font-medium text-ink">{chart.title}</figcaption>
      <ReportChart block={block} />
    </figure>
  );
}

export function messageSummary(message: AIMessage): string {
  const p = message.payload;
  const bits = [
    message.mode === "demo" ? "demo answer" : message.model,
    p.latency_ms ? formatDuration(p.latency_ms / 1000) : null,
    p.model_calls ? `${p.model_calls} model call${p.model_calls === 1 ? "" : "s"}` : null,
    p.corrected ? "figures corrected once" : null,
  ];
  return bits.filter(Boolean).join(" · ");
}
