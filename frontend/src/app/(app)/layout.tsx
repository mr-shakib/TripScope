import { type ReactNode, Suspense } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { LoadingBlock } from "@/components/ui/states";

export default function AuthenticatedLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<LoadingBlock className="h-screen" />}>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
