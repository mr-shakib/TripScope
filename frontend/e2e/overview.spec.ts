import { expect, type Page, test } from "@playwright/test";

const email = process.env.E2E_EMAIL ?? "";
const password = process.env.E2E_PASSWORD ?? "";

interface Overview {
  kpis: { total_trips: { value: number } };
}

test.skip(!email || !password, "set E2E_EMAIL and E2E_PASSWORD for an existing analyst account");

async function signIn(page: Page) {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/); // protected route redirects when signed out
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

/** The chart rendered an SVG with axes, gridlines and the series (gridline paths have zero height). */
async function expectChartRendered(page: Page) {
  const chart = page.getByTestId("trips-chart");
  await expect(chart.locator("svg")).toBeVisible();
  await expect.poll(() => chart.locator("svg path").count()).toBeGreaterThan(3);
}

async function apiOverview(page: Page, query = ""): Promise<Overview> {
  const response = await page.request.get(`/api/v1/analytics/overview${query}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test("analyst sees KPIs and a chart that match the API, and filters update both", async ({ page }) => {
  await signIn(page);

  const all = await apiOverview(page);
  const tripsTile = page.getByTestId("kpi-total_trips");
  await expect(tripsTile).toHaveAttribute("data-value", String(all.kpis.total_trips.value));
  await expect(tripsTile).toHaveText(all.kpis.total_trips.value.toLocaleString("en-US"));
  await expectChartRendered(page);
  await page.screenshot({ path: "test-results/overview-all.png", fullPage: true });

  await page.getByRole("radio", { name: "First 7 days" }).click();
  await expect(page).toHaveURL(/start=2025-01-01&end=2025-01-07/);
  const week = await apiOverview(page, "?start_date=2025-01-01&end_date=2025-01-07");
  await expect(tripsTile).toHaveAttribute("data-value", String(week.kpis.total_trips.value));
  expect(week.kpis.total_trips.value).toBeLessThan(all.kpis.total_trips.value);
  await expect(page.getByTestId("active-filters")).toContainText("Jan 1, 2025 – Jan 7, 2025");

  await page.getByRole("button", { name: "Show table" }).click();
  const rows = page.getByRole("table").locator("tbody tr");
  await expect(rows).toHaveCount(7);
  const series = await (await page.request.get("/api/v1/analytics/trips-over-time?start_date=2025-01-01&end_date=2025-01-07")).json();
  const tableSum = (await rows.locator("td:nth-child(2)").allTextContents())
    .map((t) => Number(t.replace(/,/g, "")))
    .reduce((a, b) => a + b, 0);
  expect(tableSum).toBe(series.points.reduce((a: number, p: { trips: number }) => a + p.trips, 0));
  expect(tableSum).toBe(week.kpis.total_trips.value);

  await page.getByRole("button", { name: "Show chart" }).click();
  await page.getByRole("radio", { name: "Hourly" }).click();
  await expectChartRendered(page);
  await page.screenshot({ path: "test-results/overview-week-hourly.png", fullPage: true });

  await page.getByRole("button", { name: "Reset filters" }).click();
  await expect(tripsTile).toHaveAttribute("data-value", String(all.kpis.total_trips.value));

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("dark theme renders with its own tokens", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await signIn(page);
  await expectChartRendered(page);
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(background).toBe("rgb(13, 13, 13)");
  await page.screenshot({ path: "test-results/overview-dark.png", fullPage: true });
});
