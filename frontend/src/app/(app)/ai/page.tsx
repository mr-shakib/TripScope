import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingBlock } from "@/components/ui/states";

import { AIView } from "./ai-view";

export const metadata: Metadata = { title: "AI analyst" };

export default function AIPage() {
  return (
    <Suspense fallback={<LoadingBlock className="h-[60vh]" />}>
      <AIView />
    </Suspense>
  );
}
