import type { NextConfig } from "next";
import path from "node:path";

// TRAP (measured 2026-09-08): NEXT_PUBLIC_API_BASE doubles as the rewrite
// target here. Anything that repoints the BROWSER's absolute API base -- a
// tunnel, a reverse proxy -- must set API_PROXY_TARGET back to the backend in
// the same breath, or /api/* rewrites to the public URL, which resolves to
// this dev server again: every API call loops until the client times out, and
// nothing is logged. scripts/share_demo.py sets both together.
const apiProxyTarget = (process.env.API_PROXY_TARGET ?? process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8001").replace(/\/+$/, "");
const defaultApiProxyMaxBodySizeBytes = 250 * 1024 * 1024;
const configuredApiProxyMaxBodySizeBytes = Number.parseInt(process.env.API_PROXY_MAX_BODY_SIZE_BYTES ?? "", 10);
const apiProxyMaxBodySizeBytes =
  Number.isFinite(configuredApiProxyMaxBodySizeBytes) && configuredApiProxyMaxBodySizeBytes > 0
    ? configuredApiProxyMaxBodySizeBytes
    : defaultApiProxyMaxBodySizeBytes;

// Remote access (2026-08-21): the dev server is reached from the user's laptop
// through a tailscale-serve reverse proxy, so the browser's Host is the tailnet
// name, not localhost. Next 16 blocks dev-resource requests from unknown
// origins by default (measured: Chrome AND Safari hung at "불러오는 중…" while
// curl — which sends no browser origin metadata — sailed through; the dev log
// names /_next/webpack-hmr as blocked). Extra origins can be supplied via
// ALLOWED_DEV_ORIGINS (comma-separated) without editing this file.
const extraDevOrigins = (process.env.ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.resolve(__dirname),
  allowedDevOrigins: ["desktop-ck.tailf57fd1.ts.net", ...extraDevOrigins],
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
