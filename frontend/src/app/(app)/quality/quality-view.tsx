"use client";

import { Check, GitCompareArrows, Minus, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { useDataset, useQuality, useSchemaReport } from "@/api/hooks";
import type { QualityResponse, SchemaReport } from "@/api/types";
import { EChart, tooltipBase } from "@/components/charts/echart";
import { PageHeader } from "@/components/layout/app-shell";
import { Card, CardHeader } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/overlays";
import { Segmented } from "@/components/ui/segmented";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { Pill } from "@/components/ui/status";
import { cn } from "@/lib/cn";
import { monthPresets } from "@/lib/filters";
import { escapeHtml, formatBucket, formatDuration, formatInteger, formatPercent, humanize } from "@/lib/format";
import { FLAGS, QUARANTINE_REASONS } from "@/lib/quality-labels";

function Stat({ label, value, hint, tone = "neutral" }: { label: string; value: string; hint: string; tone?: "neutral" | "good" | "warning" }) {
  return (
    <div className="rounded-2xl border border-line bg-surface px-5 py-4 shadow-card">
      <p className="text-xs font-medium text-ink-muted">{label}</p>
      <p className={cn("mt-1 text-2xl font-semibold tracking-tight tabular", tone === "good" ? "text-good-ink" : tone === "warning" ? "text-warning-ink" : "text-ink")}>{value}</p>
      <p className="mt-0.5 text-xs text-ink-muted">{hint}</p>
    </div>
  );
}

function RuleBars({ counts, labels, denominator, denominatorLabel, testId }: { counts: Record<string, number>; labels: Record<string, { label: string; description: string }>; denominator: number; denominatorLabel: string; testId: string }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, n]) => n), 1);
  return (
    <ul className="space-y-3" data-testid={testId}>
      {entries.map(([id, count]) => (
        <li key={id}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[13px] font-medium text-ink">{labels[id]?.label ?? humanize(id)}</span>
            <span className="shrink-0 tabular text-[13px]">
              <span className="font-semibold text-ink">{formatInteger(count)}</span>
              <span className="ml-2 text-xs text-ink-muted">{formatPercent(count, denominator)} of {denominatorLabel}</span>
            </span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3">
            <div className={cn("h-full rounded-full transition-[width] duration-500", count ? "bg-accent/75" : "bg-transparent")} style={{ width: `${(count / max) * 100}%` }} />
          </div>
          <p className="mt-1 text-xs text-ink-muted">{labels[id]?.description}</p>
        </li>
      ))}
    </ul>
  );
}

function FlagTrend({ data }: { data: QualityResponse }) {
  const flags = Object.keys(FLAGS).filter((f) => (data.totals.flags[f] ?? 0) > 0);
  const [flag, setFlag] = useState(flags[0] ?? "invalid_amount");
  const points = useMemo(() => {
    const byDay = new Map<string, { flagged: number; trips: number }>();
    for (const row of data.daily_flags) {
      const entry = byDay.get(row.date) ?? { flagged: 0, trips: row.trips };
      if (row.flag === flag) entry.flagged = row.flagged_trips;
      byDay.set(row.date, entry);
    }
    return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, v]) => ({ date, rate: v.trips ? v.flagged / v.trips : null, ...v }));
  }, [data.daily_flags, flag]);
  return (
    <Card>
      <CardHeader
        title="Daily flag rate"
        description="Share of each day’s accepted trips carrying the selected flag."
        actions={
          <select value={flag} onChange={(e) => setFlag(e.target.value)} aria-label="Flag" className="h-8 rounded-lg border border-line-strong bg-surface px-2 text-[13px] text-ink shadow-card outline-none focus:border-accent">
            {flags.map((f) => (
              <option key={f} value={f}>
                {FLAGS[f]?.label ?? humanize(f)}
              </option>
            ))}
          </select>
        }
      />
      <div className="px-3 pb-4 pt-2 sm:px-5">
        {points.length === 0 ? (
          <EmptyBlock title="No daily data for this range" className="h-60" />
        ) : (
          <EChart
            testId="flag-trend"
            ariaLabel={`Daily share of trips flagged ${FLAGS[flag]?.label ?? flag}`}
            className="h-60"
            deps={[points, flag]}
            build={(theme) => ({
              grid: { left: 6, right: 12, top: 12, bottom: 6, containLabel: true },
              xAxis: { type: "category", data: points.map((p) => formatBucket(p.date, "day")), boundaryGap: false, axisLine: { lineStyle: { color: theme.axis } }, axisTick: { show: false }, axisLabel: { color: theme.muted, fontSize: 11, hideOverlap: true } },
              yAxis: { type: "value", axisLabel: { color: theme.muted, fontSize: 11, formatter: (v: number) => `${(v * 100).toFixed(v < 0.01 ? 2 : 1)}%` }, splitLine: { lineStyle: { color: theme.grid } } },
              tooltip: {
                ...tooltipBase(theme),
                trigger: "axis",
                formatter: (params: unknown) => {
                  const p = points[(params as { dataIndex: number }[])[0]?.dataIndex ?? -1];
                  if (!p) return "";
                  return `<div style="font-weight:600;font-size:14px">${escapeHtml(formatPercent(p.flagged, p.trips))}</div><div style="color:${theme.ink2}">${escapeHtml(formatInteger(p.flagged))} of ${escapeHtml(formatInteger(p.trips))} trips</div><div style="color:${theme.muted}">${escapeHtml(formatBucket(p.date, "day"))}</div>`;
                },
              },
              series: [{ type: "line", data: points.map((p) => p.rate), showSymbol: false, lineStyle: { width: 2, color: theme.series }, itemStyle: { color: theme.series }, areaStyle: { color: "rgba(42,120,214,0.08)" } }],
            })}
          />
        )}
      </div>
    </Card>
  );
}

function Missingness({ data }: { data: QualityResponse }) {
  const totals = new Map<string, number>();
  let input = 0;
  for (const period of data.periods) {
    input += period.input_rows;
    for (const [column, m] of Object.entries(period.missingness)) totals.set(column, (totals.get(column) ?? 0) + m.null_count);
  }
  const rows = [...totals.entries()].sort((a, b) => b[1] - a[1]);
  return (
    <Card>
      <CardHeader title="Missing values by field" description="Blank values in the source files, before any cleaning. Fields absent from a file are listed in the schema registry instead." />
      <div className="px-5 pb-5 pt-3">
        <ul className="grid gap-x-8 gap-y-2 md:grid-cols-2" data-testid="missingness">
          {rows.map(([column, nulls]) => (
            <li key={column} className="flex items-center gap-3">
              <span className="w-44 shrink-0 truncate font-mono text-xs text-ink-2">{column}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                <span className={cn("block h-full rounded-full", nulls ? "bg-warning" : "bg-good/60")} style={{ width: nulls ? `${Math.max((nulls / input) * 100, 1)}%` : "100%" }} />
              </span>
              <span className="w-20 shrink-0 text-right text-xs tabular text-ink-2">{nulls ? formatPercent(nulls, input) : "complete"}</span>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

function SchemaPanel({ report }: { report: SchemaReport }) {
  const periods = report.sources.map((s) => s.period);
  const changed = report.drift.filter((d) => d.changed);
  return (
    <Card>
      <CardHeader
        title="Schema registry"
        description="Each file’s actual columns, matched to the canonical schema. Drift is checked between consecutive months."
        actions={
          <Pill tone={changed.length ? "warning" : "good"}>
            <GitCompareArrows className="size-3" /> {changed.length ? `${changed.length} change${changed.length > 1 ? "s" : ""} detected` : "No drift across files"}
          </Pill>
        }
      />
      <div className="space-y-5 px-5 pb-5 pt-3">
        <div className="flex flex-wrap gap-2">
          {report.schema_versions.map((v) => (
            <div key={v.schema_version} className="rounded-xl border border-line bg-surface-2 px-3 py-2">
              <p className="font-mono text-xs text-ink">{v.schema_version}</p>
              <p className="text-[11px] text-ink-muted">
                {v.file_format.toUpperCase()} · {v.column_count} columns · {v.periods.join(", ")}
              </p>
            </div>
          ))}
        </div>
        {changed.length ? (
          <ul className="space-y-1.5 text-xs text-ink-2">
            {changed.map((d) => (
              <li key={d.to_period} className="rounded-lg bg-warning-soft px-3 py-2">
                <span className="font-medium text-warning-ink">
                  {d.from_period} → {d.to_period}:
                </span>{" "}
                {[d.added.length && `added ${d.added.join(", ")}`, d.removed.length && `removed ${d.removed.join(", ")}`, d.renamed_case.length && `renamed ${d.renamed_case.map((r) => `${r.from}→${r.to}`).join(", ")}`, d.type_changed.length && `retyped ${d.type_changed.map((t) => `${t.column} ${t.from}→${t.to}`).join(", ")}`].filter(Boolean).join("; ")}
              </li>
            ))}
          </ul>
        ) : null}
        <div className="scroll-thin overflow-x-auto rounded-xl border border-line">
          <table className="w-full min-w-[640px] text-xs" data-testid="schema-matrix">
            <thead className="bg-surface-2 text-ink-muted">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-medium">Canonical field</th>
                {periods.map((p) => (
                  <th key={p} scope="col" className="px-2 py-2 text-center font-medium tabular">
                    {p}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.fields.map((field) => (
                <tr key={field.name} className="border-t border-line">
                  <td className="px-3 py-1.5">
                    <Tooltip content={field.description || field.kind}>
                      <span className="font-mono text-ink">{field.name}</span>
                    </Tooltip>
                    {field.required ? <span className="ml-1.5 text-[10px] text-accent-strong">required</span> : null}
                  </td>
                  {periods.map((p) => {
                    const column = field.source_column_by_period[p];
                    return (
                      <td key={p} className="px-2 py-1.5 text-center">
                        {column ? (
                          <Tooltip content={`Source column: ${column}`}>
                            <Check className="mx-auto size-3.5 text-good" aria-label={`available as ${column}`} />
                          </Tooltip>
                        ) : (
                          <Minus className="mx-auto size-3.5 text-ink-faint" aria-label="unavailable" />
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}

export function QualityView() {
  const dataset = useDataset();
  const months = monthPresets(dataset.data);
  const [month, setMonth] = useState("all");
  const preset = months.find((m) => m.id === month);
  const quality = useQuality(preset ? { start_date: preset.start, end_date: preset.end } : {});
  const schema = useSchemaReport();
  const data = quality.data;

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Data quality"
        description="What the pipeline received, what it set aside and why. Quarantined rows never reach the dashboard; flagged values stay but are excluded from the metrics they would distort."
        actions={
          <Segmented
            label="Period"
            size="md"
            value={month}
            onChange={setMonth}
            options={[{ value: "all", label: "All months" }, ...months.map((m) => ({ value: m.id, label: m.label.split(" ")[0] ?? m.label }))]}
          />
        }
      />
      {quality.isPending ? (
        <LoadingBlock />
      ) : quality.isError ? (
        <Card>
          <ErrorBlock error={quality.error} onRetry={() => void quality.refetch()} />
        </Card>
      ) : data && data.periods.length ? (
        <div className={cn("space-y-6 transition-opacity", quality.isPlaceholderData && "opacity-60")}>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Rows received" value={formatInteger(data.totals.input_rows)} hint={`${data.periods.length} monthly runs`} />
            <Stat label="Accepted" value={formatPercent(data.totals.accepted_rows, data.totals.input_rows)} hint={`${formatInteger(data.totals.accepted_rows)} trips published`} tone="good" />
            <Stat label="Quarantined" value={formatInteger(data.totals.quarantined_rows)} hint={`${formatPercent(data.totals.quarantined_rows, data.totals.input_rows)} kept aside with reasons`} tone={data.totals.quarantined_rows ? "warning" : "neutral"} />
            <Stat label="Exact duplicates" value={formatInteger(data.totals.duplicate_rows)} hint="Detected across all fields" />
          </div>
          <div className="grid gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader title="Quarantine reasons" description="Rows removed from curated data and preserved in the lake’s quarantine area." />
              <div className="px-5 pb-5 pt-4">
                <RuleBars testId="quarantine-reasons" counts={data.totals.quarantine_reasons} labels={QUARANTINE_REASONS} denominator={data.totals.input_rows} denominatorLabel="rows" />
              </div>
            </Card>
            <Card>
              <CardHeader title="Flags on accepted trips" description="Kept in the data; excluded only from the metrics the flagged value affects." />
              <div className="px-5 pb-5 pt-4">
                <RuleBars testId="quality-flags" counts={data.totals.flags} labels={FLAGS} denominator={data.totals.accepted_rows} denominatorLabel="trips" />
              </div>
            </Card>
          </div>
          <FlagTrend data={data} />
          <Card className="overflow-hidden">
            <CardHeader title="Runs by month" description="The run currently published for each month, with its reconciliation." />
            <div className="scroll-thin mt-3 overflow-x-auto">
              <table className="w-full min-w-[900px] text-sm" data-testid="quality-periods">
                <thead className="bg-surface-2 text-left text-xs text-ink-muted">
                  <tr>
                    {["Month", "Rows read", "Accepted", "Quarantined", "Duplicates", "Run time", "Schema", "Unavailable fields"].map((h) => (
                      <th key={h} scope="col" className="px-4 py-2.5 font-medium first:pl-5">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="tabular">
                  {data.periods.map((p) => (
                    <tr key={p.period} className="border-t border-line">
                      <td className="px-4 py-2.5 pl-5 font-medium text-ink">{p.period}</td>
                      <td className="px-4 py-2.5 text-ink-2">{formatInteger(p.input_rows)}</td>
                      <td className="px-4 py-2.5 text-ink">
                        {formatInteger(p.accepted_rows)} <span className="text-xs text-ink-muted">({formatPercent(p.accepted_rows, p.input_rows)})</span>
                      </td>
                      <td className="px-4 py-2.5 text-ink-2">{formatInteger(p.quarantined_rows)}</td>
                      <td className="px-4 py-2.5 text-ink-2">{formatInteger(p.duplicate_rows)}</td>
                      <td className="px-4 py-2.5 text-ink-2">{formatDuration(p.duration_seconds)}</td>
                      <td className="px-4 py-2.5 font-mono text-[11px] text-ink-2">{p.schema_version}</td>
                      <td className="px-4 py-2.5 text-xs text-ink-2">{p.unavailable_fields.length ? p.unavailable_fields.join(", ") : <span className="text-good-ink">none</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Missingness data={data} />
          {schema.data ? <SchemaPanel report={schema.data} /> : schema.isError ? <ErrorBlock error={schema.error} /> : <LoadingBlock />}
          <p className="flex items-center gap-1.5 text-xs text-ink-muted">
            <ShieldCheck className="size-3.5" /> {data.meta.note}
          </p>
        </div>
      ) : (
        <Card>
          <EmptyBlock title="No published runs yet">Quality metrics appear once a month has been processed and published.</EmptyBlock>
        </Card>
      )}
    </div>
  );
}
