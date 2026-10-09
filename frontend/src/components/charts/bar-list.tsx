"use client";

import { Check } from "lucide-react";

import { cn } from "@/lib/cn";

export interface BarItem {
  key: string | number;
  label: string;
  sublabel?: string | null;
  value: number;
  display: string;
  share?: string;
  muted?: boolean;
}

/** Ranked horizontal bars rendered as HTML (crisp labels, real buttons when clickable). */
export function BarList({
  items,
  selected = [],
  onSelect,
  ariaLabel,
}: {
  items: BarItem[];
  selected?: (string | number)[];
  onSelect?: (key: string | number) => void;
  ariaLabel: string;
}) {
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <ul aria-label={ariaLabel} className="space-y-1">
      {items.map((item) => {
        const isSelected = selected.includes(item.key);
        const content = (
          <>
            <span
              aria-hidden
              className={cn(
                "absolute inset-y-0.5 left-0 rounded-md transition-[width,background] duration-300",
                item.muted ? "bg-surface-3" : isSelected ? "bg-accent/25" : "bg-accent/12 group-hover:bg-accent/20",
              )}
              style={{ width: `${Math.max((item.value / max) * 100, 1.5)}%` }}
            />
            <span className="relative flex min-w-0 items-center gap-2">
              {onSelect ? (
                <span
                  className={cn(
                    "flex size-4 shrink-0 items-center justify-center rounded border transition-colors",
                    isSelected ? "border-accent bg-accent text-white" : "border-line-strong bg-surface",
                  )}
                >
                  {isSelected ? <Check className="size-3" strokeWidth={3} /> : null}
                </span>
              ) : null}
              <span className="truncate text-[13px] font-medium text-ink">{item.label}</span>
              {item.sublabel ? <span className="shrink-0 text-xs text-ink-muted">{item.sublabel}</span> : null}
            </span>
            <span className="relative ml-3 flex shrink-0 items-baseline gap-2">
              <span className="tabular text-[13px] font-semibold text-ink">{item.display}</span>
              {item.share ? <span className="tabular w-12 text-right text-xs text-ink-muted">{item.share}</span> : null}
            </span>
          </>
        );
        return (
          <li key={item.key}>
            {onSelect ? (
              <button
                type="button"
                aria-pressed={isSelected}
                onClick={() => onSelect(item.key)}
                className="group relative flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left"
              >
                {content}
              </button>
            ) : (
              <div className="group relative flex items-center justify-between rounded-md px-2 py-1.5">{content}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
