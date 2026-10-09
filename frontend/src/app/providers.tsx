"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Toaster } from "sonner";

import { TooltipProvider } from "@/components/ui/overlays";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 30_000 } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={150}>
        {children}
        <Toaster position="bottom-right" toastOptions={{ className: "!rounded-xl !border-line !shadow-pop !text-sm" }} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
