"use client";

/**
 * Preserved v2 meeting detail page (the May-17 build before the v3 rewrite).
 *
 * Default `/meetings/[id]` now renders v3. Keep this route available for
 * quick visual comparison and as a fallback if v3 surfaces regressions.
 */

import { MeetingDetailV2 } from "@/components/meeting-detail-v2";

export default function MeetingDetailV2Page() {
  return <MeetingDetailV2 />;
}
