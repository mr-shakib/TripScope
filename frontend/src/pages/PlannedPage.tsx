export function PlannedPage({ title, phase, summary }: { title: string; phase: number; summary: string }) {
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-line bg-surface-1 p-6">
      <p className="text-xs uppercase tracking-wide text-ink-muted">Planned · Phase {phase}</p>
      <h1 className="mt-1 text-xl font-semibold text-ink">{title}</h1>
      <p className="mt-2 text-sm text-ink-2">{summary}</p>
      <p className="mt-4 text-xs text-ink-muted">
        This section is intentionally empty until it is backed by real data and tests (see docs/implementation-plan.md).
      </p>
    </div>
  );
}
