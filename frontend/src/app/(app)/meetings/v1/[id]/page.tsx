"use client";

/**
 * Preserved v1 meeting detail page.
 *
 * Default `/meetings/[id]` renders the v2 build; navigate here when you
 * need the legacy workspace (transcript editing, CRM push tab, deal
 * timeline, processing-stage bar, etc. — see plan/ui_revamp_detail_page.md
 * for the full list). The v2 build will reach feature parity through
 * phases D1-D7; until then, this route is the production fallback.
 */

import { MeetingDetailV1 } from "@/components/meeting-detail-v1";

export default function MeetingDetailV1Page() {
  return <MeetingDetailV1 />;
}
