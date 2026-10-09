import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

function RouteArt() {
  // Decorative street-grid and route lines; purely visual, no data.
  return (
    <svg viewBox="0 0 600 600" className="absolute inset-0 h-full w-full" aria-hidden preserveAspectRatio="xMidYMid slice">
      <defs>
        <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse" patternTransform="rotate(29)">
          <path d="M40 0H0V40" fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="1" />
        </pattern>
        <linearGradient id="route" x1="0" x2="1">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#ffffff" stopOpacity="0.9" />
          <stop offset="1" stopColor="#9cc6f5" stopOpacity="0.4" />
        </linearGradient>
      </defs>
      <rect width="600" height="600" fill="url(#grid)" />
      <path d="M-20 470 C 120 420, 160 300, 290 310 S 470 200, 620 120" fill="none" stroke="url(#route)" strokeWidth="2.5" />
      <path d="M-20 300 C 90 330, 200 240, 300 250 S 450 360, 620 330" fill="none" stroke="rgba(255,255,255,0.25)" strokeWidth="1.5" strokeDasharray="2 7" strokeLinecap="round" />
      {[[290, 310], [470, 210], [150, 395]].map(([cx, cy]) => (
        <g key={`${cx}-${cy}`}>
          <circle cx={cx} cy={cy} r="14" fill="rgba(255,255,255,0.12)" />
          <circle cx={cx} cy={cy} r="5" fill="#ffffff" />
        </g>
      ))}
    </svg>
  );
}

export default function LoginPage() {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <section className="relative hidden overflow-hidden bg-gradient-to-br from-[#1c5cab] via-[#2a78d6] to-[#3f8fe8] p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <RouteArt />
        <div className="relative flex items-center gap-2.5">
          <div className="flex size-9 items-center justify-center rounded-xl bg-white/15 ring-1 ring-white/25 backdrop-blur">
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" aria-hidden>
              <path d="M4 17c3-6 6-1 9-7s4-4 7-4" />
            </svg>
          </div>
          <span className="text-lg font-semibold tracking-tight">TripScope</span>
        </div>
        <div className="relative max-w-md">
          <h1 className="text-[34px] font-semibold leading-[1.15] tracking-tight">
            Every NYC taxi trip, validated before it reaches a chart.
          </h1>
          <ul className="mt-8 space-y-4 text-[15px] text-white/85">
            {[
              ["Verified pipeline", "Checksummed sources, Spark validation, exact reconciliation into ClickHouse."],
              ["Nothing silently dropped", "Suspect records are quarantined or flagged, with reasons you can inspect."],
              ["Answers you can cite", "Every number carries its filters, definitions and source run."],
            ].map(([title, text]) => (
              <li key={title} className="flex gap-3">
                <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-white" />
                <span>
                  <span className="font-medium text-white">{title}.</span> {text}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-white/60">Data: NYC Taxi &amp; Limousine Commission Trip Record Data</p>
      </section>
      <section className="flex items-center justify-center bg-page px-6 py-12">
        <Suspense>
          <LoginForm />
        </Suspense>
      </section>
    </div>
  );
}
