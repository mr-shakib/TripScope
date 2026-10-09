import { Compass, FileText, Settings, Sparkles } from "lucide-react";

const ICONS = { Compass, FileText, Settings, Sparkles };

/** Sections that are not built yet: clearly labelled, never filled with placeholder data. */
export function Planned({ title, phase, summary, icon }: { title: string; phase: number; summary: string; icon: keyof typeof ICONS }) {
  const Icon = ICONS[icon];
  return (
    <div className="mx-auto mt-10 max-w-xl text-center animate-fade-in">
      <div className="mx-auto flex size-14 items-center justify-center rounded-2xl bg-accent-soft text-accent ring-8 ring-accent-soft/40">
        <Icon className="size-6" />
      </div>
      <p className="mt-6 text-xs font-semibold uppercase tracking-[0.12em] text-accent-strong">Planned · Phase {phase}</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">{title}</h1>
      <p className="mt-3 text-sm leading-relaxed text-ink-2">{summary}</p>
      <p className="mt-6 text-xs text-ink-muted">This section stays empty until it is backed by real data and tests.</p>
    </div>
  );
}
