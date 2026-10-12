"use client";

/**
 * Status pill rendered on meeting list cards. Shared by:
 *   - meetings/page.tsx (legacy/v1)
 *   - meetings-v2.tsx
 *   - meetings-v3/index.tsx
 *
 * Source of truth for the per-status color palette and label mapping so
 * all three surfaces stay in sync.
 */

const STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-50 text-emerald-700 border-emerald-200",
  recording: "bg-red-50 text-red-700 border-red-200",
  processing: "bg-amber-50 text-amber-700 border-amber-200",
  analyzing: "bg-violet-50 text-violet-700 border-violet-200",
  waiting_for_transcription: "bg-amber-50 text-amber-700 border-amber-200",
  failed: "bg-rose-50 text-rose-700 border-rose-200",
  joining: "bg-blue-50 text-blue-700 border-blue-200",
};

const STATUS_LABELS: Record<string, string> = {
  completed: "Completed",
  recording: "Recording",
  processing: "Processing",
  analyzing: "Analyzing",
  waiting_for_transcription: "Queued",
  failed: "Failed",
  joining: "Joining",
};

export function MeetingStatusBadge({
  status,
  intelligenceStatus,
}: {
  status: string | null | undefined;
  /** When intelligence is still running on a completed recording we show
   *  "Analyzing" instead of "Completed" — matches the v3 behavior. */
  intelligenceStatus?: string | null;
}) {
  if (!status) return null;
  const effective =
    status === "completed" && intelligenceStatus && intelligenceStatus !== "completed"
      ? "analyzing"
      : status;
  const klass =
    STATUS_STYLES[effective] ||
    "bg-memoir-bg text-memoir-text-muted border-memoir-border";
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium border ${klass}`}
    >
      {STATUS_LABELS[effective] || effective}
    </span>
  );
}
