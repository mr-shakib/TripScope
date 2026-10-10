import { expect, type Page, test } from "@playwright/test";

export const admin = { email: process.env.E2E_ADMIN_EMAIL ?? "", password: process.env.E2E_ADMIN_PASSWORD ?? "" };
export const viewer = { email: process.env.E2E_VIEWER_EMAIL ?? "", password: process.env.E2E_VIEWER_PASSWORD ?? "" };
export const analyst = { email: process.env.E2E_ANALYST_EMAIL ?? "", password: process.env.E2E_ANALYST_PASSWORD ?? "" };

export interface Overview {
  kpis: Record<string, { value: number | null }>;
  comparison?: { available: boolean; days?: number; kpis?: Record<string, { value: number | null }> };
  meta: { source_table: string };
}

/** Fail any test in which the page throws (charts included). */
export function guardPageErrors() {
  const errors: string[] = [];
  test.beforeEach(async ({ page }) => {
    errors.length = 0;
    page.on("pageerror", (e) => errors.push(e.message));
  });
  test.afterEach(() => {
    expect(errors, "uncaught page errors").toEqual([]);
  });
}

export async function signIn(page: Page, account: { email: string; password: string }, next = "/") {
  await page.goto(next);
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password").fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

const URL_TO_API: Record<string, string> = {
  start: "start_date",
  end: "end_date",
  pz: "pickup_zone",
  dz: "dropoff_zone",
  pt: "payment_type",
  v: "vendor_id",
  h: "hour",
  wd: "weekday",
  dmin: "min_distance",
  dmax: "max_distance",
};

/** The API query equivalent to the filters currently in the page URL. */
export function apiQueryFromUrl(page: Page, extra: Record<string, string> = {}): string {
  const params = new URL(page.url()).searchParams;
  const query = new URLSearchParams();
  for (const [short, long] of Object.entries(URL_TO_API)) {
    const value = params.get(short);
    if (!value) continue;
    if (["start", "end", "dmin", "dmax"].includes(short)) query.append(long, value);
    else value.split(",").forEach((v) => query.append(long, v));
  }
  for (const [k, v] of Object.entries(extra)) query.append(k, v);
  return query.toString();
}

export async function apiOverview(page: Page, query = ""): Promise<Overview> {
  const response = await page.request.get(`/api/v1/analytics/overview${query ? `?${query}` : ""}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as Overview;
}

/** The hero KPI shows the exact API value for the filters in the URL. */
export async function expectTripsMatchApi(page: Page): Promise<Overview> {
  const expected = await apiOverview(page, apiQueryFromUrl(page));
  await expect(page.getByTestId("kpi-total_trips")).toHaveAttribute("data-value", String(expected.kpis.total_trips?.value));
  return expected;
}

/** Sum of the integers shown in a bar list (thousands separators removed). */
export async function barListTotal(page: Page, testId: string): Promise<number> {
  const values = await page.getByTestId(testId).locator("li span.tabular.font-semibold").allTextContents();
  return values.map((t) => Number(t.replace(/[^0-9]/g, ""))).reduce((a, b) => a + b, 0);
}
