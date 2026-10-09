import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingBlock } from "@/components/ui/states";

import { JobsView } from "./jobs-view";

export const metadata: Metadata = { title: "Processing jobs" };

export default function JobsPage() {
  return (
    <Suspense fallback={<LoadingBlock className="h-[60vh]" />}>
      <JobsView />
    </Suspense>
  );
}
