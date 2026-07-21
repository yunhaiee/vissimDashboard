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
