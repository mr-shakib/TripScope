import { defineConfig, devices } from "@playwright/test";

// Browser tests against a running API with published data. Default: starts `next dev` (proxying /api to :8000).
// Set E2E_BASE_URL to test a deployed build instead (e.g. http://127.0.0.1:8080 for the compose `web` service).
const externalBaseUrl = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: externalBaseUrl ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 960 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 960 } } }],
  ...(externalBaseUrl
    ? {}
    : {
        webServer: {
          command: "npm run dev",
          url: "http://127.0.0.1:3000/login",
          reuseExistingServer: true,
          timeout: 120_000,
        },
      }),
});
