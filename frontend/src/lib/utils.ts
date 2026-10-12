import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

import type { SpeakerMapEntry, MemoirResponse } from "./types"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Replace all SPEAKER_XX patterns in a string with mapped display names.
 */
export function resolveSpeakerNames(
  text: string | null | undefined,
  speakerMap: Record<string, SpeakerMapEntry> | null | undefined,
): string {
  if (!text || !speakerMap) return text ?? "";
  // Match SPEAKER_XX and common LLM typos like SPOKER_XX
  return text.replace(/(?:SPEAKER|SPOKER)_(\d+)/gi, (match, num) => {
    const canonical = `SPEAKER_${num}`;
    const entry = speakerMap[canonical] ?? speakerMap[canonical.toUpperCase()];
    return entry?.name ?? match;
  });
}

/**
 * Return a deep-copy of a MemoirResponse with all SPEAKER_XX references
 * replaced by mapped display names. Covers narrative_summary, discovery_fields,
 * action_items, signals, next_steps, call_metadata participants, and disclaimer.
 */
export function resolveMemoir(
  memoir: MemoirResponse | null | undefined,
  speakerMap: Record<string, SpeakerMapEntry> | null | undefined,
): MemoirResponse | null | undefined {
  if (!memoir || !speakerMap) return memoir;

  const r = (t: string | null | undefined) => resolveSpeakerNames(t, speakerMap);

  return {
    ...memoir,
    narrative_summary: r(memoir.narrative_summary) || null,
    disclaimer: r(memoir.disclaimer) || null,
    discovery_fields: memoir.discovery_fields?.map((f) => ({
      ...f,
      value: r(f.value) || f.value,
      speaker: r(f.speaker) || f.speaker,
      quote: r(f.quote) || f.quote,
    })) ?? null,
    action_items: memoir.action_items?.map((a) => ({
      ...a,
      text: r(a.text) || a.text,
    })) ?? null,
    signals: memoir.signals
      ? {
          buying: memoir.signals.buying.map((s) => r(s) || s),
          risk: memoir.signals.risk.map((s) => r(s) || s),
          ...(memoir.signals.discussion_threads ? { discussion_threads: memoir.signals.discussion_threads } : {}),
        }
      : null,
    next_steps: memoir.next_steps
      ? {
          next_meeting: r(memoir.next_steps.next_meeting) || null,
          next_milestone: r(memoir.next_steps.next_milestone) || null,
          suggested_deal_stage: r(memoir.next_steps.suggested_deal_stage) || null,
          ...(memoir.next_steps.decisions ? { decisions: memoir.next_steps.decisions } : {}),
          ...(memoir.next_steps.open_questions ? { open_questions: memoir.next_steps.open_questions } : {}),
        }
      : null,
    call_metadata: memoir.call_metadata
      ? {
          ...memoir.call_metadata,
          participants: memoir.call_metadata.participants.map((p) => ({
            ...p,
            name: r(p.name) || p.name,
          })),
        }
      : null,
  };
}


/**
 * Compute the user-facing label for a meeting's source. Bot involvement
 * (bot_job_id IS NOT NULL) takes precedence over the raw `source` value —
 * a bot meeting could have source=extension/manual/calendar/zoom_cloud
 * depending on entry point, but the user just wants to see "Bot Recording".
 *
 * Mirror this rule in any filter dropdowns + admin views.
 */
export function getMeetingSourceLabel(meeting: {
  source?: string | null;
  bot_job_id?: string | null;
}): string {
  // Tab/upload/link-upload paths ALSO set bot_job_id (e.g. "tab-audio-<id>",
  // "upload-<id>", "link-upload-<id>") as a job reference — that's NOT
  // actual bot involvement. Real bot recordings get a raw UUID. So source
  // check wins, and only treat bot_job_id as a real-bot signal when it
  // doesn't carry a non-bot prefix.
  if (meeting.source === "extension_tab_audio") return "Extension Tab Recording";
  const nonBotPrefixes = ["tab-audio-", "upload-", "link-upload-"];
  const isRealBotJobId =
    !!meeting.bot_job_id && !nonBotPrefixes.some((p) => meeting.bot_job_id!.startsWith(p));
  if (isRealBotJobId) {
    return "Extension Bot Recording";
  }
  switch (meeting.source) {
    case "extension_tab_audio":
      return "Extension Tab Recording";
    case "web_recorder":
      return "Web Recorder";
    case "zoom_cloud":
      return "Zoom Cloud";
    case "teams_cloud":
      return "Teams Cloud";
    case "calendar":
      return "Calendar";
    case "manual":
      return "Bot";
    case "upload":
      return "Upload";
    case "extension":
      return "Extension";
    default:
      return meeting.source || "Unknown";
  }
}

export const MEETINGS_CHANGED_EVENT = "memoir:meetings-changed";

export function notifyMeetingsChanged() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(MEETINGS_CHANGED_EVENT));
}
