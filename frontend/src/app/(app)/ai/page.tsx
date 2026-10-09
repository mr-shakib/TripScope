import type { Metadata } from "next";

import { Planned } from "@/components/layout/planned";

export const metadata: Metadata = { title: "AI analyst" };

export default function Page() {
  return <Planned title="AI analyst" phase={5} icon="Sparkles" summary="Ask questions in plain language. Answers come only from approved analytics tools, with the filters, definitions and values they used. Runs on a local model or DeepSeek." />;
}
