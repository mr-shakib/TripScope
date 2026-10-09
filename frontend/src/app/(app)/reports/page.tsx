import type { Metadata } from "next";

import { Planned } from "@/components/layout/planned";

export const metadata: Metadata = { title: "Reports" };

export default function Page() {
  return <Planned title="Reports" phase={4} icon="FileText" summary="Executive, demand, fare and zone reports with CSV, Excel and PDF export, built from the same filters and metric definitions as the dashboard." />;
}
