import { CircleAlert, Lock, SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "@/api/client";

import { Button } from "./button";

export function LoadingBlock({ label = "Loading…", className = "h-48" }: { label?: string; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={`flex items-center justify-center ${className}`}>
      <div className="flex items-center gap-2 text-sm text-ink-muted">
        <span className="size-2 animate-pulse rounded-full bg-accent" />
        {label}
      </div>
    </div>
  );
}

export function EmptyBlock({ title, children, className = "h-48" }: { title: string; children?: ReactNode; className?: string }) {
  return (
    <div className={`flex flex-col items-center justify-center gap-1.5 px-6 text-center ${className}`}>
      <SearchX className="mb-1 size-5 text-ink-faint" aria-hidden />
      <p className="text-sm font-medium text-ink">{title}</p>
      {children ? <p className="max-w-md text-sm text-ink-muted">{children}</p> : null}
    </div>
  );
}

export function ErrorBlock({ error, onRetry, className = "h-48" }: { error: unknown; onRetry?: () => void; className?: string }) {
  const apiError = error instanceof ApiError ? error : null;
  if (apiError?.status === 403) {
    return (
      <div className={`flex flex-col items-center justify-center gap-1.5 px-6 text-center ${className}`}>
        <Lock className="mb-1 size-5 text-ink-faint" aria-hidden />
        <p className="text-sm font-medium text-ink">Your role can’t view this</p>
        <p className="max-w-md text-sm text-ink-muted">Ask an administrator for analyst or admin access.</p>
      </div>
    );
  }
  return (
    <div role="alert" className={`flex flex-col items-center justify-center gap-2 px-6 text-center ${className}`}>
      <CircleAlert className="size-5 text-critical" aria-hidden />
      <p className="text-sm font-medium text-ink">Couldn’t load this data</p>
      <p className="max-w-md text-sm text-ink-muted">{apiError?.message ?? "The server could not be reached."}</p>
      {apiError?.requestId ? (
        <p className="text-xs text-ink-faint">
          Request ID <span className="font-mono">{apiError.requestId}</span>
        </p>
      ) : null}
      {onRetry ? (
        <Button size="sm" onClick={onRetry} className="mt-1">
          Retry
        </Button>
      ) : null}
    </div>
  );
}
