import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useLogout, useMe } from "../api/hooks";

interface NavItem {
  to: string;
  label: string;
  phase?: number; // planned items are visible but clearly not built yet — no placeholder data
}

const NAV: NavItem[] = [
  { to: "/", label: "Overview" },
  { to: "/explore", label: "Explore Data", phase: 3 },
  { to: "/dashboards", label: "Dashboards", phase: 3 },
  { to: "/ai", label: "AI Analyst", phase: 5 },
  { to: "/reports", label: "Reports", phase: 4 },
  { to: "/sources", label: "Data Sources", phase: 2 },
  { to: "/jobs", label: "Processing Jobs", phase: 2 },
  { to: "/quality", label: "Data Quality", phase: 3 },
  { to: "/admin", label: "Settings / Admin", phase: 6 },
];

export function Layout() {
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();

  return (
    <div className="flex min-h-screen bg-page">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-surface-1 md:flex">
        <div className="px-5 py-5">
          <p className="text-lg font-semibold tracking-tight text-ink">TripScope</p>
          <p className="text-xs text-ink-muted">NYC TLC trip analytics</p>
        </div>
        <nav aria-label="Main" className="flex flex-col gap-0.5 px-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center justify-between rounded-md px-3 py-2 text-sm ${
                  isActive ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:bg-surface-2"
                }`
              }
            >
              <span>{item.label}</span>
              {item.phase ? <span className="text-[10px] uppercase text-ink-muted">Phase {item.phase}</span> : null}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-line bg-surface-1 px-4 py-3 md:px-6">
          <div>
            <p className="text-xs text-ink-muted">Dataset</p>
            <p className="text-sm font-medium text-ink">NYC TLC Yellow Taxi Trip Records</p>
          </div>
          {me.data ? (
            <div className="flex items-center gap-3 text-sm">
              <span className="hidden text-ink-2 sm:inline">
                {me.data.display_name} · <span className="text-ink-muted">{me.data.role}</span>
              </span>
              <button
                type="button"
                onClick={() => logout.mutate(undefined, { onSettled: () => navigate("/login", { replace: true }) })}
                className="rounded-md border border-line px-3 py-1.5 text-ink hover:bg-surface-2"
              >
                Sign out
              </button>
            </div>
          ) : null}
        </header>
        <main className="flex-1 px-4 py-6 md:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
