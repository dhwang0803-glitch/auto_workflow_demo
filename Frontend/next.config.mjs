/** @type {import('next').NextConfig} */
const nextConfig = {
  // Dev: same-origin fetches for /api/* land on FastAPI without CORS plumbing.
  // Prod will route through a real ingress, so this rewrite is dev-only.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    const target = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
