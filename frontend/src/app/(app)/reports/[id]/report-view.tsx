"use client";

import { ArrowLeft, Download, Globe, Lock, Save, Trash2, Undo2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import {
  useAIStatus,
  useDataset,
  useDeleteReport,
  useGenerateReport,
  useReport,
  useReportPreview,
  useReportTemplates,
  useUpdateReport,
} from "@/api/hooks";
import type { DashboardFilters, ReportDetail, ReportFormat, ReportRun, ReportVisibility } from "@/api/types";
import { FilterBar } from "@/components/filters/filter-bar";
import { NarrativeBanner } from "@/components/reports/ai-draft";
import { FORMAT_ICONS, FORMAT_LABELS, useDownload } from "@/components/reports/file-chip";
import { ReportPreview } from "@/components/reports/report-preview";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Tooltip } from "@/components/ui/overlays";
import { Segmented } from "@/components/ui/segmented";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { fromApiBody } from "@/lib/filters";
import { formatBytes, formatDuration, formatRelative } from "@/lib/format";

const FORMATS: ReportFormat[] = ["pdf", "xlsx", "csv"];

interface Draft {
  title: string;
  filters: DashboardFilters;
  sections: string[];
  visibility: ReportVisibility;
}

function baseline(report: ReportDetail): Draft {
  return { title: report.title, filters: fromApiBody(report.filters), sections: report.sections, visibility: report.visibility };
}

function RunRow({ reportId, run }: { reportId: string; run: ReportRun }) {
  const { busy, download } = useDownload(reportId);
  const Icon = FORMAT_ICONS[run.format];
  const details = [
    run.size_bytes != null ? formatBytes(run.size_bytes) : null,
    run.page_count ? `${run.page_count} pages` : null,
    run.duration_seconds != null ? `built in ${formatDuration(run.duration_seconds)}` : null,
    run.dataset_version_id ? `data ${run.dataset_version_id}` : null,
  ].filter(Boolean);
  return (
    <li className="flex items-start gap-3 py-2.5" data-testid={`run-${run.run_id}`} data-format={run.format} data-status={run.status}>
      <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-surface-3 text-ink-2">
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium text-ink">{FORMAT_LABELS[run.format]}</span>
          <StatusBadge status={run.status} />
        </div>
        <p className="mt-0.5 text-[11.5px] text-ink-muted">
          {formatRelative(run.created_at)}
          {run.requested_by ? ` · ${run.requested_by.name}` : ""}
          {details.length ? ` · ${details.join(" · ")}` : ""}
        </p>
        {run.error_summary ? <p className="mt-1 text-[11.5px] text-critical-ink">{run.error_summary}</p> : null}
      </div>
      <Button
        size="sm"
        variant={run.status === "completed" ? "secondary" : "ghost"}
        disabled={run.status !== "completed" || busy !== null}
        onClick={() => void download(run)}
        aria-label={`Download ${FORMAT_LABELS[run.format]}`}
      >
        <Download className="size-3.5" />
      </Button>
    </li>
  );
}

export function ReportView({ reportId }: { reportId: string }) {
  const router = useRouter();
  const report = useReport(reportId);
  const preview = useReportPreview(reportId, report.data?.updated_at);
  const templates = useReportTemplates();
  const aiStatus = useAIStatus();
  const dataset = useDataset();
  const update = useUpdateReport(reportId);
  const generate = useGenerateReport(reportId);
  const remove = useDeleteReport();
  const [edits, setEdits] = useState<Partial<Draft>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (report.isPending) return <LoadingBlock className="h-[60vh]" />;
  if (report.isError) {
    return report.error instanceof ApiError && report.error.status === 404 ? (
      <EmptyBlock title="Report not found" className="h-[60vh]">
        It was deleted, or it is private to someone else. <Link href="/reports" className="text-accent-strong underline">All reports</Link>
      </EmptyBlock>
    ) : (
      <ErrorBlock error={report.error} className="h-[60vh]" onRetry={() => void report.refetch()} />
    );
  }

  const data = report.data;
  const saved = baseline(data);
  const draft: Draft = { ...saved, ...edits };
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const template = templates.data?.templates.find((t) => t.id === data.template);
  const canEdit = data.permissions.edit;
  const canGenerate = data.permissions.generate;
  const active = (fmt: ReportFormat) => data.runs.some((r) => r.format === fmt && (r.status === "queued" || r.status === "running"));

  const save = () =>
    update.mutate(
      { title: draft.title.trim() || saved.title, filters: draft.filters, sections: draft.sections, visibility: draft.visibility },
      {
        onSuccess: () => {
          setEdits({});
          toast.success("Report saved", { description: "The preview now reflects your changes." });
        },
        onError: (error) => toast.error("Could not save", { description: error instanceof ApiError ? error.message : undefined }),
      },
    );

  const start = (fmt: ReportFormat) =>
    generate.mutate(fmt, {
      onSuccess: () => toast.success(`${FORMAT_LABELS[fmt]} queued`, { description: "It appears under Files when it is ready." }),
      onError: (error) => toast.error(`Could not queue the ${FORMAT_LABELS[fmt]}`, { description: error instanceof ApiError ? error.message : undefined }),
    });

  const toggleSection = (id: string) => {
    const next = draft.sections.includes(id) ? draft.sections.filter((s) => s !== id) : [...draft.sections, id];
    const ordered = (template?.sections.map((s) => s.id) ?? next).filter((s) => next.includes(s));
    setEdits((e) => ({ ...e, sections: ordered }));
  };

  return (
    <div className="mx-auto max-w-[1500px]">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <Link href="/reports" className="inline-flex items-center gap-1 text-[12.5px] text-ink-muted hover:text-ink">
            <ArrowLeft className="size-3.5" /> All reports
          </Link>
          <h1 className="mt-1 truncate text-[24px] font-semibold tracking-tight text-ink" data-testid="report-heading">
            {data.title}
          </h1>
          <p className="mt-0.5 flex flex-wrap items-center gap-2 text-[13px] text-ink-2">
            {data.template_title}
            <span className="text-ink-faint">·</span>
            {data.created_by?.name ?? "—"}
            <span className="text-ink-faint">·</span>
            <span className="inline-flex items-center gap-1">
              {data.visibility === "shared" ? <Globe className="size-3.5" /> : <Lock className="size-3.5" />}
              {data.visibility === "shared" ? "Shared with everyone" : "Private"}
            </span>
          </p>
        </div>
        {canEdit ? (
          <Button variant="danger" size="sm" onClick={() => setConfirmDelete(true)}>
            <Trash2 className="size-3.5" /> Delete
          </Button>
        ) : null}
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[400px_minmax(0,1fr)]">
        <div className="space-y-5 xl:sticky xl:top-20 xl:max-h-[calc(100vh-6.5rem)] xl:overflow-y-auto xl:pb-2 xl:pr-1">
          <Card>
            <CardHeader
              title="Export"
              description={
                canGenerate
                  ? "Files are generated in the background from the saved report and kept with its history."
                  : "Analysts and administrators generate files; you can download the ones below."
              }
            />
            {canGenerate ? (
              <div className="flex flex-wrap gap-2 px-5 pt-3">
                {FORMATS.map((fmt) => {
                  const Icon = FORMAT_ICONS[fmt];
                  const button = (
                    <Button
                      key={fmt}
                      variant={fmt === "pdf" ? "primary" : "secondary"}
                      size="sm"
                      disabled={dirty || active(fmt) || generate.isPending}
                      onClick={() => start(fmt)}
                      data-testid={`generate-${fmt}`}
                    >
                      <Icon className="size-3.5" /> {active(fmt) ? `Generating ${FORMAT_LABELS[fmt]}…` : FORMAT_LABELS[fmt]}
                    </Button>
                  );
                  return dirty ? (
                    <Tooltip key={fmt} content="Save your changes first so the file matches the preview.">
                      <span>{button}</span>
                    </Tooltip>
                  ) : (
                    button
                  );
                })}
              </div>
            ) : null}
            <div className="px-5 pb-3 pt-2">
              <h3 className="mt-2 text-[12px] font-medium text-ink-2">Files</h3>
              {data.runs.length === 0 ? (
                <p className="py-4 text-[12.5px] text-ink-muted">No files yet.</p>
              ) : (
                <ul className="divide-y divide-line" data-testid="report-runs">
                  {data.runs.map((run) => (
                    <RunRow key={run.run_id} reportId={reportId} run={run} />
                  ))}
                </ul>
              )}
            </div>
          </Card>
          {canEdit ? (
            <Card>
              <CardHeader title="Report settings" description="Saved changes rebuild the preview; exports always use the saved version." />
              <div className="space-y-4 p-5">
                <label className="block">
                  <span className="text-[12px] font-medium text-ink-2">Title</span>
                  <input
                    value={draft.title}
                    maxLength={200}
                    onChange={(e) => setEdits((x) => ({ ...x, title: e.target.value }))}
                    data-testid="report-title-input"
                    className="mt-1 h-9 w-full rounded-lg border border-line-strong bg-surface px-3 text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
                  />
                </label>
                <div>
                  <span className="text-[12px] font-medium text-ink-2">Period and filters</span>
                  <div className="mt-1.5">
                    <FilterBar coverage={dataset.data} filters={draft.filters} onChange={(filters) => setEdits((x) => ({ ...x, filters }))} />
                  </div>
                </div>
                <fieldset>
                  <legend className="text-[12px] font-medium text-ink-2">Sections</legend>
                  <div className="mt-1.5 space-y-1">
                    {template?.sections.map((s) => (
                      <label key={s.id} className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-1 hover:bg-surface-2">
                        <input
                          type="checkbox"
                          checked={draft.sections.includes(s.id)}
                          disabled={draft.sections.length === 1 && draft.sections.includes(s.id)}
                          onChange={() => toggleSection(s.id)}
                          data-testid={`section-${s.id}`}
                          className="mt-0.5 accent-[var(--accent)]"
                        />
                        <span>
                          <span className="block text-[13px] text-ink">{s.title}</span>
                          <span className="block text-[11.5px] text-ink-muted">{s.description}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[12px] font-medium text-ink-2">Visibility</span>
                  <Segmented
                    label="Visibility"
                    value={draft.visibility}
                    onChange={(visibility) => setEdits((x) => ({ ...x, visibility }))}
                    options={[
                      { value: "private", label: "Private" },
                      { value: "shared", label: "Shared" },
                    ]}
                  />
                </div>
                <div className="flex items-center justify-end gap-2 border-t border-line pt-4">
                  <Button variant="ghost" size="sm" disabled={!dirty || update.isPending} onClick={() => setEdits({})}>
                    <Undo2 className="size-3.5" /> Discard
                  </Button>
                  <Button variant="primary" size="sm" disabled={!dirty || update.isPending} onClick={save} data-testid="save-report">
                    <Save className="size-3.5" /> {update.isPending ? "Saving…" : "Save changes"}
                  </Button>
                </div>
              </div>
            </Card>
          ) : null}

        </div>

        <div className="min-w-0">
          {data.narrative ? (
            <NarrativeBanner reportId={reportId} narrative={data.narrative} canEdit={canEdit} aiEnabled={Boolean(aiStatus.data?.enabled)} />
          ) : null}
          {dirty ? (
            <div className="mb-3 rounded-lg border border-warning/40 bg-warning-soft px-4 py-2.5 text-[12.5px] text-warning-ink" role="status">
              Unsaved changes. The preview and exports use the saved report until you save.
            </div>
          ) : null}
          {preview.isPending ? (
            <LoadingBlock className="h-[70vh]" label="Building the preview…" />
          ) : preview.isError ? (
            <ErrorBlock error={preview.error} className="h-[50vh]" onRetry={() => void preview.refetch()} />
          ) : (
            <ReportPreview doc={preview.data} dimmed={preview.isFetching} />
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this report?"
        description="The report, its history and every generated file are removed. This cannot be undone."
        confirmLabel="Delete report"
        tone="danger"
        onConfirm={() =>
          remove.mutate(reportId, {
            onSuccess: () => {
              toast.success("Report deleted");
              router.push("/reports");
            },
            onError: (error) => toast.error("Could not delete", { description: error instanceof ApiError ? error.message : undefined }),
          })
        }
      />
    </div>
  );
}

