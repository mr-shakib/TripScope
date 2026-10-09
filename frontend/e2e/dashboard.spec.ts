import { expect, type Locator, type Page, test } from "@playwright/test";

/*
 * End-to-end against the real stack (API + published data + a running worker for the jobs test).
 * Accounts come from the environment: E2E_ADMIN_EMAIL/PASSWORD (required) and E2E_VIEWER_EMAIL/PASSWORD (optional).
 */
const admin = { email: process.env.E2E_ADMIN_EMAIL ?? "", password: process.env.E2E_ADMIN_PASSWORD ?? "" };
const viewer = { email: process.env.E2E_VIEWER_EMAIL ?? "", password: process.env.E2E_VIEWER_PASSWORD ?? "" };

test.skip(!admin.email || !admin.password, "set E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD");

interface Overview {
  kpis: Record<string, { value: number | null }>;
  meta: { source_table: string };
}

async function signIn(page: Page, account: { email: string; password: string }, next = "/") {
  await page.goto(next);
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password").fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function apiOverview(page: Page, query = ""): Promise<Overview> {
  const response = await page.request.get(`/api/v1/analytics/overview${query}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as Overview;
}

/** The hero KPI shows the exact API value for the filters currently in the URL. */
async function expectTripsMatchApi(page: Page) {
  const params = new URL(page.url()).searchParams;
  const query = new URLSearchParams();
  const map: Record<string, string> = { start: "start_date", end: "end_date", pz: "pickup_zone", pt: "payment_type", h: "hour", wd: "weekday" };
  for (const [short, long] of Object.entries(map)) {
    const value = params.get(short);
    if (!value) continue;
    if (short === "start" || short === "end") query.append(long, value);
    else value.split(",").forEach((v) => query.append(long, v));
  }
  const expected = await apiOverview(page, `?${query.toString()}`);
  await expect(page.getByTestId("kpi-total_trips")).toHaveAttribute("data-value", String(expected.kpis.total_trips?.value));
  return expected;
}

/** ECharts SVG bars in series colour, ordered left to right. */
async function bars(chart: Locator): Promise<Locator[]> {
  const paths = chart.locator("svg path[fill='#2a78d6'], svg path[fill='rgb(42,120,214)']");
  await expect(paths.first()).toBeVisible();
  const items = await paths.all();
  const withX = await Promise.all(items.map(async (p) => ({ p, x: (await p.boundingBox())?.x ?? 0, h: (await p.boundingBox())?.height ?? 0 })));
  return withX.filter((b) => b.h > 2).sort((a, b) => a.x - b.x).map((b) => b.p);
}

test("signed-out visitors are sent to sign in and back to where they were going", async ({ page }) => {
  await page.goto("/quality");
  await expect(page).toHaveURL(/\/login\?next=%2Fquality/);
  await page.getByLabel("Email").fill(admin.email);
  await page.getByLabel("Password").fill("definitely-not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("email or password is incorrect")).toBeVisible();
  await page.getByLabel("Password").fill(admin.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/quality$/);
});

test("overview KPIs, charts and filters agree with the API", async ({ page }) => {
  await signIn(page, admin);
  const all = await expectTripsMatchApi(page);
  expect(all.meta.source_table).toBe("trips_hourly_agg");
  await expect(page.getByTestId("trend-chart").locator("svg")).toBeVisible();

  // Click Saturday in the weekday chart → filter applied, every KPI follows.
  const weekdayBars = await bars(page.getByTestId("weekday-chart"));
  expect(weekdayBars).toHaveLength(7);
  await weekdayBars[5]!.click();
  await expect(page).toHaveURL(/wd=6/);
  const saturday = await expectTripsMatchApi(page);
  expect(saturday.kpis.total_trips!.value!).toBeLessThan(all.kpis.total_trips!.value!);
  await expect(page.getByTestId("active-filters")).toContainText("Sat");

  // Pick JFK from the searchable zone picker.
  await page.getByTestId("filter-zones").click();
  await page.getByLabel("Search pickup zones").fill("JFK");
  await page.getByRole("option", { name: /JFK Airport/ }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/pz=132/);
  await expectTripsMatchApi(page);
  await expect(page.getByTestId("active-filters")).toContainText("JFK Airport");

  // Add morning-peak hours from the time picker.
  await page.getByTestId("filter-time").click();
  await page.getByTestId("hour-8").click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/h=8/);
  const narrow = await expectTripsMatchApi(page);
  expect(narrow.kpis.total_trips!.value!).toBeGreaterThan(0);

  // Remove a chip, then reset everything.
  await page.getByRole("button", { name: "Remove Sat" }).click();
  await expect(page).not.toHaveURL(/wd=/);
  await expectTripsMatchApi(page);
  await page.getByRole("button", { name: "Reset" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("kpi-total_trips")).toHaveAttribute("data-value", String(all.kpis.total_trips!.value));
});

test("month preset and trend drill-down narrow the date range", async ({ page }) => {
  await signIn(page, admin);
  await page.getByTestId("filter-dates").click();
  await page.getByRole("button", { name: "Mar", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/start=2025-03-01&end=2025-03-31/);
  const march = await expectTripsMatchApi(page);
  const series = await (await page.request.get("/api/v1/analytics/trips-over-time?start_date=2025-03-01&end_date=2025-03-31&granularity=month")).json();
  expect(series.points[0].trips).toBe(march.kpis.total_trips!.value);

  // Table view lists the 31 days; their sum equals the KPI.
  await page.getByRole("radio", { name: "Table" }).click();
  const cells = page.locator("table tbody tr td:nth-child(3)");
  await expect(cells).toHaveCount(31);
  const sum = (await cells.allTextContents()).map((t) => Number(t.replace(/,/g, ""))).reduce((a, b) => a + b, 0);
  expect(sum).toBe(march.kpis.total_trips!.value);
});

test("admin queues a run and cancels it from the job drawer", async ({ page }) => {
  await signIn(page, admin, "/sources");
  await expect(page.getByRole("heading", { name: "Data sources" })).toBeVisible();
  const row = page.getByTestId("source-yellow-2025-06");
  await expect(row).toContainText("4,322,709");
  await row.getByRole("button", { name: /Re-run/ }).click();
  await page.getByRole("button", { name: "Queue re-run" }).click();
  await expect(page.getByText("Queued yellow-2025-06")).toBeVisible();
  await page.getByRole("button", { name: "View job" }).click();

  await expect(page).toHaveURL(/\/jobs\?job=/);
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("2025-06 · yellow-2025-06");
  await drawer.getByRole("button", { name: "Cancel job" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel job" }).click();
  await expect(drawer.locator("[data-status]").first()).toHaveAttribute("data-status", "cancelled", { timeout: 45_000 });
  await expect(drawer.getByRole("alert")).toContainText(/cancelled/);
  await expect(drawer.getByRole("button", { name: "Retry" })).toBeVisible();

  // Published data for June is unchanged.
  const june = await apiOverview(page, "?start_date=2025-06-01&end_date=2025-06-30");
  expect(june.kpis.total_trips!.value).toBe(4322709);
});

test("quality page shows reasons, flags and the schema matrix", async ({ page }) => {
  await signIn(page, admin, "/quality");
  const quality = await (await page.request.get("/api/v1/datasets/nyc-tlc-yellow/quality")).json();
  await expect(page.getByTestId("quarantine-reasons")).toContainText("Drop-off before pickup");
  await expect(page.getByTestId("quarantine-reasons")).toContainText(quality.totals.quarantine_reasons.dropoff_before_pickup.toLocaleString("en-US"));
  await expect(page.getByTestId("quality-flags")).toContainText("No passenger count");
  await expect(page.getByTestId("quality-periods").locator("tbody tr")).toHaveCount(quality.periods.length);
  await expect(page.getByTestId("schema-matrix")).toContainText("cbd_congestion_fee");
  await expect(page.getByText("No drift across files")).toBeVisible();
});

test("viewers see analytics but not data operations", async ({ page }) => {
  test.skip(!viewer.email, "set E2E_VIEWER_EMAIL and E2E_VIEWER_PASSWORD");
  await signIn(page, viewer);
  await expect(page.getByTestId("kpi-total_trips")).not.toHaveAttribute("data-value", "");
  await expect(page.getByRole("link", { name: "Data sources" })).toHaveCount(0);
  await page.goto("/sources");
  await expect(page.getByText("Your role can’t view this")).toBeVisible();
});
