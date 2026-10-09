import { Ban, CircleCheck, CircleDashed, CircleX, LoaderCircle } from "lucide-react";

import type { JobStatus } from "@/api/types";
import { cn } from "@/lib/cn";

const STYLES: Record<JobStatus, { label: string; className: string; icon: typeof CircleCheck }> = {
  queued: { label: "Queued", className: "bg-surface-3 text-ink-2 ring-line-strong", icon: CircleDashed },
  running: { label: "Running", className: "bg-accent-soft text-accent-strong ring-accent/30", icon: LoaderCircle },
  completed: { label: "Completed", className: "bg-good-soft text-good-ink ring-good/25", icon: CircleCheck },
  failed: { label: "Failed", className: "bg-critical-soft text-critical-ink ring-critical/25", icon: CircleX },
  cancelled: { label: "Cancelled", className: "bg-warning-soft text-warning-ink ring-warning/40", icon: Ban },
};

/** Status always pairs an icon and a label with its colour (never colour alone). */
export function StatusBadge({ status, className }: { status: JobStatus; className?: string }) {
  const style = STYLES[status];
  const Icon = style.icon;
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        style.className,
        className,
      )}
    >
      <Icon className={cn("size-3.5", status === "running" && "animate-spin")} aria-hidden />
      {style.label}
    </span>
  );
}

export function Pill({ children, tone = "neutral", className }: { children: React.ReactNode; tone?: "neutral" | "accent" | "good" | "warning" | "critical"; className?: string }) {
  const tones = {
    neutral: "bg-surface-3 text-ink-2",
    accent: "bg-accent-soft text-accent-strong",
    good: "bg-good-soft text-good-ink",
    warning: "bg-warning-soft text-warning-ink",
    critical: "bg-critical-soft text-critical-ink",
  };
  return <span className={cn("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium", tones[tone], className)}>{children}</span>;
}
