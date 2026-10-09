import { Ban, Check, CircleDashed, LoaderCircle, X } from "lucide-react";

import type { RunSummary } from "@/api/types";
import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";
import { STAGES } from "@/lib/quality-labels";

type StageState = "done" | "active" | "failed" | "pending";
type StageStateWithStop = StageState | "stopped";

/** Stage states in pipeline order. Stages run sequentially, so in an unsuccessful run the last timed stage (by
 * pipeline order, not JSON key order, which PostgreSQL does not preserve) is where it stopped. */
export function stageStates(run: RunSummary | undefined): Record<string, StageStateWithStop> {
  const states: Record<string, StageStateWithStop> = {};
  const timed = STAGES.map((s) => Boolean(run && s.id in run.stage_timings));
  const lastTimed = timed.lastIndexOf(true);
  STAGES.forEach((stage, index) => {
    if (!run || !timed[index]) {
      states[stage.id] = run?.status === "running" && run.current_stage === stage.id ? "active" : "pending";
    } else if (index === lastTimed && run.status === "failed") states[stage.id] = "failed";
    else if (index === lastTimed && run.status === "cancelled") states[stage.id] = "stopped";
    else states[stage.id] = "done";
  });
  return states;
}

/** Compact 8-segment progress bar for tables. */
export function StageBar({ run }: { run: RunSummary | undefined }) {
  const states = stageStates(run);
  return (
    <div className="flex w-36 gap-0.5" aria-label="Stage progress">
      {STAGES.map((stage) => (
        <span
          key={stage.id}
          title={stage.label}
          className={cn(
            "h-1.5 flex-1 rounded-full",
            states[stage.id] === "done" && "bg-good",
            states[stage.id] === "active" && "animate-pulse bg-accent",
            states[stage.id] === "failed" && "bg-critical",
            states[stage.id] === "stopped" && "bg-warning",
            states[stage.id] === "pending" && "bg-surface-3",
          )}
        />
      ))}
    </div>
  );
}

export function StageTimeline({ run }: { run: RunSummary | undefined }) {
  const states = stageStates(run);
  const total = Math.max(...Object.values(run?.stage_timings ?? {}), 0.001);
  return (
    <ol className="relative space-y-0.5" aria-label="Pipeline stages">
      {STAGES.map((stage, index) => {
        const state = states[stage.id];
        const seconds = run?.stage_timings[stage.id];
        return (
          <li key={stage.id} className="relative flex items-center gap-3 py-1.5">
            {index < STAGES.length - 1 ? (
              <span aria-hidden className={cn("absolute left-[11px] top-7 h-[calc(100%-12px)] w-px", state === "done" ? "bg-good/40" : "bg-line")} />
            ) : null}
            <span
              className={cn(
                "relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full ring-4 ring-surface",
                state === "done" && "bg-good text-white",
                state === "active" && "bg-accent text-white",
                state === "failed" && "bg-critical text-white",
                state === "stopped" && "bg-warning text-white",
                state === "pending" && "bg-surface-3 text-ink-faint",
              )}
            >
              {state === "done" ? <Check className="size-3.5" strokeWidth={3} /> : null}
              {state === "active" ? <LoaderCircle className="size-3.5 animate-spin" /> : null}
              {state === "failed" ? <X className="size-3.5" strokeWidth={3} /> : null}
              {state === "stopped" ? <Ban className="size-3.5" strokeWidth={2.5} /> : null}
              {state === "pending" ? <CircleDashed className="size-3.5" /> : null}
            </span>
            <span className={cn("w-48 shrink-0 text-[13px]", state === "pending" ? "text-ink-muted" : "font-medium text-ink")}>{stage.label}</span>
            <span className="flex flex-1 items-center gap-2">
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                {seconds !== undefined ? (
                  <span className={cn("block h-full rounded-full", state === "failed" ? "bg-critical" : state === "stopped" ? "bg-warning" : "bg-accent/70")} style={{ width: `${Math.max((seconds / total) * 100, 2)}%` }} />
                ) : null}
              </span>
              <span className="tabular w-14 text-right text-xs text-ink-muted">{seconds !== undefined ? formatDuration(seconds) : state === "active" ? "…" : ""}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
