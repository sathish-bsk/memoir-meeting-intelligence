// Revamp v2 feature-flag env declarations.
//
// Set these in `.env.local` (gitignored) or via the runtime environment.
// Both flags default OFF — when unset/false, the v1 pages render.
//
//   NEXT_PUBLIC_HOME_V2=true       → renders frontend/src/components/home-v2.tsx
//                                    instead of the existing /home page body
//   NEXT_PUBLIC_MEETINGS_V2=true   → renders frontend/src/components/meetings-v2.tsx
//                                    + MeetingPeekPanel instead of the existing
//                                    1700-line /meetings page
//
// Drop these flags + the dispatcher checks in `home/page.tsx` + `meetings/page.tsx`
// once the v2 surfaces are the canonical render path and v1 is deleted.

declare namespace NodeJS {
  interface ProcessEnv {
    /** Revamp v2 home page (default false). */
    NEXT_PUBLIC_HOME_V2?: "true" | "false";
    /** Revamp v2 meetings list + peek panel (default false). */
    NEXT_PUBLIC_MEETINGS_V2?: "true" | "false";
    /** Meetings v3 list (plan/meetings-events-v3-list.md). When true, /meetings renders the new calendar-strip list; /meetings/v2 stays as fallback. */
    NEXT_PUBLIC_FEATURE_MEETINGS_LIST_V3?: "true" | "false";
    /** Events v3 list (plan/meetings-events-v3-list.md). When true, /events route mounts; when false, /events 404s (no v2 exists for events). */
    NEXT_PUBLIC_FEATURE_EVENTS_LIST_V3?: "true" | "false";
  }
}
