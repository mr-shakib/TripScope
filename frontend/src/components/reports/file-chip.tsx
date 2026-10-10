"use client";

import { CircleX, Download, FileSpreadsheet, FileText, LoaderCircle, Sheet } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { downloadReport } from "@/api/hooks";
import type { ReportFormat, ReportRun } from "@/api/types";
import { cn } from "@/lib/cn";

export const FORMAT_LABELS: Record<ReportFormat, string> = { pdf: "PDF", xlsx: "Excel", csv: "CSV" };
export const FORMAT_ICONS: Record<ReportFormat, typeof FileText> = { pdf: FileText, xlsx: FileSpreadsheet, csv: Sheet };

/** Save a completed run's file; errors become a toast with the API's message. */
export function useDownload(reportId: string) {
  const [busy, setBusy] = useState<string | null>(null);
  const download = async (run: ReportRun) => {
    setBusy(run.run_id);
    try {
      const name = await downloadReport(reportId, run.run_id);
      toast.success(`Downloaded ${name}`);
    } catch (error) {
      toast.error("Download failed", { description: error instanceof ApiError ? error.message : undefined });
    } finally {
      setBusy(null);
    }
  };
  return { busy, download };
}

/** Latest file of one format: downloadable only once it is complete (spec §11). */
export function FileChip({ reportId, run, title }: { reportId: string; run: ReportRun; title: string }) {
  const { busy, download } = useDownload(reportId);
  const label = FORMAT_LABELS[run.format];
  if (run.status === "completed") {
    return (
      <button
        type="button"
        onClick={() => void download(run)}
        disabled={busy !== null}
        aria-label={`Download ${label} of ${title}`}
        className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent-strong transition-colors hover:bg-accent/20 disabled:opacity-60"
      >
        {busy ? <LoaderCircle className="size-3 animate-spin" /> : <Download className="size-3" />} {label}
      </button>
    );
  }
  const failed = run.status === "failed" || run.status === "cancelled";
  return (
    <span
      title={failed ? (run.error_summary ?? "Failed") : "Being generated"}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium",
        failed ? "bg-critical-soft text-critical-ink" : "bg-surface-3 text-ink-2",
      )}
    >
      {failed ? <CircleX className="size-3" /> : <LoaderCircle className="size-3 animate-spin" />} {label}
    </span>
  );
}
