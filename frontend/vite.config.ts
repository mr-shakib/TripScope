import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server proxies the API so the session cookie stays same-origin (no CORS with credentials).
const apiTarget = process.env.TRIPSCOPE_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: false },
      "/health": { target: apiTarget },
      "/ready": { target: apiTarget },
    },
  },
  // The lazily loaded ECharts chunk is ~500 kB minified (171 kB gzip); everything else is far smaller.
  build: { sourcemap: true, chunkSizeWarningLimit: 600 },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
  },
});
