import type { Metadata } from "next";

import { Planned } from "@/components/layout/planned";

export const metadata: Metadata = { title: "Explore data" };

export default function Page() {
  return <Planned title="Explore data" phase={3} icon="Compass" summary="Browse the schema, preview a bounded number of rows, sort and filter, and download a permitted extract." />;
}
