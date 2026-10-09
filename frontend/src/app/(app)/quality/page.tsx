import type { Metadata } from "next";

import { QualityView } from "./quality-view";

export const metadata: Metadata = { title: "Data quality" };

export default function QualityPage() {
  return <QualityView />;
}
