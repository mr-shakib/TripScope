"use client";

import { ArrowRight, LoaderCircle } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { useLogin, useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";

/** Only same-site relative paths are honoured as post-login destinations (no open redirects). */
function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

export function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();
  const me = useMe();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));

  useEffect(() => {
    if (me.data) router.replace(next);
  }, [me.data, next, router]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => router.replace(next) });
  };
  const error = login.error instanceof ApiError ? login.error.message : login.error ? "Sign-in failed." : null;

  return (
    <form onSubmit={submit} className="w-full max-w-sm animate-fade-in">
      <h2 className="text-2xl font-semibold tracking-tight text-ink">Welcome back</h2>
      <p className="mt-1.5 text-sm text-ink-2">Sign in to explore validated NYC trip data.</p>
      <div className="mt-8 space-y-4">
        <label className="block">
          <span className="text-[13px] font-medium text-ink-2">Email</span>
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1.5 h-10 w-full rounded-lg border border-line-strong bg-surface px-3 text-sm text-ink shadow-card outline-none transition-shadow placeholder:text-ink-faint focus:border-accent focus:ring-4 focus:ring-[var(--accent-ring)]"
            placeholder="you@example.org"
          />
        </label>
        <label className="block">
          <span className="text-[13px] font-medium text-ink-2">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1.5 h-10 w-full rounded-lg border border-line-strong bg-surface px-3 text-sm text-ink shadow-card outline-none transition-shadow focus:border-accent focus:ring-4 focus:ring-[var(--accent-ring)]"
          />
        </label>
      </div>
      {error ? (
        <p role="alert" className="mt-4 rounded-lg bg-critical-soft px-3 py-2 text-sm text-critical-ink">
          {error}
        </p>
      ) : null}
      <Button type="submit" variant="primary" disabled={login.isPending} className="mt-6 h-10 w-full">
        {login.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
        {login.isPending ? "Signing in…" : "Sign in"}
        {!login.isPending ? <ArrowRight className="size-4" /> : null}
      </Button>
      <p className="mt-6 text-center text-xs text-ink-muted">Accounts are created by an administrator.</p>
    </form>
  );
}
