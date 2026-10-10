import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingBlock } from "@/components/ui/states";

import { ReportsView } from "./reports-view";

export const metadata: Metadata = { title: "Reports" };

export default function ReportsPage() {
  return (
    <Suspense fallback={<LoadingBlock className="h-[60vh]" />}>
      <ReportsView />
    </Suspense>
  );
}
