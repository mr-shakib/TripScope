"use client";

import type { ReportDocument, ReportTableBlock, ReportUnit } from "@/api/types";
import { cn } from "@/lib/cn";

import { ReportChart } from "./report-chart";

const NUMERIC: ReadonlySet<ReportUnit> = new Set(["trips", "usd", "miles", "minutes", "rows", "count", "share", "seconds", "bytes"]);

function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="mt-8 text-[17px] font-semibold tracking-tight text-ink">{children}</h2>;
}

function Bullets({ lines, muted = false }: { lines: string[]; muted?: boolean }) {
  return (
    <ul className={cn("mt-2 space-y-1.5 text-[13px] leading-relaxed", muted ? "text-ink-2" : "text-ink")}>
      {lines.map((line) => (
        <li key={line} className="flex gap-2">
          <span className="mt-[7px] size-1 shrink-0 rounded-full bg-ink-faint" aria-hidden />
          <span>{line}</span>
        </li>
      ))}
    </ul>
  );
}

function Table({ block }: { block: ReportTableBlock }) {
  const numeric = block.columns.map((c) => NUMERIC.has(c.unit));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]" data-testid={`report-table-${block.id}`}>
        <thead>
          <tr className="border-b border-line-strong text-ink-2">
            {block.columns.map((c, i) => (
              <th key={c.key} className={cn("px-2 py-1.5 font-medium", numeric[i] ? "text-right" : "text-left")}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.display.map((row, r) => (
            <tr key={r} className="border-b border-line even:bg-surface-2">
              {row.map((cell, i) => (
                <td key={i} className={cn("px-2 py-1.5", numeric[i] ? "text-right tabular" : "text-left")}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function evidenceValue(value: string | number | boolean | null): string {
  if (typeof value === "number") return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
  return String(value);
}

/**
 * The report document laid out like the PDF (same order, tables, chart kinds and palette), so what is previewed
 * is what gets exported.
 */
export function ReportPreview({ doc, dimmed = false }: { doc: ReportDocument; dimmed?: boolean }) {
  const meta: [string, string][] = [
    ["Period", doc.period.label],
    ...(doc.comparison.available && doc.comparison.label ? ([["Compared with", doc.comparison.label]] as [string, string][]) : []),
    ["Dataset", `${doc.dataset.name} · version ${doc.dataset.version_id} (${doc.dataset.periods.length} month${doc.dataset.periods.length === 1 ? "" : "s"})`],
    ["Filters", doc.filters.slice(1).map((f) => `${f.label}: ${f.value}`).join("; ") || "None"],
    ["Prepared by", doc.prepared_by],
    ["Source", doc.dataset.attribution],
  ];
  return (
    <article
      className={cn(
        "mx-auto max-w-[860px] rounded-xl border border-line bg-white px-6 py-8 shadow-card transition-opacity sm:px-12 sm:py-12",
        dimmed && "opacity-60",
      )}
      data-testid="report-preview"
      aria-label={`Preview of ${doc.title}`}
    >
      <p className="text-[12px] font-medium text-accent">TripScope report · {doc.template_title}</p>
      <h1 className="mt-1 text-[28px] font-semibold leading-tight tracking-tight text-ink" data-testid="preview-title">
        {doc.title}
      </h1>
      <p className="mt-1 text-[15px] font-medium text-ink-2">{doc.period.label}</p>
      <dl className="mt-5 grid grid-cols-[minmax(90px,auto)_1fr] gap-x-4 gap-y-1.5 rounded-lg border border-line bg-surface-2 px-4 py-3 text-[12px]">
        {meta.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="font-medium text-ink-muted">{label}</dt>
            <dd className="text-ink">{value}</dd>
          </div>
        ))}
      </dl>

      <H2>Executive summary</H2>
      <Bullets lines={doc.summary} />

      {doc.kpis.length ? (
        <>
          <H2>Key figures</H2>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="border-b border-line-strong text-ink-2">
                  <th className="px-2 py-1.5 text-left font-medium">Metric</th>
                  <th className="px-2 py-1.5 text-right font-medium">Value</th>
                  {doc.comparison.available ? (
                    <>
                      <th className="px-2 py-1.5 text-right font-medium">Previous period</th>
                      <th className="px-2 py-1.5 text-right font-medium">Change</th>
                    </>
                  ) : null}
                  <th className="px-2 py-1.5 text-left font-medium">Notes</th>
                </tr>
              </thead>
              <tbody>
                {doc.kpis.map((kpi) => (
                  <tr key={kpi.id} className="border-b border-line" data-testid={`preview-kpi-${kpi.id}`} data-value={kpi.value ?? ""}>
                    <td className="px-2 py-1.5 text-ink">{kpi.label}</td>
                    <td className="px-2 py-1.5 text-right font-semibold tabular text-ink">{kpi.display}</td>
                    {doc.comparison.available ? (
                      <>
                        <td className="px-2 py-1.5 text-right tabular text-ink-2">{kpi.previous_display ?? "—"}</td>
                        <td
                          className={cn(
                            "px-2 py-1.5 text-right tabular",
                            kpi.change === null || kpi.change === 0 ? "text-ink-2" : kpi.change > 0 ? "text-good-ink" : "text-critical-ink",
                          )}
                        >
                          {kpi.change_display ?? "—"}
                        </td>
                      </>
                    ) : null}
                    <td className="px-2 py-1.5 text-[11px] text-ink-muted">{kpi.note ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!doc.comparison.available && doc.comparison.reason ? (
            <p className="mt-2 text-[11px] text-ink-muted">No period comparison: {doc.comparison.reason}</p>
          ) : null}
        </>
      ) : null}

      {doc.sections.map((section) => (
        <section key={section.id} data-testid={`preview-section-${section.id}`}>
          <H2>{section.title}</H2>
          <p className="text-[12px] text-ink-muted">{section.description}</p>
          {section.blocks.map((block) => (
            <div key={block.id} className="mt-4">
              <h3 className="mb-2 text-[13px] font-semibold text-ink">{block.title}</h3>
              {block.type === "chart" ? <ReportChart block={block} /> : <Table block={block} />}
              {block.note ? <p className="mt-1.5 text-[11px] text-ink-muted">{block.note}</p> : null}
            </div>
          ))}
        </section>
      ))}

      {doc.findings.length ? (
        <>
          <H2>Key findings</H2>
          <p className="text-[12px] text-ink-muted">Each finding lists the values it rests on.</p>
          <ol className="mt-3 space-y-3" data-testid="preview-findings">
            {doc.findings.map((finding, n) => (
              <li key={finding.id} className="text-[13px]">
                <p className="text-ink">
                  <span className="font-semibold">{n + 1}.</span> {finding.statement}
                </p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-ink-muted">
                  Evidence: {finding.evidence.source}
                  {finding.evidence.source_table ? ` (${finding.evidence.source_table})` : ""}:{" "}
                  {Object.entries(finding.evidence.values)
                    .map(([k, v]) => `${k} = ${evidenceValue(v)}`)
                    .join(", ")}
                </p>
                {finding.caveat ? <p className="text-[11px] text-ink-muted">Caveat: {finding.caveat}</p> : null}
              </li>
            ))}
          </ol>
        </>
      ) : null}

      <H2>Dataset version</H2>
      <p className="text-[12px] text-ink-muted">Version {doc.dataset.version_id}: the published monthly runs this report read.</p>
      <div className="mt-2">
        <Table
          block={{
            type: "table",
            id: "dataset-version",
            title: "",
            note: null,
            columns: [
              { key: "period", label: "Month", unit: "text" },
              { key: "run", label: "Processing run", unit: "text" },
              { key: "rows", label: "Rows", unit: "rows" },
            ],
            rows: [],
            display: doc.dataset.periods.map((p) => [p.period, p.run_id, p.row_count.toLocaleString("en-US")]),
          }}
        />
      </div>

      <H2>Methodology</H2>
      <Bullets lines={doc.methodology} muted />
      <H2>Limitations</H2>
      <Bullets lines={doc.limitations} muted />
      <p className="mt-6 text-[11px] text-ink-muted">Source: {doc.dataset.attribution}</p>
    </article>
  );
}
