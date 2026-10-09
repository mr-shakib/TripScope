import { defineConfig, devices } from "@playwright/test";

// End-to-end smoke test against a running API with published data.
// Default: starts the Vite dev server (proxying /api to :8000). Set E2E_BASE_URL to test a deployed build
// instead, e.g. E2E_BASE_URL=http://127.0.0.1:8080 for the docker compose `web` service.
const externalBaseUrl = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: externalBaseUrl ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  ...(externalBaseUrl
    ? {}
    : {
        webServer: {
          command: "npm run dev -- --strictPort",
          url: "http://127.0.0.1:5173",
          reuseExistingServer: true,
          timeout: 60_000,
        },
      }),
});
