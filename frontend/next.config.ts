import type { NextConfig } from "next";

// The API origin is baked in at build time (rewrites are serialized into the standalone server).
// Dev: the FastAPI server on the host. Docker: the `api` service (build arg TRIPSCOPE_API_ORIGIN).
const apiOrigin = process.env.TRIPSCOPE_API_ORIGIN ?? "http://127.0.0.1:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  devIndicators: false,
  reactStrictMode: true,
  turbopack: {
    rules: {
      "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" },
    },
  },
  // Same-origin API: the HttpOnly session cookie stays first-party and no CORS is needed (ADR-14).
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiOrigin}/api/:path*` },
      { source: "/health", destination: `${apiOrigin}/health` },
      { source: "/ready", destination: `${apiOrigin}/ready` },
    ];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
