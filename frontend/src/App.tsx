import { Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { RequireAuth } from "./components/RequireAuth";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PlannedPage } from "./pages/PlannedPage";

const PLANNED = [
  { path: "explore", title: "Explore Data", phase: 3, summary: "Schema browser, bounded row preview and filtered extracts." },
  { path: "dashboards", title: "Dashboards", phase: 3, summary: "All required charts with drill-down filters." },
  { path: "ai", title: "AI Analyst", phase: 5, summary: "Questions answered only from approved analytics tools, with evidence." },
  { path: "reports", title: "Reports", phase: 4, summary: "Report templates with CSV, XLSX and PDF exports." },
  { path: "sources", title: "Data Sources", phase: 2, summary: "Register sources and start ingestion jobs." },
  { path: "jobs", title: "Processing Jobs", phase: 2, summary: "Job status, stage timings and logs." },
  { path: "quality", title: "Data Quality", phase: 3, summary: "Quarantine reasons, flags and missingness per run." },
  { path: "admin", title: "Settings / Admin", phase: 6, summary: "Users, roles and audit events." },
];

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route index element={<OverviewPage />} />
          {PLANNED.map((p) => (
            <Route key={p.path} path={p.path} element={<PlannedPage title={p.title} phase={p.phase} summary={p.summary} />} />
          ))}
          <Route path="*" element={<PlannedPage title="Not found" phase={1} summary="This page does not exist." />} />
        </Route>
      </Route>
    </Routes>
  );
}
