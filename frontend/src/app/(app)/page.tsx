import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingBlock } from "@/components/ui/states";

import { OverviewView } from "./overview-view";

export const metadata: Metadata = { title: "Overview" };

export default function OverviewPage() {
  return (
    <Suspense fallback={<LoadingBlock className="h-[60vh]" />}>
      <OverviewView />
    </Suspense>
  );
}
