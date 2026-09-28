import path from "node:path";

import type { NextConfig } from "next";

// The browser only talks to Next; /api/* is proxied to the ARC backend.
// One forwarded port in Codespaces, no CORS configuration on the backend.
const backendUrl = process.env.ARC_BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Production image (frontend/Dockerfile) runs the self-contained server from .next/standalone.
  // Tracing starts at the monorepo root so workspace-hoisted dependencies are included.
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, ".."),
  // The dev server only trusts localhost by default. Codespaces serves the app from
  // *.app.github.dev; without this, dev assets and the proxied session socket are blocked.
  allowedDevOrigins: ["127.0.0.1", "*.app.github.dev"],
  // The floating dev badge covers the utterance input.
  devIndicators: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/:path*` }];
  },
};

export default nextConfig;
