import type { Metadata } from "next";

import { Planned } from "@/components/layout/planned";

export const metadata: Metadata = { title: "Settings" };

export default function Page() {
  return <Planned title="Settings" phase={6} icon="Settings" summary="Users, roles, dataset permissions and the audit log." />;
}
