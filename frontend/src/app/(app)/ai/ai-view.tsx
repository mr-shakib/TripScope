"use client";

import { Cpu, Globe, MessageSquarePlus, Send, Sparkles, Trash2, TriangleAlert } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useAIConversation, useAIConversations, useAIStatus, useAsk, useDataset, useDeleteConversation } from "@/api/hooks";
import type { AIMessage, AIStatus } from "@/api/types";
import { AIChartView, AnswerText, Evidence, messageSummary, ToolSteps, VerificationBadge } from "@/components/ai/answer";
import { FilterBar } from "@/components/filters/filter-bar";
import { PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/overlays";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "@/components/ui/states";
import { Pill } from "@/components/ui/status";
import { useUrlFilters } from "@/hooks/use-url-filters";
import { cn } from "@/lib/cn";
import { activeFilterCount } from "@/lib/filters";
import { formatRelative } from "@/lib/format";

function StatusPill({ status }: { status: AIStatus }) {
  if (!status.enabled) {
    return (
      <Tooltip content={status.reason ?? "No model configured"}>
        <span data-testid="ai-status" data-enabled="false">
          <Pill tone="warning">
            <TriangleAlert className="size-3" /> Demo mode · no model configured
          </Pill>
        </span>
      </Tooltip>
    );
  }
  return (
    <Tooltip content={status.local ? "Runs on this machine or network; no data leaves it." : "An external provider: only bounded aggregates from the tools are sent, never trip rows."}>
      <span data-testid="ai-status" data-enabled="true">
        <Pill tone={status.local ? "good" : "accent"}>
          {status.local ? <Cpu className="size-3" /> : <Globe className="size-3" />}
          {status.local ? "Local model" : "External provider"} · {status.model}
        </Pill>
      </span>
    </Tooltip>
  );
}

function Assistant({ message, onFollowUp, disabled }: { message: AIMessage; onFollowUp: (q: string) => void; disabled: boolean }) {
  const running = message.status === "running";
  const verification = message.payload.verification;
  return (
    <div className="space-y-3 rounded-xl border border-line bg-surface p-4 shadow-card" data-testid="ai-message" data-status={message.status} data-mode={message.mode}>
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-muted">
        <Sparkles className="size-3.5 text-accent" />
        {message.mode === "demo" ? <Pill tone="accent">Demo answer: fixed rules over tool results, no language model</Pill> : <span>Analyst</span>}
        {!running && messageSummary(message) ? <span>{messageSummary(message)}</span> : null}
      </div>
      {running || (message.tool_runs.length > 0 && message.status !== "answered") ? (
        <ToolSteps runs={message.tool_runs} running={running} />
      ) : null}
      {message.status === "answered" ? (
        <>
          <AnswerText text={message.content} claims={verification?.claims ?? []} />
          <VerificationBadge total={verification?.total ?? 0} verified={verification?.verified ?? 0} />
          {message.payload.chart ? <AIChartView chart={message.payload.chart} /> : null}
          {message.payload.caveats?.length ? (
            <ul className="space-y-1 text-[12.5px] text-ink-2">
              {message.payload.caveats.map((c) => (
                <li key={c} className="flex gap-2">
                  <span className="mt-[7px] size-1 shrink-0 rounded-full bg-ink-faint" aria-hidden /> {c}
                </li>
              ))}
            </ul>
          ) : null}
          <Evidence runs={message.tool_runs} />
        </>
      ) : null}
      {message.status === "clarification" ? (
        <p className="text-[14px] text-ink" data-testid="ai-clarification">
          {message.content}
        </p>
      ) : null}
      {message.status === "failed" ? (
        <p className="text-[13px] text-critical-ink" role="alert">
          The analyst could not answer: {message.payload.error ?? "unknown error"}. No figures were invented.
        </p>
      ) : null}
      {message.payload.follow_ups?.length ? (
        <div className="flex flex-wrap gap-1.5">
          {message.payload.follow_ups.map((q) => (
            <button key={q} type="button" disabled={disabled} onClick={() => onFollowUp(q)} className="rounded-full border border-line-strong bg-surface px-2.5 py-1 text-[12px] text-ink-2 hover:border-accent/40 hover:text-accent-strong disabled:opacity-50">
              {q}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function AIView() {
  const status = useAIStatus();
  const dataset = useDataset();
  const [filters, setFilters] = useUrlFilters();
  const params = useSearchParams();
  const [conversationId, setConversationId] = useState<string | null>(() => params.get("c"));
  const [draft, setDraft] = useState("");
  const [useContext, setUseContext] = useState(true);
  const canChat = Boolean(status.data?.can_chat);
  const conversations = useAIConversations(canChat);
  const conversation = useAIConversation(conversationId);
  const ask = useAsk();
  const remove = useDeleteConversation();
  const running = conversation.data?.messages.at(-1)?.status === "running";
  const enabled = Boolean(status.data?.enabled);
  const contextFilters = useContext && activeFilterCount(filters) > 0 ? filters : null;

  const select = (id: string | null) => {
    setConversationId(id);
    const query = new URLSearchParams(window.location.search);
    if (id) query.set("c", id);
    else query.delete("c");
    window.history.replaceState(null, "", `${window.location.pathname}${query.size ? `?${query}` : ""}`);
  };

  const send = (input: { message?: string; demo_id?: string }) =>
    ask.mutate(
      { ...input, conversation_id: conversationId, filters: contextFilters },
      {
        onSuccess: (result) => {
          select(result.conversation_id);
          setDraft("");
        },
        onError: (error) => toast.error("Could not ask", { description: error instanceof ApiError ? error.message : undefined }),
      },
    );

  if (status.isPending) return <LoadingBlock className="h-[60vh]" />;
  if (status.isError) return <ErrorBlock error={status.error} className="h-[60vh]" />;
  const info = status.data;

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="AI analyst"
        description="Ask questions in plain language. Answers come only from approved analytics tools; every figure is checked against their results, and the evidence is one click away."
        actions={<StatusPill status={info} />}
      />
      {!info.can_chat ? (
        <EmptyBlock title="The AI analyst is not available to your role" className="h-64">
          Viewers can use it when an administrator turns on AI_ALLOW_VIEWERS.
        </EmptyBlock>
      ) : (
        <div className="grid items-start gap-6 xl:grid-cols-[260px_minmax(0,1fr)]">
          <Card className="xl:sticky xl:top-20">
            <div className="flex items-center justify-between px-4 pt-3">
              <h2 className="text-[13px] font-semibold text-ink">Conversations</h2>
              <Button size="sm" variant="ghost" onClick={() => select(null)} aria-label="New conversation" data-testid="ai-new">
                <MessageSquarePlus className="size-4" />
              </Button>
            </div>
            <ul className="max-h-[60vh] overflow-y-auto px-2 pb-3 pt-1" data-testid="ai-conversations">
              {(conversations.data ?? []).map((c) => (
                <li key={c.conversation_id} className="group flex items-center">
                  <button type="button" onClick={() => select(c.conversation_id)} className={cn("min-w-0 flex-1 rounded-md px-2 py-1.5 text-left", c.conversation_id === conversationId ? "bg-accent-soft" : "hover:bg-surface-2")}>
                    <span className="block truncate text-[12.5px] text-ink">{c.title}</span>
                    <span className="block text-[11px] text-ink-muted">{formatRelative(c.updated_at)}</span>
                  </button>
                  <button
                    type="button"
                    aria-label={`Delete ${c.title}`}
                    className="p-1 text-ink-faint opacity-0 hover:text-critical-ink group-hover:opacity-100 focus-visible:opacity-100"
                    onClick={() =>
                      remove.mutate(c.conversation_id, { onSuccess: () => c.conversation_id === conversationId && select(null) })
                    }
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </li>
              ))}
              {conversations.data?.length === 0 ? <li className="px-2 py-3 text-[12px] text-ink-muted">No conversations yet.</li> : null}
            </ul>
            <p className="border-t border-line px-4 py-2.5 text-[11px] text-ink-muted">Kept for {info.retention_days} days, visible only to you.</p>
          </Card>

          <Card className="flex min-h-[70vh] flex-col">
            <div className="space-y-2 border-b border-line px-5 py-3">
              <FilterBar coverage={dataset.data} filters={filters} onChange={setFilters} />
              <label className="flex items-center gap-2 text-[12px] text-ink-2">
                <input type="checkbox" checked={useContext} onChange={(e) => setUseContext(e.target.checked)} className="accent-[var(--accent)]" data-testid="ai-use-filters" />
                Give these filters to the analyst as context (it says which filters its numbers cover)
              </label>
            </div>
            <div className="flex-1 space-y-4 px-5 py-5" data-testid="ai-thread">
              {!conversationId ? (
                <div className="mx-auto max-w-2xl space-y-4 py-6 text-center">
                  <Sparkles className="mx-auto size-8 text-accent" />
                  <p className="text-[14px] text-ink-2">
                    {enabled
                      ? "Ask about trips, fares, distances, zones, time patterns or data quality. The analyst may ask a clarifying question."
                      : "No language model is configured, so free questions are off. The demo questions below run the same tools and checks with fixed answer rules."}
                  </p>
                  <div className="flex flex-wrap justify-center gap-2" data-testid="ai-demo-questions">
                    {info.demo_questions.map((q) => (
                      <button key={q.id} type="button" disabled={ask.isPending} onClick={() => send({ demo_id: q.id })} className="rounded-full border border-line-strong bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-accent/40 hover:text-accent-strong disabled:opacity-50" data-testid={`demo-${q.id}`}>
                        {q.text}
                      </button>
                    ))}
                  </div>
                  {info.suggestions.length ? (
                    <div className="flex flex-wrap justify-center gap-2">
                      {info.suggestions.map((q) => (
                        <button key={q} type="button" onClick={() => setDraft(q)} className="rounded-full bg-surface-3 px-3 py-1.5 text-[12.5px] text-ink-2 hover:text-ink">
                          {q}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : conversation.isPending ? (
                <LoadingBlock className="h-64" />
              ) : conversation.isError ? (
                <ErrorBlock error={conversation.error} className="h-64" />
              ) : (
                conversation.data.messages.map((m) =>
                  m.role === "user" ? (
                    <div key={m.message_id} className="flex justify-end">
                      <p className="max-w-[80%] rounded-2xl rounded-br-md bg-accent px-4 py-2 text-[14px] text-white" data-testid="ai-question">
                        {m.content}
                      </p>
                    </div>
                  ) : (
                    <Assistant key={m.message_id} message={m} disabled={running || ask.isPending || !enabled} onFollowUp={(q) => send({ message: q })} />
                  ),
                )
              )}
            </div>
            <form
              className="flex items-end gap-2 border-t border-line px-5 py-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (draft.trim()) send({ message: draft.trim() });
              }}
            >
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (draft.trim() && enabled && !running) send({ message: draft.trim() });
                  }
                }}
                rows={2}
                maxLength={2000}
                disabled={!enabled}
                placeholder={enabled ? "Ask a question… (Enter to send, Shift+Enter for a new line)" : "Configure LLM_PROVIDER to ask your own questions"}
                aria-label="Question"
                data-testid="ai-input"
                className="min-h-[44px] flex-1 resize-none rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-surface-2"
              />
              <Button type="submit" variant="primary" disabled={!enabled || running || ask.isPending || !draft.trim()} data-testid="ai-send">
                <Send className="size-4" /> {running ? "Answering…" : "Ask"}
              </Button>
            </form>
          </Card>
        </div>
      )}
    </div>
  );
}
