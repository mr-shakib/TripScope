"use client";

import { Ban, RotateCcw } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useCancelJob, useJob, useJobs, useMe, useRetryJob, useRunLogs } from "@/api/hooks";
import type { Job, JobStatus } from "@/api/types";
import { LogViewer } from "@/components/jobs/log-viewer";
import { StageBar, StageTimeline } from "@/components/jobs/stage-timeline";
import { PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog, Drawer } from "@/components/ui/overlays";
import { Segmented } from "@/components/ui/segmented";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { formatDuration, formatInteger, formatPercent, formatRelative, formatTimestamp } from "@/lib/format";

type Tab = "all" | JobStatus;

function jobDuration(job: Job): number | null {
  if (!job.started_at) return null;
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  return (end - Date.parse(job.started_at)) / 1000;
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface-2 px-3 py-2.5">
      <p className="text-[11px] font-medium text-ink-muted">{label}</p>
      <p className="mt-0.5 text-lg font-semibold text-ink tabular">{value}</p>
      {hint ? <p className="text-[11px] text-ink-muted">{hint}</p> : null}
    </div>
  );
}

function JobDetail({ jobId, onClose, isAdmin }: { jobId: string; onClose: () => void; isAdmin: boolean }) {
  const job = useJob(jobId);
  const run = job.data?.runs.at(-1);
  const logs = useRunLogs(run?.run_id ?? null, job.data?.status === "running");
  const cancel = useCancelJob();
  const retry = useRetryJob();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const router = useRouter();
  const data = job.data;
  const onError = (title: string) => (error: Error) => toast.error(title, { description: error instanceof ApiError ? error.message : undefined });

  return (
    <Drawer
      open
      onOpenChange={(open) => !open && onClose()}
      title={data ? `${data.period} · ${data.source_key}` : "Job"}
      description={data ? `Attempt ${data.attempt} · ${data.trigger} · job ${data.job_id.slice(0, 8)}` : undefined}
      footer={
        data && isAdmin ? (
          <>
            {["queued", "running"].includes(data.status) ? (
              <Button variant="danger" size="sm" disabled={data.cancel_requested || cancel.isPending} onClick={() => setConfirmCancel(true)}>
                <Ban className="size-3.5" /> {data.cancel_requested ? "Cancelling…" : "Cancel job"}
              </Button>
            ) : null}
            {["failed", "cancelled"].includes(data.status) ? (
              <Button
                variant="primary"
                size="sm"
                disabled={retry.isPending}
                onClick={() =>
                  retry.mutate(data.job_id, {
                    onSuccess: (next) => {
                      toast.success("Retry queued", { description: `Attempt ${next.attempt} for ${next.source_key}.` });
                      router.replace(`/jobs?job=${next.job_id}`, { scroll: false });
                    },
                    onError: onError("Couldn’t retry"),
                  })
                }
              >
                <RotateCcw className="size-3.5" /> Retry
              </Button>
            ) : null}
          </>
        ) : undefined
      }
    >
      {job.isPending ? (
        <LoadingBlock />
      ) : job.isError ? (
        <ErrorBlock error={job.error} />
      ) : data ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-3">
            <StatusBadge status={data.status} />
            <span className="text-xs text-ink-muted">
              Created {formatTimestamp(data.created_at)}
              {data.started_at ? ` · started ${formatTimestamp(data.started_at)}` : ""}
              {data.finished_at ? ` · finished ${formatTimestamp(data.finished_at)}` : ""}
            </span>
          </div>
          {data.error_summary ? (
            <div className={`rounded-xl px-4 py-3 text-sm ${data.status === "cancelled" ? "bg-warning-soft text-warning-ink" : "bg-critical-soft text-critical-ink"}`} role="alert">
              {data.error_summary}
            </div>
          ) : null}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Metric label="Rows read" value={formatInteger(run?.input_rows)} />
            <Metric label="Accepted" value={formatInteger(run?.accepted_rows)} hint={formatPercent(run?.accepted_rows, run?.input_rows)} />
            <Metric label="Quarantined" value={formatInteger(run?.quarantined_rows)} hint={formatPercent(run?.quarantined_rows, run?.input_rows)} />
            <Metric label="Published" value={formatInteger(run?.clickhouse_rows)} hint={run?.clickhouse_rows != null ? "Verified in ClickHouse" : undefined} />
          </div>
          <section>
            <div className="mb-2 flex items-baseline justify-between">
              <h3 className="text-sm font-semibold text-ink">Pipeline stages</h3>
              <span className="text-xs text-ink-muted">{formatDuration(jobDuration(data))} total</span>
            </div>
            <StageTimeline run={run} />
          </section>
          {run ? (
            <section>
              <h3 className="mb-2 text-sm font-semibold text-ink">Run log</h3>
              {logs.data ? <LogViewer logs={logs.data.logs} /> : <LoadingBlock className="h-24" />}
              <p className="mt-2 text-[11px] text-ink-muted">
                Run <span className="font-mono">{run.run_id}</span>
                {run.schema_version ? ` · schema ${run.schema_version}` : ""}
                {data.worker_id ? ` · worker ${data.worker_id}` : ""}
              </p>
            </section>
          ) : null}
          <ConfirmDialog
            open={confirmCancel}
            onOpenChange={setConfirmCancel}
            tone="danger"
            title="Cancel this job?"
            description={data.status === "queued" ? "It hasn’t started; it will be cancelled immediately." : "The worker stops at the next safe point. Nothing is published unless the final swap already happened."}
            confirmLabel="Cancel job"
            onConfirm={() =>
              cancel.mutate(data.job_id, {
                onSuccess: (j) => toast.message(j.status === "cancelled" ? "Job cancelled" : "Cancellation requested"),
                onError: onError("Couldn’t cancel"),
              })
            }
          />
        </div>
      ) : null}
    </Drawer>
  );
}

export function JobsView() {
  const [tab, setTab] = useState<Tab>("all");
  const jobs = useJobs();
  const me = useMe();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const selected = params.get("job");
  const list = jobs.data ?? [];
  const counts = list.reduce<Record<string, number>>((acc, j) => ({ ...acc, [j.status]: (acc[j.status] ?? 0) + 1 }), {});
  const visible = tab === "all" ? list : list.filter((j) => j.status === tab);
  const live = list.some((j) => ["queued", "running"].includes(j.status));
  const open = (id: string | null) => router.replace(id ? `${pathname}?job=${id}` : pathname, { scroll: false });

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Processing jobs"
        description="Every ingestion attempt with its stages, timings, row reconciliation and logs. Failed and cancelled jobs keep their history and can be retried."
        actions={
          <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-ink-2 shadow-card">
            <span className={`size-2 rounded-full ${live ? "bg-accent animate-pulse-dot" : "bg-ink-faint"}`} />
            {live ? "Live · updating every 2 s" : "Idle"}
          </span>
        }
      />
      <div className="mb-4">
        <Segmented
          label="Status"
          size="md"
          value={tab}
          onChange={setTab}
          options={[
            { value: "all", label: `All ${list.length}` },
            { value: "running", label: `Running ${counts.running ?? 0}` },
            { value: "queued", label: `Queued ${counts.queued ?? 0}` },
            { value: "completed", label: `Completed ${counts.completed ?? 0}` },
            { value: "failed", label: `Failed ${counts.failed ?? 0}` },
            { value: "cancelled", label: `Cancelled ${counts.cancelled ?? 0}` },
          ]}
        />
      </div>
      <Card className="overflow-hidden">
        {jobs.isPending ? (
          <LoadingBlock />
        ) : jobs.isError ? (
          <ErrorBlock error={jobs.error} onRetry={() => void jobs.refetch()} />
        ) : visible.length === 0 ? (
          <EmptyBlock title={tab === "all" ? "No jobs yet" : `No ${tab} jobs`}>Queue a run from the Data sources page.</EmptyBlock>
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[900px] text-sm">
              <thead className="bg-surface-2 text-left text-xs text-ink-muted">
                <tr>
                  {["Status", "Source", "Progress", "Rows read → published", "Duration", "Started"].map((h) => (
                    <th key={h} scope="col" className="px-4 py-2.5 font-medium first:pl-5">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((job) => {
                  const run = job.runs.at(-1);
                  return (
                    <tr
                      key={job.job_id}
                      data-testid={`job-${job.job_id}`}
                      tabIndex={0}
                      onClick={() => open(job.job_id)}
                      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && open(job.job_id)}
                      className="cursor-pointer border-t border-line outline-none transition-colors hover:bg-surface-2/70 focus-visible:bg-accent-soft"
                    >
                      <td className="px-4 py-3 pl-5">
                        <StatusBadge status={job.status} />
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-medium text-ink">{job.period}</p>
                        <p className="text-xs text-ink-muted">
                          <span className="font-mono">{job.source_key}</span> · attempt {job.attempt} · {job.trigger}
                        </p>
                      </td>
                      <td className="px-4 py-3">
                        <StageBar run={run} />
                        <p className="mt-1 text-[11px] text-ink-muted">{job.status === "running" ? (run?.current_stage?.replace(/_/g, " ") ?? "starting") : job.status === "queued" ? "waiting for a worker" : ""}</p>
                      </td>
                      <td className="px-4 py-3 tabular text-xs text-ink-2">
                        {run?.input_rows != null ? (
                          <>
                            {formatInteger(run.input_rows)} → <span className="font-medium text-ink">{formatInteger(run.clickhouse_rows)}</span>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-3 tabular text-xs text-ink-2">{formatDuration(jobDuration(job))}</td>
                      <td className="px-4 py-3 text-xs text-ink-2">{formatRelative(job.started_at ?? job.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {selected ? <JobDetail jobId={selected} onClose={() => open(null)} isAdmin={me.data?.role === "admin"} /> : null}
    </div>
  );
}
