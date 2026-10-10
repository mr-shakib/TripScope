"use client";

import { BadgeCheck, LoaderCircle, RefreshCw, Sparkles, TriangleAlert, Wand2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useDraftReport, useRedraftReport, useReportOutline } from "@/api/hooks";
import type { AIOutline, DashboardFilters, NarrativeStatus } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/overlays";
import { formatRelative } from "@/lib/format";

/** FR-11: describe the report → the model proposes an outline → edit it → the report is drafted. */
export function AIDraftDrawer({ open, onOpenChange, filters, periodLabel }: { open: boolean; onOpenChange: (open: boolean) => void; filters: DashboardFilters; periodLabel: string }) {
  const router = useRouter();
  const outline = useReportOutline();
  const draft = useDraftReport();
  const [focus, setFocus] = useState("");
  const [plan, setPlan] = useState<AIOutline | null>(null);
  const [title, setTitle] = useState("");
  const [sections, setSections] = useState<string[]>([]);

  const propose = () =>
    outline.mutate(
      { filters, focus },
      {
        onSuccess: (result) => {
          setPlan(result);
          setTitle(result.title);
          setSections(result.sections);
        },
        onError: (error) => toast.error("No outline", { description: error instanceof ApiError ? error.message : undefined }),
      },
    );

  const create = () =>
    draft.mutate(
      { title, filters, sections: plan ? plan.library.map((s) => s.id).filter((id) => sections.includes(id)) : sections, focus },
      {
        onSuccess: ({ report_id }) => {
          onOpenChange(false);
          toast.success("Report created", { description: "The narrative is being drafted from its results." });
          router.push(`/reports/${report_id}`);
        },
        onError: (error) => toast.error("Could not draft the report", { description: error instanceof ApiError ? error.message : undefined }),
      },
    );

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Draft a report with the AI analyst"
      description={`Period and filters: ${periodLabel}. The model picks sections and writes the summary; tables and charts come from the analytics service, and every figure in the text is checked.`}
      footer={
        plan ? (
          <div className="flex justify-between gap-2">
            <Button variant="ghost" onClick={() => setPlan(null)}>Back</Button>
            <Button variant="primary" onClick={create} disabled={!title.trim() || sections.length === 0 || draft.isPending} data-testid="ai-draft-create">
              {draft.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Wand2 className="size-4" />} Draft report
            </Button>
          </div>
        ) : (
          <div className="flex justify-end">
            <Button variant="primary" onClick={propose} disabled={outline.isPending} data-testid="ai-outline">
              {outline.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
              {outline.isPending ? "Proposing an outline…" : "Propose an outline"}
            </Button>
          </div>
        )
      }
    >
      {!plan ? (
        <label className="block">
          <span className="text-[12.5px] font-medium text-ink-2">What should the report answer? (optional)</span>
          <textarea
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            rows={4}
            maxLength={600}
            placeholder="e.g. How did demand and fares behave, and where did trips start?"
            className="mt-1 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[13px] outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
            data-testid="ai-focus"
          />
        </label>
      ) : (
        <div className="space-y-4">
          <label className="block">
            <span className="text-[12.5px] font-medium text-ink-2">Title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} className="mt-1 h-9 w-full rounded-lg border border-line-strong bg-surface px-3 text-[13px] outline-none focus:border-accent" data-testid="ai-outline-title" />
          </label>
          {plan.rationale ? <p className="rounded-lg bg-accent-soft px-3 py-2 text-[12.5px] text-accent-strong">{plan.rationale}</p> : null}
          <fieldset>
            <legend className="text-[12.5px] font-medium text-ink-2">Sections (proposed by {plan.model}; change freely)</legend>
            <div className="mt-1.5 space-y-1" data-testid="ai-outline-sections">
              {plan.library.map((s) => (
                <label key={s.id} className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-1 hover:bg-surface-2">
                  <input
                    type="checkbox"
                    checked={sections.includes(s.id)}
                    onChange={() => setSections((cur) => (cur.includes(s.id) ? cur.filter((x) => x !== s.id) : [...cur, s.id]))}
                    className="mt-0.5 accent-[var(--accent)]"
                    data-testid={`ai-section-${s.id}`}
                  />
                  <span>
                    <span className="block text-[13px] text-ink">{s.title}</span>
                    <span className="block text-[11.5px] text-ink-muted">{s.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      )}
    </Drawer>
  );
}

/** Where a template-6 report's summary comes from, with redrafting for editors. */
export function NarrativeBanner({ reportId, narrative, canEdit, aiEnabled }: { reportId: string; narrative: NarrativeStatus; canEdit: boolean; aiEnabled: boolean }) {
  const redraft = useRedraftReport(reportId);
  const action =
    canEdit && aiEnabled && narrative.status !== "drafting" ? (
      <Button
        size="sm"
        variant={narrative.status === "ready" ? "ghost" : "secondary"}
        disabled={redraft.isPending}
        onClick={() => redraft.mutate(undefined, { onError: (e) => toast.error("Could not redraft", { description: e instanceof ApiError ? e.message : undefined }) })}
        data-testid="ai-redraft"
      >
        <RefreshCw className="size-3.5" /> {narrative.status === "none" ? "Draft with AI" : "Redraft"}
      </Button>
    ) : null;
  const text = {
    none: "No AI narrative yet; the summary and findings are rule-based.",
    drafting: "The AI analyst is drafting the summary and findings from this report's results…",
    ready: `Summary and findings drafted by ${narrative.model ?? "the AI analyst"} ${formatRelative(narrative.generated_at)}: ${narrative.figures_verified ?? 0} of ${narrative.figures ?? 0} figures checked against the report${narrative.dropped ? `; ${narrative.dropped} sentence(s) without matching figures removed` : ""}.`,
    stale: "The AI narrative was drafted for other filters or sections, so it is not shown. Redraft it for the saved report.",
    failed: `Drafting the narrative failed: ${narrative.error ?? "unknown error"}. The summary and findings are rule-based.`,
  }[narrative.status];
  const tone = narrative.status === "ready" ? "border-good/30 bg-good-soft text-good-ink" : narrative.status === "drafting" || narrative.status === "none" ? "border-accent/30 bg-accent-soft text-accent-strong" : "border-warning/40 bg-warning-soft text-warning-ink";
  const Icon = narrative.status === "ready" ? BadgeCheck : narrative.status === "drafting" ? LoaderCircle : narrative.status === "none" ? Sparkles : TriangleAlert;
  return (
    <div className={`mb-3 flex items-center gap-3 rounded-lg border px-4 py-2.5 text-[12.5px] ${tone}`} data-testid="ai-narrative" data-status={narrative.status} role="status">
      <Icon className={`size-4 shrink-0 ${narrative.status === "drafting" ? "animate-spin" : ""}`} />
      <span className="flex-1">{text}</span>
      {action}
    </div>
  );
}
