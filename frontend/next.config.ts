import type { NextConfig } from "next";

const backendUrl = process.env.SLOTFLOW_BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // WSL/loopback dev clients hit the dev server via 127.0.0.1; without this Next blocks
  // cross-origin access to /_next/webpack-hmr (HMR), which breaks the page after load.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
      {
        source: "/health",
        destination: `${backendUrl}/health`,
      },
    ];
  },
};

export default nextConfig;
