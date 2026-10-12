import { NextRequest, NextResponse } from "next/server";

/**
 * Redirect OAuth callbacks from ngrok back to localhost so the callback
 * is handled with the user's auth session during local development.
 *
 * In production, this middleware does nothing.
 */
export function middleware(request: NextRequest) {
  const { searchParams, pathname } = request.nextUrl;

  // Only act on the integrations page with OAuth callback params
  if (pathname !== "/integrations") return;

  const code = searchParams.get("code");
  const state = searchParams.get("state");
  if (!code || !state) return;

  // Only redirect in development (ngrok → localhost)
  if (process.env.NODE_ENV !== "development") return;

  const host =
    request.headers.get("x-forwarded-host") ||
    request.headers.get("host") ||
    "";

  // If already on localhost, let the page handle it
  if (host.startsWith("localhost") || host.startsWith("127.0.0.1")) return;

  // ngrok domain — redirect to localhost with same params
  const localhostUrl = `http://localhost:3000/integrations?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`;
  return NextResponse.redirect(localhostUrl);
}

export const config = {
  matcher: "/integrations",
};
