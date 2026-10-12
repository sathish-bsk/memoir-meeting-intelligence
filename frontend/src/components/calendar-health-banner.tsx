"use client";

/**
 * Phase 2 Track B (notetaker-email plan §4.5 layer B4) — prominent
 * banner shown when any of the user's calendar connections is in a
 * `token_invalid` state with auto-record turned on.
 *
 * Mounted near the top of the app shell so it stays visible across
 * routes. Calls /api/calendar/health/status once per page mount (so
 * once per full reload, and also when auth state flips so a fresh
 * login re-checks).
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

interface HealthState {
  needsReconnect: boolean;
  providers: string[];
}

export function CalendarHealthBanner() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [state, setState] = useState<HealthState>({ needsReconnect: false, providers: [] });

  const refresh = useCallback(async () => {
    if (!isAuthenticated) {
      setState({ needsReconnect: false, providers: [] });
      return;
    }
    try {
      const status = await api.getCalendarHealthStatus();
      const broken = status.connections.filter((c) => c.token_invalid && c.auto_record);
      setState({
        needsReconnect: status.needs_reconnect,
        providers: Array.from(new Set(broken.map((c) => c.provider))),
      });
    } catch {
      // Don't spam a banner on transient errors — keep last known state.
    }
  }, [isAuthenticated]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (!state.needsReconnect) return null;

  const providerLabel =
    state.providers.length === 1
      ? state.providers[0] === "google"
        ? "Google Calendar"
        : "Microsoft Outlook"
      : "calendar";

  return (
    <div
      role="alert"
      className="sticky top-0 z-50 flex items-center justify-center gap-3 border-b border-red-200 bg-red-50 px-4 py-2.5 text-sm font-medium text-red-900 shadow-sm"
    >
      <svg
        className="h-4 w-4 shrink-0"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
      <span>
        Your {providerLabel} connection has disconnected from Memoir. Auto-recording is
        paused until you reconnect.
      </span>
      <Link
        href="/settings/integrations"
        className="ml-2 rounded-md bg-red-900 px-3 py-1 text-xs font-bold text-white hover:bg-red-800"
      >
        Reconnect
      </Link>
    </div>
  );
}
