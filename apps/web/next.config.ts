import path from "node:path";
import type { NextConfig } from "next";

const API = process.env.OA_API_URL || "http://127.0.0.1:8100";

const nextConfig: NextConfig = {
  reactStrictMode: false,
  outputFileTracingRoot: path.join(__dirname, "../.."),
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/socket.io/:path*", destination: `${API}/socket.io/:path*` },
    ];
  },
};

export default nextConfig;
