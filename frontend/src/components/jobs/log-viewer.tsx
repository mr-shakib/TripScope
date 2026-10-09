"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";

import type { LogEntry } from "@/api/types";
import { Segmented } from "@/components/ui/segmented";
import { cn } from "@/lib/cn";

const LEVEL_STYLE: Record<string, string> = {
  info: "text-accent-strong bg-accent-soft",
  warning: "text-warning-ink bg-warning-soft",
  error: "text-critical-ink bg-critical-soft",
  critical: "text-critical-ink bg-critical-soft",
  debug: "text-ink-muted bg-surface-3",
};

export function LogViewer({ logs }: { logs: LogEntry[] }) {
  const [level, setLevel] = useState<"all" | "warning">("all");
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return logs.filter(
      (e) =>
        (level === "all" || ["warning", "error", "critical"].includes(e.level)) &&
        (!needle || e.message.toLowerCase().includes(needle) || Object.values(e.fields).some((v) => v.toLowerCase().includes(needle))),
    );
  }, [logs, level, query]);

  return (
    <div className="rounded-xl border border-line">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-2 px-3 py-2">
        <Segmented label="Log level" value={level} onChange={setLevel} options={[{ value: "all", label: `All (${logs.length})` }, { value: "warning", label: "Warnings & errors" }]} />
        <div className="relative ml-auto">
          <Search className="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-ink-faint" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter logs" aria-label="Filter logs" className="h-7 w-44 rounded-md border border-line-strong bg-surface pl-7 pr-2 text-xs outline-none focus:border-accent" />
        </div>
      </div>
      <ol className="scroll-thin max-h-80 divide-y divide-line overflow-y-auto font-mono text-[11.5px] leading-relaxed" data-testid="run-logs">
        {visible.map((entry, i) => (
          <li key={`${entry.at}-${i}`} className="flex gap-2.5 px-3 py-1.5 hover:bg-surface-2">
            <span className="shrink-0 text-ink-faint">{entry.at.slice(11, 23)}</span>
            <span className={cn("h-fit shrink-0 rounded px-1 text-[10px] font-semibold uppercase", LEVEL_STYLE[entry.level] ?? LEVEL_STYLE.debug)}>{entry.level}</span>
            <span className="min-w-0 text-ink">
              {entry.message}
              {Object.keys(entry.fields).length ? (
                <span className="text-ink-muted">
                  {" "}
                  {Object.entries(entry.fields)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(" ")}
                </span>
              ) : null}
            </span>
          </li>
        ))}
        {visible.length === 0 ? <li className="px-3 py-6 text-center font-sans text-xs text-ink-muted">No log entries.</li> : null}
      </ol>
    </div>
  );
}
