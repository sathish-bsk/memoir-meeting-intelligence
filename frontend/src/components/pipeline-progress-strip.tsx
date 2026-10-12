"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Pipeline progress strip — single horizontal bar at the top of the
 * meeting detail page that shows each backend stage with its real
 * percentage from /processing-status (Redis-backed). Stages match the
 * backend pipeline order T1→T4→T6→T7→T5→T3→T2:
 *
 *   preprocess (T1) → enhance (T4) → diarization (T6) → fast_summary (T7)
 *     → transcribe (T5) → ai_analysis (T3) → crm_sync (T2)
 *
 * "Email" is NOT a stage — fast email fires after fast_summary, polished
 * email fires after transcribe. Both are side-effects of those stages
 * completing.
 *
 * Auto-hides when meeting.status is completed or fast_email_only (since
 * everything terminal is in the artifacts panels already).
 */

interface ProcessingStatus {
  active_stage?: string;
  stages?: Record<string, { percent: number; label: string }>;
  transcription?: string;
  summary?: string;
  intelligence?: string;
  crm_extraction?: string;
}

interface Props {
  meetingId: string | undefined;
  status: string | undefined;
  // Optional: parent-supplied /processing-status payload. When provided
  // the strip skips its own poller and reads from this prop, avoiding a
  // duplicate /processing-status request every 5 s (the v3 detail page
  // already polls this endpoint for its phase-gated artifact refetches).
  data?: ProcessingStatus | null;
}

// Render order matches the pipeline order users actually experience.
// Each entry maps a backend stage key (matches the /processing-status
// Redis tuple at meetings.py:1682) to a user-friendly label.
const STAGE_ORDER: Array<{ key: string; label: string }> = [
  { key: "preprocess", label: "Preprocess" },
  { key: "enhance", label: "Enhance" },
  { key: "diarization", label: "Diarize" },
  { key: "fast_summary", label: "Fast Summary" },
  { key: "transcribe", label: "Transcribe" },
  { key: "ai_analysis", label: "AI Analysis" },
  { key: "crm_sync", label: "CRM Sync" },
];

function classifyTone(pct: number, isActive: boolean): "done" | "active" | "pending" {
  if (pct >= 100) return "done";
  if (isActive || pct > 0) return "active";
  return "pending";
}

const TONE_CLASSES: Record<"done" | "active" | "pending" | "failed", { fill: string; chip: string; text: string }> = {
  done: { fill: "bg-emerald-500", chip: "bg-emerald-50 border-emerald-200", text: "text-emerald-700" },
  active: { fill: "bg-brand", chip: "bg-brand/10 border-brand/30", text: "text-brand" },
  pending: { fill: "bg-surface-border", chip: "bg-surface-soft border-surface-border", text: "text-ink-secondary" },
  failed: { fill: "bg-red-500", chip: "bg-red-50 border-red-200", text: "text-red-700" },
};

export function PipelineProgressStrip({ meetingId, status, data: dataProp }: Props) {
  const [ownData, setOwnData] = useState<ProcessingStatus | null>(null);
  // Prefer parent-supplied data (single source of truth). Fall back to
  // self-fetched data when used outside the v3 detail page.
  const data = dataProp ?? ownData;

  // Hide logic — must satisfy BOTH (a) the pipeline is genuinely done
  // and (b) we trust the signal. The two reliable phase fields from
  // /processing-status are `intelligence` and `crm_extraction`; they
  // come from the meeting row + transcription_jobs and survive Redis
  // TTL expiry (unlike `stages`). We hide when both are terminal.
  //
  // Redis-backed per-stage `data.stages` is great for live progress
  // animation, but expires after some minutes — on completed meetings
  // we see all stages = 0% even though everything succeeded. That's
  // why `allStagesDone` alone is unreliable.
  const failed = status === "failed" || status === "transcription_failed";
  const isPhaseTerminal = (s: string | undefined) =>
    s === "completed" || s === "skipped" || s === "failed";
  const phasesDone =
    !!data &&
    isPhaseTerminal(data.intelligence) &&
    isPhaseTerminal(data.crm_extraction) &&
    isPhaseTerminal(data.transcription);
  const allStagesDone =
    !!data && STAGE_ORDER.every((s) => (data.stages?.[s.key]?.percent ?? 0) >= 100);
  const allDone = phasesDone || allStagesDone;

  useEffect(() => {
    // Skip self-polling when parent is feeding data via prop.
    if (dataProp !== undefined) return;
    if (!meetingId || failed || allDone) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await api.getProcessingStatus(meetingId);
        if (!cancelled) setOwnData(s as unknown as ProcessingStatus);
      } catch {
        /* transient — retry next tick */
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [meetingId, failed, allDone, dataProp]);

  if (!status) return null;
  if (allDone) return null;
  // `completed` is NOT in this hide-set — meeting.status flips to
  // 'completed' after T5 transcribe, but T3 (AI Analysis) and T2
  // (CRM Sync) keep running for another minute or two. Hiding on
  // status='completed' alone kills the strip during the very window
  // users care about. The strip will still hide once `allDone` flips
  // true (every phase terminal) above. For meetings where Redis TTL
  // has expired AND phases are unreachable (rare cold-read), the
  // 'completed' status still bubbles up via the parent page, and the
  // empty-phases case is handled by the data-null short circuit.
  const STATUS_TERMINAL = new Set(["fast_email_only", "failed", "transcription_failed"]);
  if (STATUS_TERMINAL.has(status)) return null;

  const stagesMap = data?.stages ?? {};
  const activeKey = data?.active_stage ?? "";

  type Tone = "done" | "active" | "pending" | "failed";
  const stages: Array<{ key: string; label: string; pct: number; tone: Tone }> = STAGE_ORDER.map((s) => {
    const pct = stagesMap[s.key]?.percent ?? 0;
    const tone: Tone = failed ? "failed" : classifyTone(pct, activeKey === s.key);
    return { ...s, pct, tone };
  });

  const totalPct = failed
    ? 0
    : Math.round(stages.reduce((acc, s) => acc + Math.min(100, s.pct), 0) / stages.length);

  return (
    <div className="shrink-0 border-b border-surface-border bg-surface-soft px-7 pt-3">
      <div className="mb-4 rounded-md border border-surface-border bg-surface px-4 py-3">
      <div className="mb-2 flex items-center justify-between text-[11px] font-extrabold uppercase tracking-wider text-ink-secondary">
        <span>{failed ? "Pipeline failed" : `Processing — ${totalPct}%`}</span>
        <span className="text-[10px] font-semibold">{activeKey ? `Active: ${activeKey}` : status}</span>
      </div>
      <div className="flex gap-1.5">
        {stages.map((s) => {
          const t = TONE_CLASSES[s.tone];
          const widthPct = s.tone === "done" ? 100 : s.tone === "failed" ? 100 : Math.min(100, Math.max(2, s.pct));
          return (
            <div
              key={s.key}
              className={`flex flex-1 flex-col gap-1 rounded border px-2 py-1.5 ${t.chip}`}
              title={`${s.label}: ${s.pct}%`}
            >
              <div className="flex items-baseline justify-between gap-1">
                <span className={`truncate text-[10px] font-semibold ${t.text}`}>
                  {s.label}
                </span>
                <span className={`shrink-0 text-[9px] font-extrabold ${t.text}`}>
                  {s.tone === "done" ? "✓" : s.tone === "failed" ? "✕" : `${s.pct}%`}
                </span>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-surface-border">
                <div
                  className={`h-full transition-all ${t.fill}`}
                  style={{ width: `${widthPct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
      </div>
    </div>
  );
}
