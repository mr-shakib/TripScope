"use client";

import {
  Activity,
  ChartColumn,
  ChevronDown,
  Compass,
  Database,
  FileText,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Settings,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { DropdownMenu } from "radix-ui";
import { type ReactNode, useEffect } from "react";

import { ApiError } from "@/api/client";
import { useDataset, useLogout, useMe } from "@/api/hooks";
import type { Role } from "@/api/types";
import { LoadingBlock } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import { readFilters, writeFilters } from "@/lib/filters";
import { formatDate, formatInteger } from "@/lib/format";

interface NavItem {
  href: string;
  label: string;
  icon: typeof Activity;
  phase?: number;
  roles?: Role[];
  /** Analytics pages share the global filters: links carry them along. */
  keepsFilters?: boolean;
}

const NAV: { title: string; items: NavItem[] }[] = [
  {
    title: "Analyze",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard, keepsFilters: true },
      { href: "/dashboards", label: "Dashboards", icon: ChartColumn, keepsFilters: true },
      { href: "/explore", label: "Explore data", icon: Compass, keepsFilters: true },
      { href: "/ai", label: "AI analyst", icon: Sparkles, phase: 5 },
      { href: "/reports", label: "Reports", icon: FileText, phase: 4 },
    ],
  },
  {
    title: "Data operations",
    items: [
      { href: "/sources", label: "Data sources", icon: Database, roles: ["admin", "analyst"] },
      { href: "/jobs", label: "Processing jobs", icon: Activity, roles: ["admin", "analyst"] },
      { href: "/quality", label: "Data quality", icon: ShieldCheck },
    ],
  },
  { title: "Workspace", items: [{ href: "/admin", label: "Settings", icon: Settings, phase: 6, roles: ["admin"] }] },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="relative flex size-8 items-center justify-center rounded-[10px] bg-gradient-to-br from-[#3b8ae6] to-[#1c5cab] shadow-[0_2px_6px_rgba(28,92,171,0.4)]">
        <svg viewBox="0 0 24 24" className="size-[18px] text-white" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M4 17c3-6 6-1 9-7s4-4 7-4" />
          <circle cx="4" cy="17" r="1.6" fill="currentColor" stroke="none" />
          <circle cx="20" cy="6" r="1.6" fill="currentColor" stroke="none" />
        </svg>
      </div>
      <div className="leading-tight">
        <p className="text-[15px] font-semibold tracking-tight text-ink">TripScope</p>
        <p className="text-[11px] text-ink-muted">NYC trip intelligence</p>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const me = useMe();
  const dataset = useDataset();
  const logout = useLogout();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  // Only the filter keys travel between pages (not e.g. ?tab= or ?job=).
  const filterQuery = writeFilters(new URLSearchParams(), readFilters(new URLSearchParams(searchParams.toString()))).toString();

  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;
  useEffect(() => {
    if (unauthenticated) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [unauthenticated, pathname, router]);

  if (me.isPending || unauthenticated) return <LoadingBlock label="Checking your session…" className="h-screen" />;
  const role = me.data?.role;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-line bg-surface lg:flex">
        <div className="px-5 pb-6 pt-5">
          <Logo />
        </div>
        <nav aria-label="Main" className="scroll-thin flex-1 space-y-6 overflow-y-auto px-3">
          {NAV.map((group) => (
            <div key={group.title}>
              <p className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">{group.title}</p>
              <ul className="space-y-0.5">
                {group.items
                  .filter((item) => !item.roles || (role && item.roles.includes(role)))
                  .map((item) => {
                    const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                    const Icon = item.icon;
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.keepsFilters && filterQuery ? `${item.href}?${filterQuery}` : item.href}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "group relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] transition-colors",
                            active ? "bg-accent-soft font-medium text-accent-strong" : "text-ink-2 hover:bg-surface-3 hover:text-ink",
                          )}
                        >
                          {active ? <span className="absolute -left-3 top-1.5 h-[calc(100%-12px)] w-[3px] rounded-r-full bg-accent" /> : null}
                          <Icon className={cn("size-4 shrink-0", active ? "text-accent" : "text-ink-muted group-hover:text-ink-2")} />
                          <span className="flex-1">{item.label}</span>
                          {item.phase ? (
                            <span className="rounded-md bg-surface-3 px-1.5 py-0.5 text-[10px] font-medium text-ink-muted">P{item.phase}</span>
                          ) : null}
                        </Link>
                      </li>
                    );
                  })}
              </ul>
            </div>
          ))}
        </nav>
        {dataset.data?.start_date ? (
          <div className="m-3 rounded-xl border border-line bg-surface-2 p-3">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
              <ListChecks className="size-3.5" /> Published data
            </div>
            <p className="mt-1.5 text-sm font-semibold text-ink tabular">{formatInteger(dataset.data.total_rows)} trips</p>
            <p className="text-xs text-ink-muted">
              {formatDate(dataset.data.start_date)} – {formatDate(dataset.data.end_date)}
            </p>
            <p className="mt-0.5 text-xs text-ink-muted">{dataset.data.periods.length} monthly files · Yellow taxi</p>
          </div>
        ) : null}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-4 border-b border-line bg-surface/85 px-4 backdrop-blur-md md:px-8">
          <div className="flex items-center gap-3 lg:hidden">
            <Logo />
          </div>
          <div className="hidden items-center gap-2 text-xs text-ink-muted lg:flex">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1">
              <span className="size-1.5 rounded-full bg-good" /> NYC TLC Yellow Taxi · source: nyc.gov
            </span>
          </div>
          {me.data ? (
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button
                  type="button"
                  className="flex items-center gap-2.5 rounded-full border border-line bg-surface py-1 pl-1 pr-2.5 text-sm shadow-card transition-colors hover:bg-surface-2"
                >
                  <span className="flex size-7 items-center justify-center rounded-full bg-gradient-to-br from-accent-soft to-[#d6e6fb] text-xs font-semibold text-accent-strong">
                    {me.data.display_name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="hidden text-left leading-tight sm:block">
                    <span className="block text-[13px] font-medium text-ink">{me.data.display_name}</span>
                    <span className="block text-[11px] capitalize text-ink-muted">{me.data.role}</span>
                  </span>
                  <ChevronDown className="size-3.5 text-ink-muted" />
                </button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content
                  align="end"
                  sideOffset={8}
                  className="z-50 min-w-56 rounded-xl border border-line bg-surface p-1.5 shadow-pop animate-fade-in"
                >
                  <div className="px-2.5 py-2">
                    <p className="text-sm font-medium text-ink">{me.data.display_name}</p>
                    <p className="text-xs text-ink-muted">{me.data.email}</p>
                  </div>
                  <DropdownMenu.Separator className="my-1 h-px bg-line" />
                  <DropdownMenu.Item
                    onSelect={() => logout.mutate(undefined, { onSettled: () => router.replace("/login") })}
                    className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-ink-2 outline-none data-[highlighted]:bg-surface-3 data-[highlighted]:text-ink"
                  >
                    <LogOut className="size-4" /> Sign out
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          ) : null}
        </header>
        <main className="flex-1 px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[26px] font-semibold tracking-tight text-ink">{title}</h1>
        {description ? <p className="mt-1 max-w-2xl text-sm text-ink-2">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
