import { type Download, expect, type Page, test } from "@playwright/test";

import { analyst, apiOverview, guardPageErrors, signIn, viewer } from "./helpers";

/*
 * Report center against the real stack, including a running report worker (`make report-worker` or the
 * report-worker container). Follows spec §12.3: sign in as analyst, filter, preview, export PDF and Excel,
 * confirm the history. Needs E2E_ANALYST_* and E2E_VIEWER_* accounts.
 */
test.skip(!analyst.email || !viewer.email, "set E2E_ANALYST_EMAIL/PASSWORD and E2E_VIEWER_EMAIL/PASSWORD");
guardPageErrors();
test.describe.configure({ mode: "serial" });

let reportUrl = "";

async function firstBytes(download: Download, count: number): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).subarray(0, count).toString("latin1");
}

async function downloadFrom(page: Page, format: "pdf" | "xlsx", label: string): Promise<Download> {
  const row = page.locator(`[data-format="${format}"][data-status="completed"]`).first();
  await expect(row).toBeVisible({ timeout: 60_000 });
  const pending = page.waitForEvent("download");
  await row.getByRole("button", { name: `Download ${label}` }).click();
  return pending;
}

test("analyst previews an executive report, exports PDF and Excel, and history records them", async ({ page }) => {
  await signIn(page, analyst, "/reports?start=2025-03-01&end=2025-03-31");
  await page.getByTestId("template-executive_overview").click();
  await expect(page).toHaveURL(/\/reports\/[0-9a-f-]{36}$/);
  reportUrl = new URL(page.url()).pathname;

  // The preview is the document the files are rendered from; its KPIs equal the analytics API.
  await expect(page.getByTestId("preview-title")).toHaveText("Executive overview · Mar 1 – Mar 31, 2025");
  const march = await apiOverview(page, "start_date=2025-03-01&end_date=2025-03-31");
  await expect(page.getByTestId("preview-kpi-total_trips")).toHaveAttribute("data-value", String(march.kpis.total_trips!.value));
  await expect(page.getByTestId("preview-kpi-total_trips")).toContainText(march.kpis.total_trips!.value!.toLocaleString("en-US"));
  await expect(page.getByTestId("preview-findings")).toContainText("versus the previous 31 days");

  // Unsaved edits block exports, so a file can never differ from the preview.
  await page.getByTestId("report-title-input").fill("March executive report");
  await expect(page.getByTestId("generate-pdf")).toBeDisabled();
  await page.getByRole("radio", { name: "Shared" }).click();
  await page.getByTestId("save-report").click();
  await expect(page.getByTestId("preview-title")).toHaveText("March executive report");
  await expect(page.getByTestId("report-heading")).toHaveText("March executive report");

  await page.getByTestId("generate-pdf").click();
  await page.getByTestId("generate-xlsx").click();
  const pdf = await downloadFrom(page, "pdf", "PDF");
  expect(pdf.suggestedFilename()).toBe("tripscope-executive-overview-20250301-20250331.pdf");
  expect(await firstBytes(pdf, 5)).toBe("%PDF-");
  const xlsx = await downloadFrom(page, "xlsx", "Excel");
  expect(xlsx.suggestedFilename()).toMatch(/\.xlsx$/);
  expect(await firstBytes(xlsx, 2)).toBe("PK"); // an Office Open XML (zip) workbook

  // Report history lists the report with both completed files.
  await page.goto("/reports");
  const row = page.getByTestId(`report-row-${reportUrl.split("/").pop()}`);
  await expect(row).toContainText("March executive report");
  await expect(row).toContainText("Mar 1, 2025 – Mar 31, 2025");
  await expect(row.getByRole("button", { name: /Download PDF/ })).toBeVisible();
  await expect(row.getByRole("button", { name: /Download Excel/ })).toBeVisible();
});

test("viewers download a shared report but cannot generate or edit it", async ({ page }) => {
  await signIn(page, viewer, "/reports");
  await expect(page.getByTestId("report-templates")).toHaveCount(0);
  await page.getByRole("link", { name: "March executive report" }).first().click();
  await expect(page.getByTestId("preview-title")).toHaveText("March executive report");
  await expect(page.getByTestId("generate-pdf")).toHaveCount(0);
  await expect(page.getByTestId("save-report")).toHaveCount(0);
  const pdf = await downloadFrom(page, "pdf", "PDF");
  expect(await firstBytes(pdf, 5)).toBe("%PDF-");
});

test("analyst deletes the report", async ({ page }) => {
  await signIn(page, analyst, reportUrl);
  await page.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete report" }).click();
  await expect(page).toHaveURL(/\/reports$/);
  await page.goto(reportUrl);
  await expect(page.getByText("Report not found")).toBeVisible();
});

test("explorer downloads an Excel extract", async ({ page }) => {
  await signIn(page, viewer, "/explore?start=2025-01-06&end=2025-01-06");
  await expect(page.getByTestId("row-count")).toContainText("matching");
  await page.getByTestId("download-xlsx").click();
  await expect(page.getByRole("alertdialog")).toContainText("Excel extract");
  const pending = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toMatch(/^tripscope-trips-.*\.xlsx$/);
  expect(await firstBytes(download, 2)).toBe("PK");
});
