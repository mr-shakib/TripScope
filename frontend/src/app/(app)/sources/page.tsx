import type { Metadata } from "next";

import { SourcesView } from "./sources-view";

export const metadata: Metadata = { title: "Data sources" };

export default function SourcesPage() {
  return <SourcesView />;
}
