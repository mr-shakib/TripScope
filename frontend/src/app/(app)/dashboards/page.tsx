import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingBlock } from "@/components/ui/states";

import { DashboardsView } from "./dashboards-view";

export const metadata: Metadata = { title: "Dashboards" };

export default function DashboardsPage() {
  return (
    <Suspense fallback={<LoadingBlock className="h-[60vh]" />}>
      <DashboardsView />
    </Suspense>
  );
}
