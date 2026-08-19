import type { NextConfig } from "next";
import path from "node:path";

const apiProxyTarget = (process.env.API_PROXY_TARGET ?? process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8001").replace(/\/+$/, "");
const defaultApiProxyMaxBodySizeBytes = 250 * 1024 * 1024;
const configuredApiProxyMaxBodySizeBytes = Number.parseInt(process.env.API_PROXY_MAX_BODY_SIZE_BYTES ?? "", 10);
const apiProxyMaxBodySizeBytes =
  Number.isFinite(configuredApiProxyMaxBodySizeBytes) && configuredApiProxyMaxBodySizeBytes > 0
    ? configuredApiProxyMaxBodySizeBytes
    : defaultApiProxyMaxBodySizeBytes;

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.resolve(__dirname),
  experimental: {
    proxyClientMaxBodySize: apiProxyMaxBodySizeBytes,
  },
  // Unit #50: base-model validation stopped being its own destination and
  // became the calibration workspace's scoring station (the user's 2026-08-19
  // page-cleanup decision). The route is gone, the SCREEN is not — it renders
  // at /calibration — so an existing bookmark must land on the work rather
  // than on a 404. `permanent: true` (308) because this is not a trial: the
  // page will not come back to /base-model.
  async redirects() {
    return [
      {
        source: "/base-model",
        destination: "/calibration",
        permanent: true,
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiProxyTarget}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
