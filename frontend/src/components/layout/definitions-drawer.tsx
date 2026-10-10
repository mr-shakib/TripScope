"use client";

import { BookOpen } from "lucide-react";
import { useState } from "react";

import { useMetricCatalogue } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/overlays";
import { LoadingBlock } from "@/components/ui/states";

/** Every metric's definition, inclusion rule and caveats in one place (shared by all analytics pages). */
export function DefinitionsButton() {
  const [open, setOpen] = useState(false);
  const catalogue = useMetricCatalogue();
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)} data-testid="open-definitions">
        <BookOpen className="size-3.5" /> Metric definitions
      </Button>
      <Drawer open={open} onOpenChange={setOpen} title="Metric definitions" description="How every number on these pages is calculated.">
        {catalogue.data ? (
          <div className="space-y-4">
            {catalogue.data.map((m) => (
              <section key={m.id} className="rounded-xl border border-line p-4">
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="text-sm font-semibold text-ink">{m.label}</h3>
                  <span className="font-mono text-[11px] text-ink-muted">{m.id} · {m.unit}</span>
                </div>
                <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">{m.description}</p>
                <p className="mt-2 text-xs text-ink-muted">
                  <span className="font-medium text-ink-2">Rows included: </span>
                  {m.rows_included}
                </p>
                {m.caveats.length ? (
                  <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-ink-2">
                    {m.caveats.map((c) => (
                      <li key={c}>{c}</li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ))}
            <p className="text-xs leading-relaxed text-ink-muted">
              Quarantined records never reach these metrics; flagged values are excluded only from the metrics they would distort, and every
              result states how many rows its flag excluded. Times are NYC local wall-clock as recorded by TLC.
            </p>
          </div>
        ) : (
          <LoadingBlock />
        )}
      </Drawer>
    </>
  );
}
