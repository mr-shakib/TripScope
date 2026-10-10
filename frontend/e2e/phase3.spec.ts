import { expect, type Locator, type Page, test } from "@playwright/test";

import { admin, apiOverview, apiQueryFromUrl, barListTotal, expectTripsMatchApi, guardPageErrors, signIn, viewer } from "./helpers";

test.skip(!admin.email || !admin.password, "set E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD");
guardPageErrors();

/** Every chart on a dashboard tab agrees with the API for the filters in the URL (payment bar list = KPI). */
async function expectPaymentTotalMatchesApi(page: Page) {
  const expected = await apiOverview(page, apiQueryFromUrl(page));
  await expect.poll(() => barListTotal(page, "payment-types-card")).toBe(expected.kpis.total_trips!.value);
}

const NOT_DATA = /^(none|transparent|url\(|#000|#000000|rgb\(0, ?0, ?0\)|#fff|#ffffff|rgb\(255, ?255, ?255\)|#e5e8ee|rgb\(229, ?232, ?238\))/i;

/** Click a shape at its centre with the mouse (chart tooltips would otherwise block actionability checks). */
async function clickShape(page: Page, shape: Locator) {
  await shape.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300); // let the sticky header and chart settle after scrolling
  const box = await shape.boundingBox();
  if (!box) throw new Error("shape has no box");
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

/** Largest SVG path inside a chart that carries data colour (a reliably clickable zone, cell or bar). */
async function largestPath(chart: Locator): Promise<Locator> {
  const dataPaths = async () => {
    const sized = await Promise.all(
      (await chart.locator("svg path").all()).map(async (p) => {
        const box = await p.boundingBox();
        const fill = (await p.getAttribute("fill")) ?? "";
        return { p, area: box ? box.width * box.height : 0, fill };
      }),
    );
    return sized.filter((s) => s.area > 0 && s.fill !== "" && !NOT_DATA.test(s.fill)).sort((a, b) => b.area - a.area);
  };
  // The first <path> can be a zero-size axis or clip path, so wait for data shapes rather than any path.
  await expect.poll(async () => (await dataPaths()).length).toBeGreaterThan(0);
  return (await dataPaths())[0]!.p;
}

test("overview compares a month with the previous period", async ({ page }) => {
  await signIn(page, admin, "/?start=2025-03-01&end=2025-03-31");
  const expected = await apiOverview(page, "start_date=2025-03-01&end_date=2025-03-31&compare=previous");
  await expectTripsMatchApi(page);
  const current = expected.kpis.total_trips!.value!;
  const previous = expected.comparison!.kpis!.total_trips!.value!;
  const change = ((current - previous) / previous) * 100;
  await expect(page.getByTestId("delta-total_trips")).toContainText(`${change > 0 ? "+" : ""}${change.toFixed(Math.abs(change) < 10 ? 1 : 0)}%`);
  await expect(page.getByTestId("delta-total_trips")).toContainText("vs previous 31 days");
  await expect(page.getByText("Previous 31 days: Jan 29, 2025 – Feb 28, 2025")).toBeVisible();

  // Unticking the comparison removes the deltas.
  await page.getByTestId("compare-toggle").uncheck();
  await expect(page.getByTestId("delta-total_trips")).toHaveCount(0);
});

test("clicking a zone on the map filters the whole overview", async ({ page }) => {
  await signIn(page, admin);
  await clickShape(page, await largestPath(page.getByTestId("zone-map")));
  await expect(page).toHaveURL(/pz=\d+/);
  await expect(page.getByTestId("active-filters")).toContainText("From");
  const filtered = await expectTripsMatchApi(page);
  expect(filtered.kpis.total_trips!.value!).toBeLessThan(24_082_454);
});

test("dashboard drill-downs keep every chart consistent", async ({ page }) => {
  await signIn(page, admin, "/dashboards");
  await expectPaymentTotalMatchesApi(page);

  // Heatmap cell → weekday + hour.
  await clickShape(page, await largestPath(page.getByTestId("hour-weekday-heatmap")));
  await expect(page).toHaveURL(/[?&]wd=\d/);
  await expect(page).toHaveURL(/[?&]h=\d+/);
  await expectPaymentTotalMatchesApi(page);

  // Payment type bar → payment filter (combined with the cell).
  await page.getByTestId("payment-types-card").getByRole("button", { name: /Credit card/ }).click();
  await expect(page).toHaveURL(/pt=1/);
  await expectPaymentTotalMatchesApi(page);

  // Distance histogram bar → distance range; the query moves to the fact table.
  await page.getByRole("tab", { name: "Trips & fares" }).click();
  await expect(page).toHaveURL(/tab=fares/);
  await clickShape(page, await largestPath(page.getByTestId("trip_distance-histogram")));
  await expect(page).toHaveURL(/dmin=\d+&dmax=\d+/);
  await expect(page.getByTestId("active-filters")).toContainText("mi");

  // Busiest pair → pickup and drop-off zones.
  await page.getByRole("tab", { name: "Zones & flows" }).click();
  await page.getByRole("button", { name: "Reset" }).click();
  await expect(page).toHaveURL(/\/dashboards\?tab=zones$/);
  const flows = await (await page.request.get(`/api/v1/analytics/top-flows?limit=1`)).json();
  const top = flows.flows[0];
  // Wait for the unfiltered list (the previous one stays on screen, dimmed, while it refetches).
  const firstPair = page.getByTestId("top-flows").getByRole("button").first();
  await expect(firstPair).toContainText(top.trips.toLocaleString("en-US"));
  await firstPair.click();
  await expect(page).toHaveURL(new RegExp(`pz=${top.pickup_zone}&dz=${top.dropoff_zone}`));
  const pair = await apiOverview(page, apiQueryFromUrl(page));
  expect(pair.kpis.total_trips!.value).toBe(top.trips);
});

test("definitions drawer explains every metric", async ({ page }) => {
  await signIn(page, admin, "/dashboards");
  await page.getByTestId("open-definitions").click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("Trips per day");
  await expect(drawer).toContainText("Total recorded amount");
  await expect(drawer).toContainText("cash tips");
});

test("explorer previews, sorts, filters, pages and downloads", async ({ page }) => {
  test.skip(!viewer.email, "set E2E_VIEWER_EMAIL and E2E_VIEWER_PASSWORD");
  await signIn(page, viewer, "/explore?start=2025-01-06&end=2025-01-06");
  await expect(page.getByTestId("field-catalogue")).toContainText("cbd_congestion_fee");

  const api = async (extra: Record<string, string>) =>
    (await (await page.request.get(`/api/v1/explorer/rows?${apiQueryFromUrl(page, extra)}`)).json()) as {
      total: number;
      rows: Record<string, unknown>[];
    };
  const all = await api({});
  await expect(page.getByTestId("row-count")).toContainText(`of ${all.total.toLocaleString("en-US")} matching`);

  // Only rows flagged with an implausible amount.
  await page.getByLabel("Only rows with flag").selectOption("invalid_amount");
  const flagged = await api({ flag: "invalid_amount" });
  await expect(page.getByTestId("row-count")).toContainText(`of ${flagged.total.toLocaleString("en-US")} matching`);
  await expect(page.getByTestId("explorer-table").locator("tbody tr").first()).toContainText("Implausible amount");

  // Sort by total amount (header click toggles desc → asc).
  await page.getByLabel("Only rows with flag").selectOption("");
  await page.getByRole("button", { name: "Total amount" }).click();
  const sorted = await api({ sort: "total_amount", order: "desc", page_size: "50" });
  const top = Number(sorted.rows[0]!.total_amount);
  await expect(page.getByTestId("explorer-table").locator("tbody tr").first()).toContainText(
    top.toLocaleString("en-US", { style: "currency", currency: "USD" }),
  );

  // Next page.
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByTestId("row-count")).toContainText("Rows 51–100");

  // Download the extract: header + every matching row (under the export limit).
  await page.getByTestId("download-csv").click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const download = await downloadPromise;
  const text = await (await download.createReadStream()).toArray().then((chunks) => Buffer.concat(chunks).toString("utf8"));
  const lines = text.trim().split("\n");
  expect(lines[0]).toBe("pickup_datetime,pickup_location_id,dropoff_location_id,trip_distance,trip_duration_minutes,passenger_count,payment_type,total_amount,tip_amount,quality_flags");
  expect(lines.length - 1).toBe(Math.min(all.total, 100_000));
  await expect(page.getByText(/Downloaded [\d,]+ rows/)).toBeVisible();
});
