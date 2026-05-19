/** @type {import('next').NextConfig} */
const backendUrl = process.env.TRACK_ANYWHERE_BACKEND_URL ?? "http://127.0.0.1:8001";

const nextConfig = {
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`
      }
    ];
  }
};

export default nextConfig;
