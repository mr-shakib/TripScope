"use client";

import { ArrowUpRight, FileWarning, Lock, LockOpen, Play, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useDataSources, useMe, useQueueJob } from "@/api/hooks";
import type { DataSourceItem } from "@/api/types";
import { PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog, Tooltip } from "@/components/ui/overlays";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { Pill, StatusBadge } from "@/components/ui/status";
import { formatBytes, formatDate, formatInteger, formatRelative } from "@/lib/format";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-line bg-surface px-5 py-4 shadow-card">
      <p className="text-xs font-medium text-ink-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-ink tabular">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-ink-muted">{hint}</p> : null}
    </div>
  );
}

function host(uri: string): string {
  try {
    return new URL(uri).hostname;
  } catch {
    return uri;
  }
}

export function SourcesView() {
  const sources = useDataSources();
  const me = useMe();
  const queue = useQueueJob();
  const [confirm, setConfirm] = useState<DataSourceItem | null>(null);
  const router = useRouter();
  const isAdmin = me.data?.role === "admin";

  const run = (source: DataSourceItem) => {
    queue.mutate(source.source_key, {
      onSuccess: (job) =>
        toast.success(`Queued ${source.source_key}`, {
          description: "The worker will pick it up in a moment.",
          action: { label: "View job", onClick: () => router.push(`/jobs?job=${job.job_id}`) },
        }),
      onError: (error) =>
        toast.error("Couldn’t queue the run", { description: error instanceof ApiError ? error.message : "Unexpected error" }),
    });
  };

  const list = sources.data ?? [];
  const published = list.filter((s) => s.published);
  const rows = published.reduce((sum, s) => sum + (s.published?.row_count ?? 0), 0);
  const bytes = list.reduce((sum, s) => sum + (s.file_size_bytes ?? 0), 0);
  const active = list.filter((s) => s.latest_job && ["queued", "running"].includes(s.latest_job.status)).length;

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Data sources"
        description="Monthly TLC files listed in the source manifest, with their checksum pin, schema and publication state. Runs are idempotent: re-running a month replaces it."
        actions={
          <Button variant="secondary" size="sm" onClick={() => void sources.refetch()}>
            <RefreshCw className={`size-3.5 ${sources.isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
        }
      />
      {sources.isPending ? (
        <LoadingBlock />
      ) : sources.isError ? (
        <Card>
          <ErrorBlock error={sources.error} onRetry={() => void sources.refetch()} />
        </Card>
      ) : (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Sources in manifest" value={String(list.length)} hint={`${list.filter((s) => s.checksum_pinned).length} with pinned checksums`} />
            <Stat label="Published months" value={`${published.length} / ${list.length}`} hint={active ? `${active} run${active > 1 ? "s" : ""} in progress` : "No runs in progress"} />
            <Stat label="Published trips" value={formatInteger(rows)} hint="Accepted rows in ClickHouse" />
            <Stat label="Raw data received" value={formatBytes(bytes)} hint="Immutable copies in the lake" />
          </div>
          <Card className="overflow-hidden">
            {list.length === 0 ? (
              <EmptyBlock title="The manifest lists no sources" />
            ) : (
              <div className="scroll-thin overflow-x-auto">
                <table className="w-full min-w-[980px] text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-ink-muted">
                    <tr>
                      {["Source", "Status", "Published rows", "Coverage", "File", "Schema", "Last run", ""].map((h) => (
                        <th key={h} scope="col" className="px-4 py-2.5 font-medium first:pl-5">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((source) => {
                      const job = source.latest_job;
                      const busy = Boolean(job && ["queued", "running"].includes(job.status));
                      return (
                        <tr key={source.source_key} data-testid={`source-${source.source_key}`} className="border-t border-line transition-colors hover:bg-surface-2/70">
                          <td className="px-4 py-3 pl-5">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-ink">{source.period}</span>
                              <Pill tone={source.format === "csv" ? "warning" : "neutral"}>{source.format.toUpperCase()}</Pill>
                            </div>
                            <p className="mt-0.5 text-xs text-ink-muted">
                              <span className="font-mono">{source.source_key}</span> · {host(source.uri)}
                            </p>
                          </td>
                          <td className="px-4 py-3">
                            {job ? <StatusBadge status={job.status} /> : <Pill>Not ingested</Pill>}
                            {job?.status === "failed" && job.error_summary ? (
                              <Tooltip content={job.error_summary}>
                                <p className="mt-1 flex max-w-[200px] items-center gap-1 truncate text-xs text-critical-ink">
                                  <FileWarning className="size-3.5 shrink-0" /> {job.error_summary}
                                </p>
                              </Tooltip>
                            ) : null}
                          </td>
                          <td className="px-4 py-3 tabular font-medium text-ink">{source.published ? formatInteger(source.published.row_count) : "—"}</td>
                          <td className="whitespace-nowrap px-4 py-3 text-xs text-ink-2">
                            {source.published ? `${formatDate(source.published.min_pickup_date)} – ${formatDate(source.published.max_pickup_date)}` : "—"}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-1.5 text-xs text-ink-2">
                              <span className="tabular whitespace-nowrap">{formatBytes(source.file_size_bytes)}</span>
                              <Tooltip content={source.checksum_pinned ? `SHA-256 pinned in the manifest${source.sha256 ? `: ${source.sha256.slice(0, 16)}…` : ""}` : "No checksum pin: upstream changes would not be detected"}>
                                <span className={source.checksum_pinned ? "text-good-ink" : "text-warning-ink"}>
                                  {source.checksum_pinned ? <Lock className="size-3.5" aria-label="checksum pinned" /> : <LockOpen className="size-3.5" aria-label="checksum not pinned" />}
                                </span>
                              </Tooltip>
                            </div>
                          </td>
                          <td className="px-4 py-3">
                            {source.schema_version ? <span className="whitespace-nowrap rounded-md bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-ink-2">{source.schema_version}</span> : "—"}
                          </td>
                          <td className="px-4 py-3 text-xs text-ink-2">
                            {job ? (
                              <Link href={`/jobs?job=${job.job_id}`} className="group inline-flex items-center gap-1 hover:text-accent-strong">
                                {formatRelative(job.finished_at ?? job.created_at)} · attempt {job.attempt}
                                <ArrowUpRight className="size-3 opacity-0 transition-opacity group-hover:opacity-100" />
                              </Link>
                            ) : (
                              "—"
                            )}
                          </td>
                          <td className="px-4 py-3 pr-5 text-right">
                            {isAdmin ? (
                              <Button size="sm" variant={source.published ? "secondary" : "primary"} disabled={busy || queue.isPending} onClick={() => setConfirm(source)} data-testid={`run-${source.source_key}`}>
                                <Play className="size-3.5" /> {busy ? "In progress" : source.published ? "Re-run" : "Run"}
                              </Button>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          {!isAdmin ? <p className="mt-3 text-xs text-ink-muted">Only administrators can start runs.</p> : null}
        </>
      )}
      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={confirm?.published ? `Re-run ${confirm.period}?` : `Run ${confirm?.period}?`}
        description={
          confirm?.published
            ? "The file is re-validated and re-processed. The month is replaced only after every check passes; until then the current data stays published."
            : "The file is downloaded, verified against its checksum, transformed with Spark and loaded. The month becomes visible once every check passes."
        }
        confirmLabel={confirm?.published ? "Queue re-run" : "Queue run"}
        onConfirm={() => confirm && run(confirm)}
      />
    </div>
  );
}
