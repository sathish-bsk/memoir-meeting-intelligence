"use client";

/**
 * /events — top-level Events tab (plan/meetings-events-v3-list.md §4 + §11.6).
 *
 * When the v3 flag is off, returns a 404-equivalent. Once the flag is on,
 * mounts the EventsV3 calendar-strip surface. No v2 fallback exists (this
 * route is new in v3).
 */

import { notFound } from "next/navigation";

import { EventsV3 } from "@/components/events-v3";

export default function EventsPage() {
  if (process.env.NEXT_PUBLIC_FEATURE_EVENTS_LIST_V3 !== "true") {
    notFound();
  }
  return <EventsV3 />;
}
