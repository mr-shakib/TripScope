import { expect, test } from "@playwright/test";

import { analyst, guardPageErrors, liveModel, signIn, viewer } from "./helpers";

/*
 * AI analyst against the real stack. Demo questions need no model and always run; the live-model tests (a
 * real question, an AI-drafted report) run with E2E_LLM=1 when the API has LLM_* configured.
 */
test.skip(!analyst.email || !viewer.email, "set E2E_ANALYST_* and E2E_VIEWER_* accounts");
guardPageErrors();

interface Zone {
  label: string;
  trips: number;
}

test("a demo question is answered from the tools, with matching figures and evidence", async ({ page }) => {
  await signIn(page, analyst, "/ai?start=2025-03-01&end=2025-03-31");
  await page.getByTestId("demo-top_pickup_zones").click();
  const answer = page.locator('[data-testid="ai-message"][data-status="answered"]');
  await expect(answer).toBeVisible({ timeout: 30_000 });
  await expect(answer).toHaveAttribute("data-mode", "demo");
  await expect(page).toHaveURL(/[?&]c=[0-9a-f-]{36}/);

  // The figures in the text are the API's, and every one is marked as matching the tool results.
  const zones = (await (await page.request.get("/api/v1/analytics/top-pickup-zones?start_date=2025-03-01&end_date=2025-03-31&limit=5")).json()).groups as Zone[];
  const text = answer.getByTestId("ai-answer-text");
  for (const zone of zones) await expect(text).toContainText(`${zone.label} (${zone.trips.toLocaleString("en-US")} trips`);
  const badge = answer.getByTestId("ai-verification");
  expect(await badge.getAttribute("data-verified")).toBe(await badge.getAttribute("data-total"));
  await expect(text.locator('[data-verified="false"]')).toHaveCount(0);

  // Evidence: the tool, its arguments (the dashboard filters given as context), period and source table.
  await answer.getByTestId("ai-evidence").getByRole("button").click();
  const evidence = answer.getByTestId("evidence-get_top_zones");
  await expect(evidence).toContainText("Period: Mar 1 – Mar 31, 2025");
  await expect(evidence).toContainText("start_date=2025-03-01");
  await expect(evidence).toContainText(zones[0]!.label);
  await expect(answer.getByTestId("ai-chart")).toBeVisible();

  // The conversation is listed and can be deleted.
  const title = "Which pickup zones had the most trips in the selected period?";
  const deleteButtons = page.getByTestId("ai-conversations").getByRole("button", { name: `Delete ${title}` });
  const before = await deleteButtons.count();
  expect(before).toBeGreaterThan(0);
  await deleteButtons.first().click();
  await expect(deleteButtons).toHaveCount(before - 1);
});

test("viewers cannot use the AI analyst unless an administrator enables it", async ({ page }) => {
  await signIn(page, viewer, "/ai");
  await expect(page.getByText("The AI analyst is not available to your role")).toBeVisible();
  expect((await page.request.post("/api/v1/ai/chat", { data: { demo_id: "top_pickup_zones" } })).status()).toBe(403);
});

test("a live model answers with tool evidence and checked figures", async ({ page }) => {
  test.skip(!liveModel, "set E2E_LLM=1 with a configured model");
  test.setTimeout(240_000);
  await signIn(page, analyst, "/ai");
  await expect(page.getByTestId("ai-status")).toHaveAttribute("data-enabled", "true");
  await page.getByTestId("ai-input").fill("Which pickup zones had the most trips in March 2025?");
  await page.getByTestId("ai-send").click();
  await expect(page.getByTestId("ai-steps")).toBeVisible();
  const answer = page.locator('[data-testid="ai-message"][data-status="answered"]');
  await expect(answer).toBeVisible({ timeout: 200_000 });
  const badge = answer.getByTestId("ai-verification");
  const total = Number(await badge.getAttribute("data-total"));
  expect(total).toBeGreaterThan(0);
  expect(Number(await badge.getAttribute("data-verified"))).toBe(total);
  await answer.getByTestId("ai-evidence").getByRole("button").click();
  await expect(answer.getByTestId("evidence-get_top_zones")).toContainText("Midtown Center");
});

test("an AI-assisted report is outlined, drafted with checked figures and exported", async ({ page }) => {
  test.skip(!liveModel, "set E2E_LLM=1 with a configured model");
  test.setTimeout(300_000);
  await signIn(page, analyst, "/reports?start=2025-03-01&end=2025-03-31");
  await page.getByTestId("template-custom_ai").click();
  await page.getByTestId("ai-focus").fill("How did demand and fares behave in March?");
  await page.getByTestId("ai-outline").click();
  await expect(page.getByTestId("ai-outline-sections")).toBeVisible({ timeout: 120_000 });
  await page.getByTestId("ai-draft-create").click();
  await expect(page).toHaveURL(/\/reports\/[0-9a-f-]{36}$/);
  const banner = page.locator('[data-testid="ai-narrative"][data-status="ready"]');
  await expect(banner).toBeVisible({ timeout: 200_000 });
  await expect(page.getByTestId("preview-narrative-note")).toContainText("figures checked against them");
  await page.getByTestId("generate-pdf").click();
  await expect(page.locator('[data-format="pdf"][data-status="completed"]')).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete report" }).click();
  await expect(page).toHaveURL(/\/reports$/);
});
