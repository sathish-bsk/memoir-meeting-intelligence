/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    const authUrl = process.env.AUTH_SERVICE_URL || "http://localhost:8001";
    const apiUrl = process.env.CORE_API_URL || "http://localhost:8005";
    const integrationUrl = process.env.INTEGRATION_SERVICE_URL || "http://localhost:8006";
    return [
      { source: "/auth/:path*", destination: `${authUrl}/auth/:path*` },
      { source: "/integrations/calendar/:path*", destination: `${integrationUrl}/integrations/calendar/:path*` },
      { source: "/integrations/zoom/:path*", destination: `${integrationUrl}/integrations/zoom/:path*` },
      { source: "/integrations/slack/:path*", destination: `${integrationUrl}/integrations/slack/:path*` },
      { source: "/api/:path*", destination: `${apiUrl}/api/:path*` },
    ];
  },
  // Permanent 308 for /calls → /meetings rename (May 2026 UI revamp).
  // Absorbs links from already-sent emails, durable notifications.link rows,
  // and old extension installs in the wild. Cost is zero (compile-time route rule).
  async redirects() {
    return [
      { source: "/calls", destination: "/meetings", permanent: true },
      { source: "/calls/:path*", destination: "/meetings/:path*", permanent: true },
    ];
  },
};

export default nextConfig;
