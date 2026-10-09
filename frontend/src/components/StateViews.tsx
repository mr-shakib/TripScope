import { ApiError } from "../api/client";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex min-h-40 items-center justify-center text-sm text-ink-muted">
      {label}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-1 px-6 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children ? <p className="max-w-md text-sm text-ink-2">{children}</p> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className="flex min-h-40 flex-col items-center justify-center gap-2 px-6 text-center">
      <p className="text-sm font-medium text-critical">Couldn’t load this data</p>
      <p className="max-w-md text-sm text-ink-2">{apiError?.message ?? "The server could not be reached."}</p>
      {apiError?.requestId ? (
        <p className="text-xs text-ink-muted">
          Request ID <span className="tabular font-mono">{apiError.requestId}</span>
        </p>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 rounded-md border border-line px-3 py-1.5 text-sm text-ink hover:bg-surface-2"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}
