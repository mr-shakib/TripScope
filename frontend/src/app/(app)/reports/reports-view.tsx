"use client";

import { ArrowRight, Globe, Lock, Plus, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useCreateReport, useDataset, useMe, useReports, useReportTemplates } from "@/api/hooks";
import type { ReportFormat, ReportSummary } from "@/api/types";
import { FilterBar } from "@/components/filters/filter-bar";
import { PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { Pill } from "@/components/ui/status";
import { FileChip } from "@/components/reports/file-chip";
import { useUrlFilters, useUrlParam } from "@/hooks/use-url-filters";
import { cn } from "@/lib/cn";
import { fromApiBody } from "@/lib/filters";
import { formatDate, formatRelative } from "@/lib/format";

const SCOPES = ["all", "mine", "shared"] as const;
type Scope = (typeof SCOPES)[number];
const FORMATS: ReportFormat[] = ["pdf", "xlsx", "csv"];

export function reportPeriod(filters: Record<string, unknown>): string {
  const f = fromApiBody(filters);
  if (f.start_date && f.end_date) return `${formatDate(f.start_date)} – ${formatDate(f.end_date)}`;
  if (f.start_date) return `From ${formatDate(f.start_date)}`;
  if (f.end_date) return `To ${formatDate(f.end_date)}`;
  return "All published data";
}

function Visibility({ value }: { value: ReportSummary["visibility"] }) {
  return value === "shared" ? (
    <Pill tone="accent">
      <Globe className="size-3" /> Shared
    </Pill>
  ) : (
    <Pill>
      <Lock className="size-3" /> Private
    </Pill>
  );
}

export function ReportsView() {
  const me = useMe();
  const author = me.data?.role === "admin" || me.data?.role === "analyst";
  const dataset = useDataset();
  const [filters, setFilters] = useUrlFilters();
  const [scope, setScope] = useUrlParam<Scope>("scope", "all", SCOPES);
  const templates = useReportTemplates();
  const reports = useReports(scope);
  const create = useCreateReport();
  const router = useRouter();

  const start = (template: string) =>
    create.mutate(
      { template, filters },
      {
        onSuccess: (report) => router.push(`/reports/${report.report_id}`),
        onError: (error) => toast.error("Could not create the report", { description: error instanceof ApiError ? error.message : undefined }),
      },
    );

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader
        title="Reports"
        description="Evidence-backed reports built from the same filters and metric definitions as the dashboard. Preview a report, then export it as PDF, Excel or CSV."
      />

      {author ? (
        <Card>
          <CardHeader
            title="Start a report"
            description="Pick a template. It starts with the filters below; you can change the period, filters and sections before exporting."
          />
          <div className="px-5 pt-3">
            <FilterBar coverage={dataset.data} filters={filters} onChange={setFilters} />
          </div>
          <div className="grid gap-3 p-5 sm:grid-cols-2 xl:grid-cols-3" data-testid="report-templates">
            {templates.isPending ? (
              <LoadingBlock className="h-40 sm:col-span-2 xl:col-span-3" />
            ) : templates.isError ? (
              <ErrorBlock error={templates.error} className="h-40 sm:col-span-2 xl:col-span-3" />
            ) : (
              <>
                {templates.data.templates.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    data-testid={`template-${t.id}`}
                    disabled={create.isPending}
                    onClick={() => start(t.id)}
                    className="group flex flex-col rounded-xl border border-line bg-surface p-4 text-left transition-all hover:-translate-y-px hover:border-accent/40 hover:shadow-card disabled:opacity-60"
                  >
                    <span className="flex items-center gap-2">
                      <span className="grid size-6 place-items-center rounded-md bg-accent-soft text-[11px] font-semibold text-accent-strong">{t.number}</span>
                      <span className="text-[14px] font-semibold text-ink">{t.title}</span>
                    </span>
                    <span className="mt-2 flex-1 text-[12.5px] leading-relaxed text-ink-2">{t.description}</span>
                    <span className="mt-3 flex items-center justify-between text-[11.5px] text-ink-muted">
                      {t.sections.length} sections{t.kpis.length ? ` · ${t.kpis.length} KPIs` : ""}
                      <span className="inline-flex items-center gap-1 font-medium text-accent-strong opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                        <Plus className="size-3.5" /> Create
                      </span>
                    </span>
                  </button>
                ))}
                <div className="flex flex-col rounded-xl border border-dashed border-line-strong p-4 text-left opacity-80">
                  <span className="flex items-center gap-2">
                    <span className="grid size-6 place-items-center rounded-md bg-surface-3 text-ink-muted">
                      <Sparkles className="size-3.5" />
                    </span>
                    <span className="text-[14px] font-semibold text-ink-2">Custom AI-assisted report</span>
                  </span>
                  <span className="mt-2 text-[12.5px] leading-relaxed text-ink-muted">
                    Template 6 arrives with the AI analyst (Phase 5): an outline and narrative drafted from approved analytics results, with every number checked.
                  </span>
                </div>
              </>
            )}
          </div>
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title="Report history"
          description={author ? "Your reports and the ones shared with everyone." : "Reports shared with you. Analysts and administrators create reports."}
          actions={
            <Segmented
              label="Which reports"
              value={scope}
              onChange={setScope}
              options={[
                { value: "all", label: "All" },
                { value: "mine", label: "Mine" },
                { value: "shared", label: "Shared" },
              ]}
            />
          }
        />
        <div className={cn("px-2 pb-3 pt-2 transition-opacity", reports.isPlaceholderData && "opacity-60")}>
          {reports.isPending ? (
            <LoadingBlock className="h-48" />
          ) : reports.isError ? (
            <ErrorBlock error={reports.error} className="h-48" onRetry={() => void reports.refetch()} />
          ) : reports.data.length === 0 ? (
            <EmptyBlock title="No reports yet" className="h-48">
              {author ? "Choose a template above to create one." : "Nothing has been shared yet."}
            </EmptyBlock>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]" data-testid="reports-table">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-ink-muted">
                    <th className="px-3 py-2 font-medium">Report</th>
                    <th className="px-3 py-2 font-medium">Period</th>
                    <th className="px-3 py-2 font-medium">Owner</th>
                    <th className="px-3 py-2 font-medium">Visibility</th>
                    <th className="px-3 py-2 font-medium">Latest files</th>
                    <th className="px-3 py-2 font-medium">Updated</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody>
                  {reports.data.map((r) => (
                    <tr key={r.report_id} className="group border-b border-line last:border-0 hover:bg-surface-2" data-testid={`report-row-${r.report_id}`}>
                      <td className="px-3 py-2.5">
                        <Link href={`/reports/${r.report_id}`} className="font-medium text-ink hover:text-accent-strong">
                          {r.title}
                        </Link>
                        <div className="text-[11.5px] text-ink-muted">{r.template_title}</div>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-ink-2">{reportPeriod(r.filters)}</td>
                      <td className="px-3 py-2.5 text-ink-2">{r.created_by?.name ?? "—"}</td>
                      <td className="px-3 py-2.5">
                        <Visibility value={r.visibility} />
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex flex-wrap gap-1">
                          {FORMATS.map((fmt) => {
                            const run = r.latest_runs[fmt];
                            return run ? <FileChip key={fmt} reportId={r.report_id} run={run} title={r.title} /> : null;
                          })}
                          {Object.keys(r.latest_runs).length === 0 ? <span className="text-[12px] text-ink-faint">None yet</span> : null}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-ink-muted">{formatRelative(r.updated_at)}</td>
                      <td className="px-2">
                        <Button asChild variant="ghost" size="sm" aria-label={`Open ${r.title}`}>
                          <Link href={`/reports/${r.report_id}`}>
                            <ArrowRight className="size-4" />
                          </Link>
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
