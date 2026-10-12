"use client";

/**
 * /meetings/v2 — explicit rollback route to the pre-v3 Meetings list.
 *
 * This route always renders MeetingsV2 regardless of any feature flag,
 * so once v3 ships in production the operator can deep-link here as a
 * safety net while debugging or comparing the two surfaces.
 *
 * Plan: plan/meetings-events-v3-list.md §4.1.
 */

import { MeetingsV2 } from "@/components/meetings-v2";

export default function MeetingsV2Page() {
  return <MeetingsV2 />;
}
