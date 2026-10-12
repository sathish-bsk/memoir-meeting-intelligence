"use client";

/**
 * Meeting Detail v3 — three-tab layout per `memoir-meeting-detail.jsx`.
 *
 * Layout:
 *   ┌─ topbar (Back · Find&Replace · Share)
 *   ├─ optional find&replace strip
 *   ├─ meeting header card (platform · date · duration · title · participants)
 *   ├─ tabs (AI Summary / Transcript / CRM Update)
 *   └─ 1fr + 340px grid:
 *       left: active-tab body
 *       right: Recording · Speakers · Coaching Metrics
 *
 * Data wiring reuses existing endpoints. New endpoint just for editable
 * Milestone + Next Meeting (`/api/meetings/{id}/next-steps`).
 *
 * v1 detail remains at `/meetings/v1/[id]` for CRM push workspace,
 * email send dialog, and share approval — features intentionally not
 * mirrored in this clean rebuild.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Edit3,
  FileSpreadsheet,
  FileText,
  Link2,
  MessageSquare,
  Mic,
  Music,
  Pause,
  Play,
  Plus,
  Replace,
  Search,
  Share2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import { api } from "@/lib/api";
import type {
  CallIntelligenceResponse,
  CoachingResponse,
  CrmConnection,
  CrmField,
  CrmPushResult,
  MeetingDetail,
  MeetingParticipantInfo,
  MemoirResponse,
  TranscriptResponse,
  TranscriptSegment,
} from "@/lib/types";
import { resolveMemoir } from "@/lib/utils";
import { PlatformBadge, platformFromSourceOrUrl } from "@/components/ui/platform-badge";
import { SpeakerMappingDropdown } from "@/components/speaker-mapping-dropdown";
import { SpeakerMergeDialog } from "@/components/speaker-merge-dialog";
import { PipelineProgressStrip } from "@/components/pipeline-progress-strip";

// ────────────────────────────────────────────────────────────────────────────
// Tiny helpers
// ────────────────────────────────────────────────────────────────────────────


function fmtDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function fmtTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function fmtDuration(secs: number | null): string {
  if (!secs) return "—";
  const m = Math.round(secs / 60);
  return `${m} min`;
}

function fmtClock(secs: number): string {
  if (!Number.isFinite(secs) || secs < 0) return "0:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtSegTime(seg: TranscriptSegment): string {
  const sec = (seg.original_start ?? seg.start) || 0;
  return fmtClock(sec);
}

function escRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}


// Assign a stable color per speaker by index.
const SPEAKER_PALETTE = [
  "#5C5CE0", // brand
  "#E8683A", // orange
  "#00B87C", // green
  "#F5A623", // amber
  "#9D5CE0", // purple
  "#E5484D", // red
];

function speakerColor(label: string | undefined, allLabels: string[]): string {
  if (!label) return SPEAKER_PALETTE[0];
  const idx = allLabels.indexOf(label);
  return SPEAKER_PALETTE[(idx >= 0 ? idx : 0) % SPEAKER_PALETTE.length];
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() || "")
    .join("");
}

// ────────────────────────────────────────────────────────────────────────────
// EditableField — D2
// ────────────────────────────────────────────────────────────────────────────

interface EditableFieldProps {
  value: string;
  onChange: (next: string) => void;
  multiline?: boolean;
  placeholder?: string;
  inline?: boolean;
  className?: string;
  /**
   * Compact mode: removes the padded card chrome and lets the field sit
   * inline with surrounding bullet/checkbox markers (used in lists).
   * The hover/edit affordance shows via a small edit icon only.
   */
  compact?: boolean;
}

function EditableField({
  value,
  onChange,
  multiline = false,
  placeholder = "",
  inline = false,
  className = "",
  compact = false,
}: EditableFieldProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  const save = useCallback(() => {
    setEditing(false);
    if (draft !== value) onChange(draft);
  }, [draft, value, onChange]);

  const cancel = useCallback(() => {
    setDraft(value);
    setEditing(false);
  }, [value]);

  if (editing) {
    return multiline ? (
      <div className={className}>
        <textarea
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Escape") cancel();
          }}
          className="w-full rounded-lg border-2 border-brand bg-white px-3 py-2.5 text-[13.5px] font-normal leading-[1.7] text-ink outline-none"
          style={{ minHeight: 80, resize: "vertical" }}
        />
        <div className="mt-1.5 flex gap-1.5">
          <button
            onMouseDown={(e) => e.preventDefault()}
            onClick={save}
            className="rounded-md bg-brand px-3 py-1 text-[11px] font-extrabold text-white"
          >
            Save
          </button>
          <button
            onMouseDown={(e) => e.preventDefault()}
            onClick={cancel}
            className="rounded-md border border-surface-border bg-surface-soft px-3 py-1 text-[11px] font-extrabold text-ink-secondary"
          >
            Cancel
          </button>
        </div>
      </div>
    ) : (
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
          if (e.key === "Escape") cancel();
        }}
        className={`w-full rounded-md border-2 border-brand bg-white px-2.5 py-1.5 text-[13px] font-medium text-ink outline-none ${className}`}
      />
    );
  }

  const displayValue = value || placeholder;
  const isEmpty = !value;

  // Compact: bare text with a hidden edit affordance — used in list items
  // where adjacent markers (checkbox, bullet) align to the text baseline.
  if (compact) {
    return (
      <span
        onClick={() => setEditing(true)}
        className={`group/edit inline-flex w-full cursor-text items-baseline gap-1 rounded px-1 -mx-1 transition-colors hover:bg-brand/5 ${className}`}
      >
        <span className={`text-[13px] leading-[1.5] ${isEmpty ? "italic text-ink-light" : "text-ink"}`}>
          {displayValue}
        </span>
        <Edit3 className="ml-auto h-3 w-3 shrink-0 self-center text-brand opacity-0 transition-opacity group-hover/edit:opacity-100" />
      </span>
    );
  }

  return (
    <div
      onClick={() => setEditing(true)}
      className={`group/edit relative cursor-text rounded-lg border-[1.5px] border-dashed border-transparent px-2.5 py-1.5 transition-colors hover:border-brand/40 hover:bg-brand/5 ${className}`}
    >
      {multiline ? (
        <p className="m-0 whitespace-pre-wrap text-[13.5px] font-normal leading-[1.7] text-ink">
          <span className={isEmpty ? "text-ink-light" : ""}>{displayValue}</span>
        </p>
      ) : (
        <span className={`text-[13px] font-medium ${isEmpty ? "text-ink-light" : inline ? "text-ink" : "text-ink"}`}>
          {displayValue}
        </span>
      )}
      <span className="absolute right-1 top-1 opacity-40 transition-opacity group-hover/edit:opacity-100">
        <Edit3 className="h-3 w-3 text-brand" />
      </span>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Action-item view-model
// ────────────────────────────────────────────────────────────────────────────

interface ActionItemVM {
  text: string;
  owner_email?: string;
  owner?: string;
  urgency?: string;
  context?: string;
  done?: boolean;
  /** Source bucket — matches MemoirResponse.action_items[].type. */
  bucket: "todo" | "commitment";
}

/** ActionItemVM ↔ ActionItem (P10 schema) — converted on save.
 *
 * Sends every field we know about so checkbox state (`completed`),
 * owner, urgency, and context all survive a page reload. Backend
 * accepts list[dict[str, Any]] for action_items, so unknown keys are
 * preserved untouched.
 */
function vmToMemoir(items: ActionItemVM[]): Array<{
  text: string;
  type: "rep" | "prospect";
  completed?: boolean;
  owner?: string;
  owner_email?: string;
  urgency?: string;
  context?: string | null;
}> {
  return items
    .filter((i) => i.text.trim())
    .map((i) => ({
      text: i.text,
      type: i.bucket === "todo" ? "rep" : "prospect",
      completed: !!i.done,
      ...(i.owner ? { owner: i.owner } : {}),
      ...(i.owner_email ? { owner_email: i.owner_email } : {}),
      ...(i.urgency ? { urgency: i.urgency } : {}),
      ...(i.context ? { context: i.context } : {}),
    }));
}

// ────────────────────────────────────────────────────────────────────────────
// Main component
// ────────────────────────────────────────────────────────────────────────────

type TabKey = "summary" | "transcript" | "crm";

interface P5Summary {
  tldr?: string;
  decisions?: Array<{ title?: string; detail?: string }>;
  open_questions?: Array<{ text?: string }>;
  action_items?: Array<{
    text?: string;
    owner?: string;
    owner_email?: string;
    urgency?: string;
    context?: string | null;
    blocking?: string | null;
  }>;
  discussion_threads?: Array<{
    title?: string;
    priority?: string;
    summary?: string;
  }>;
}

export function MeetingDetailV3() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id ?? "";

  // ── State ──────────────────────────────────────────────────────────
  const [meeting, setMeeting] = useState<MeetingDetail | null>(null);
  const [summary, setSummary] = useState<P5Summary | null>(null);
  const [rawMemoir, setRawMemoir] = useState<MemoirResponse | null>(null);
  // True once `api.getMemoir(id)` has resolved (success or failure).
  // Used to gate fallback-to-P5 seeding so the slower memoir response
  // always wins when it has data.
  const [memoirResolved, setMemoirResolved] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);
  const [coaching, setCoaching] = useState<CoachingResponse | null>(null);
  const [callIntel, setCallIntel] = useState<CallIntelligenceResponse | null>(null);
  // Shared /processing-status payload — fed to <PipelineProgressStrip> so
  // the strip doesn't fire its own duplicate poll every 5 s.
  const [pipelinePhases, setPipelinePhases] = useState<{
    active_stage?: string;
    stages?: Record<string, { percent: number; label: string }>;
    transcription?: string;
    summary?: string;
    intelligence?: string;
    crm_extraction?: string;
  } | null>(null);
  const [speakers, setSpeakers] = useState<MeetingParticipantInfo[]>([]);
  // Open speaker-merge dialog with this source profile pre-selected. When
  // not null, <SpeakerMergeDialog> is mounted at the page root.
  const [mergingProfile, setMergingProfile] = useState<{ id: string; name: string } | null>(null);
  const [internalEmails, setInternalEmails] = useState<Set<string>>(new Set());
  const [internalNames, setInternalNames] = useState<Set<string>>(new Set());
  const [nextSteps, setNextSteps] = useState<{ milestone: string; next_meeting_date: string }>({
    milestone: "",
    next_meeting_date: "",
  });
  // Editable signals draft (P10). Both arrays mutate independently;
  // any change is autosaved through editMemoir({signals}).
  const [buyingSignals, setBuyingSignals] = useState<string[]>([]);
  const [riskSignals, setRiskSignals] = useState<string[]>([]);

  // CRM tab state — mirrors v1 detail page.
  const [crmConnections, setCrmConnections] = useState<CrmConnection[]>([]);
  const [crmPushResults, setCrmPushResults] = useState<CrmPushResult[]>([]);
  const [activeCrmProvider, setActiveCrmProvider] = useState<string>("");
  const [crmAction, setCrmAction] = useState<"pushing" | "skipping" | null>(null);
  const [regeneratingCrm, setRegeneratingCrm] = useState(false);
  const [crmExtractError, setCrmExtractError] = useState<string | null>(null);
  const [editingCrmField, setEditingCrmField] = useState<string | null>(null);
  const [editingCrmValue, setEditingCrmValue] = useState("");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Resolve memoir speaker labels lazily — same util v1 uses.
  const memoir = useMemo(
    () => resolveMemoir(rawMemoir, transcript?.speaker_map) ?? rawMemoir,
    [rawMemoir, transcript?.speaker_map],
  );

  // Tabs
  const [activeTab, setActiveTab] = useState<TabKey>("summary");

  // Audio
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [audioSrc, setAudioSrc] = useState<string | null>(null);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [audioTime, setAudioTime] = useState(0);
  const [audioDuration, setAudioDuration] = useState(0);

  // Find & Replace
  const [showFindReplace, setShowFindReplace] = useState(false);
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [replaceScope, setReplaceScope] = useState<"all" | "summary" | "transcript" | "actions">("all");
  const [replaceCount, setReplaceCount] = useState<number | null>(null);

  // Share dropdown
  const [shareOpen, setShareOpen] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const [generatingLink, setGeneratingLink] = useState(false);
  const [pdfDownloading, setPdfDownloading] = useState(false);
  const [audioDownloading, setAudioDownloading] = useState(false);
  const shareRef = useRef<HTMLDivElement>(null);

  // Send Email dialog — opens in-page (not via v1 redirect)
  const [emailDialogOpen, setEmailDialogOpen] = useState(false);
  const [emailTo, setEmailTo] = useState("");
  const [emailTemplate, setEmailTemplate] = useState<"recorder" | "internal" | "external">("external");
  const [emailPreviewHtml, setEmailPreviewHtml] = useState("");
  const [emailPreviewSubject, setEmailPreviewSubject] = useState("");
  const [emailSending, setEmailSending] = useState(false);
  const [emailSent, setEmailSent] = useState(false);
  const [emailLog, setEmailLog] = useState<
    Array<{ id: string; to_email: string; status: string; created_at: string }>
  >([]);

  // Persisted edits to summary fields (separate from the read-only AI output).
  // Stored locally in component state; written through API on save.
  const [summaryDraft, setSummaryDraft] = useState("");
  const [todosDraft, setTodosDraft] = useState<ActionItemVM[]>([]);
  const [commitmentsDraft, setCommitmentsDraft] = useState<ActionItemVM[]>([]);

  // ── Initial load ──────────────────────────────────────────────────
  useEffect(() => {
    if (!id) return;
    let cancelled = false;

    (async () => {
      try {
        const m = await api.getMeeting(id);
        if (cancelled) return;
        setMeeting(m);

        // Parallel non-blocking fetches
        api.getMeetingSummary(id)
          .then((s) => !cancelled && setSummary((s ?? {}) as P5Summary))
          .catch(() => {});
        api.getTranscript(id).then((t) => !cancelled && setTranscript(t)).catch(() => {});
        api.getMemoir(id)
          .then((mem) => {
            if (cancelled) return;
            setRawMemoir(mem);
            setMemoirResolved(true);
          })
          .catch(() => !cancelled && setMemoirResolved(true));
        api.getCoaching(id).then((c) => !cancelled && setCoaching(c)).catch(() => {});
        api.getCallIntelligence(id).then((ci) => !cancelled && setCallIntel(ci)).catch(() => {});
        api.getMeetingSpeakers(id)
          .then((resp) => !cancelled && setSpeakers(resp.participants))
          .catch(() => {});
        api.getCrmConnections()
          .then((c) => !cancelled && setCrmConnections(c))
          .catch(() => {});
        api.getCrmPushResults(id)
          .then((r) => !cancelled && setCrmPushResults(r))
          .catch(() => {});
        api.getMeetingNextSteps(id)
          .then((ns) =>
            !cancelled && setNextSteps({
              milestone: ns.milestone || "",
              next_meeting_date: ns.next_meeting_date || "",
            }),
          )
          .catch(() => {});

        // Audio (lazy-load presigned URL; preprocessed first then raw)
        if (m.has_recording) {
          api.getAudioUrls(id)
            .then((urls) => {
              if (cancelled) return;
              const pref = urls.preprocessed || urls.raw || Object.values(urls)[0];
              if (pref) setAudioSrc(pref);
            })
            .catch(() => {
              api.getRecordingPresignedUrl(id)
                .then((u) => !cancelled && setAudioSrc(u))
                .catch(() => {});
            });
        }

        // Team emails for the To-Do vs Commitment split
        if (m.team_id) {
          // Reuse speaker profiles + meeting created_by as a cheap heuristic —
          // any participant whose email matches the host org likely internal.
          // We don't have a clean "team emails" endpoint, so we use speakers
          // already loaded above + the meeting's created_by_name as fallback.
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load meeting");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [id]);

  // Pipeline polling — while the meeting is still processing, re-fetch
  // meeting + all artifacts every 5s so each stage's output (fast P5
  // summary → polished transcript → intelligence → CRM) shows up the
  // moment it's available. Earlier behavior only refetched artifacts on
  // terminal-status flip, so the AI Summary / Transcript / Coaching
  // panels stayed blank until the user manually reloaded the page even
  // though the data was in R2 already. Stops only after status reaches
  // terminal AND every backend phase reports completed/skipped/failed.
  useEffect(() => {
    if (!id) return;
    // Only skip polling when the WHOLE pipeline is shut down (failed,
    // fast-email-only path that explicitly skipped T3/T2). `completed`
    // is NOT in this set — meeting.status flips to 'completed' after
    // T5 but T3 (intelligence) and T2 (CRM) keep running. Skipping
    // polling here would freeze the progress strip + memoir refetch
    // mid-pipeline. The polling has its own all-phases-terminal exit
    // check below that handles the actual end-of-pipeline cleanly.
    const skipStatuses = new Set(["fast_email_only", "failed", "transcription_failed"]);
    if (!meeting?.status || skipStatuses.has(meeting.status)) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | null = null;
    const phasesTerminal = new Set(["completed", "skipped", "failed"]);
    // Stuck-pipeline guard: if /processing-status returns the same
    // snapshot for STUCK_THRESHOLD_MS without progress, exit polling.
    // Without this the loop runs forever on meetings whose pipeline
    // dropped a webhook (e.g. orchestrator gave up after MAX_RETRIES
    // and meeting.status stayed at "processing"). 10 min is well past
    // the longest legitimate inter-phase gap (T5 → T3 transition) so
    // a stuck signal is much more likely than slow progress.
    const STUCK_THRESHOLD_MS = 10 * 60 * 1000;
    let lastPhasesSnapshot = "";
    let lastPhasesChangedAt = Date.now();
    // Each artifact is fetched on every tick UNTIL it lands with content.
    // Once we have it, the corresponding flag flips and we stop refetching.
    // This handles the T7→T5 window for /summary (fast P5 lands well
    // before transcription="completed") and also kills the /speakers
    // spam (once we have a non-empty speakers list we stop).
    let haveSummary = false;
    let haveSpeakers = false;
    let haveTranscript = false;
    let haveMemoir = false;
    let haveCoaching = false;
    let haveCallIntel = false;
    const tick = async () => {
      try {
        // Drop the bare /meetings/{id} fetch from the tick — its useful
        // field (status) is mirrored by /processing-status which we read
        // below for the gating logic. Halves the request count per tick.
        let phases: {
          active_stage?: string;
          stages?: Record<string, { percent: number; label: string }>;
          transcription?: string;
          summary?: string;
          intelligence?: string;
          crm_extraction?: string;
        } = {};
        try {
          phases = (await api.getProcessingStatus(id)) as typeof phases;
        } catch {
          /* phases stays empty, no artifact fetches this tick */
        }
        if (cancelled) return;
        // Hand the payload to <PipelineProgressStrip> so it doesn't have
        // to fire its own /processing-status fetch in parallel.
        setPipelinePhases(phases);
        const trans = phases.transcription ?? "";
        const intel = phases.intelligence ?? "";
        const crm = phases.crm_extraction ?? "";

        // Stuck-pipeline check (see STUCK_THRESHOLD_MS comment above).
        // Hash the phase tuple — if it changes, reset the timer; if
        // it's the same for too long, assume the meeting is stuck and
        // exit the polling loop. Refresh the meeting once on the way
        // out so the user-visible status can flip to whatever the
        // server eventually sets.
        const snapshot = `${trans}|${intel}|${crm}|${phases.active_stage ?? ""}`;
        if (snapshot !== lastPhasesSnapshot) {
          lastPhasesSnapshot = snapshot;
          lastPhasesChangedAt = Date.now();
        } else if (Date.now() - lastPhasesChangedAt > STUCK_THRESHOLD_MS) {
          api.getMeeting(id).then((m) => !cancelled && setMeeting(m)).catch(() => {});
          if (interval) clearInterval(interval);
          interval = null;
          return;
        }

        // Pipeline-bail short-circuit: when the meeting transitions to a
        // skip status server-side (failed / fast_email_only) the outer
        // useEffect won't re-fire because we never refetch /meetings/{id}
        // unless ALL three phases are terminal — and intel/crm stay at
        // "pending" forever when the pipeline never ran. /processing-
        // status mirrors meeting.status into the transcription field, so
        // we can detect the bail here. Refetch the meeting (so the UI
        // header flips), kill the interval, exit.
        if (skipStatuses.has(trans)) {
          api.getMeeting(id).then((m) => !cancelled && setMeeting(m)).catch(() => {});
          if (interval) clearInterval(interval);
          interval = null;
          return;
        }

        // Strict-mode gating: fire each artifact endpoint exactly ONCE
        // when its stage transitions to terminal. No every-tick polling
        // until content lands. Detection lag is bounded by the 5 s
        // polling interval (i.e., worst case 5 s after the stage
        // actually finishes, the next tick picks it up).
        //
        // /summary lands when fast_summary (T7) writes summary_fast.json
        // to R2. Signal: stages.fast_summary.percent === 100.
        // /speakers row is written at T5 (transcription completes).
        // Signal: trans === "completed".
        const fastSummaryDone =
          (phases.stages?.fast_summary?.percent ?? 0) >= 100;
        if (!haveSummary && fastSummaryDone) {
          api.getMeetingSummary(id).then((s) => {
            if (cancelled) return;
            const sum = (s ?? {}) as P5Summary;
            setSummary(sum);
            haveSummary = true;
          }).catch(() => {});
        }
        if (!haveSpeakers && trans === "completed") {
          api.getMeetingSpeakers(id).then((r) => {
            if (cancelled) return;
            setSpeakers(r.participants);
            haveSpeakers = true;
          }).catch(() => {});
        }
        // Transcript + memoir/coaching/call-intel are heavier — gate on
        // their phase transitions (T5 done / T3 done) to avoid 404 spam.
        if (!haveTranscript && trans === "completed") {
          api.getTranscript(id).then((t) => {
            if (cancelled) return;
            setTranscript(t);
            haveTranscript = true;
          }).catch(() => {});
        }
        if (!haveMemoir && intel === "completed") {
          api.getMemoir(id).then((mem) => {
            if (cancelled) return;
            setRawMemoir(mem);
            setMemoirResolved(true);
            haveMemoir = true;
          }).catch(() => {});
        }
        if (!haveCoaching && intel === "completed") {
          api.getCoaching(id).then((c) => { if (!cancelled) { setCoaching(c); haveCoaching = true; } }).catch(() => {});
        }
        if (!haveCallIntel && intel === "completed") {
          api.getCallIntelligence(id).then((ci) => { if (!cancelled) { setCallIntel(ci); haveCallIntel = true; } }).catch(() => {});
        }

        // Stop polling once every backend phase reports a terminal state.
        // Without this, polling continued forever for meetings whose
        // meeting.status never flipped (intelligence/crm done but status
        // stuck at waiting_for_transcription, etc.).
        if (
          phasesTerminal.has(trans) &&
          phasesTerminal.has(intel) &&
          phasesTerminal.has(crm)
        ) {
          // Final meeting refresh so header badges flip from "processing"
          // to the terminal status the moment the pipeline completes.
          api.getMeeting(id).then((m) => !cancelled && setMeeting(m)).catch(() => {});
          if (interval) clearInterval(interval);
          interval = null;
        }
      } catch {
        /* transient errors are fine — try again next tick */
      }
    };
    interval = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [id, meeting?.status]);

  // Internal email heuristic: anyone whose email shares the meeting creator's
  // email domain. Falls back to "owner_email matches a participant marked
  // is_internal in callIntel.summary.meeting.participants" when available.
  useEffect(() => {
    const emailSet = new Set<string>();
    const nameSet = new Set<string>();
    // P5 already labels participants as internal/external
    const meta = (callIntel?.summary as Record<string, unknown> | undefined)?.["meeting"] as
      | { participants?: Array<{ email?: string; name?: string; is_internal?: boolean }> }
      | undefined;
    if (meta?.participants) {
      for (const p of meta.participants) {
        if (p.is_internal) {
          if (p.email) emailSet.add(p.email.toLowerCase());
          if (p.name) nameSet.add(p.name.toLowerCase());
        }
      }
    }
    // P10 memoir's call_metadata also has participants with role
    if (memoir?.call_metadata?.participants) {
      for (const p of memoir.call_metadata.participants) {
        // Treat anyone whose role is rep / sales / internal / host as internal
        const role = (p.role || "").toLowerCase();
        const isInternal = role === "rep" || role === "sales" || role === "internal" || role === "host";
        if (isInternal && p.name) nameSet.add(p.name.toLowerCase());
      }
    }
    setInternalEmails(emailSet);
    setInternalNames(nameSet);
  }, [callIntel, memoir]);

  // ── Seed editable drafts whenever AI data arrives ─────────────────
  // Summary text: prefer P10 narrative_summary (richer + edit-aware via
  // memoir.is_edited), fall back to P5 tldr.
  useEffect(() => {
    if (summaryDraft) return;
    // Only seed from P10. If memoir has no narrative_summary yet we
    // intentionally leave summaryDraft empty so the P10 card shows its
    // placeholder rather than silently borrowing P5 prose (which would
    // be written back to memoir on save, clobbering the two sources).
    const text = memoir?.narrative_summary || "";
    if (text) setSummaryDraft(text);
  }, [memoir, summaryDraft]);

  // P10 action items — editable, persisted via editMemoir.
  // The P5 action items are surfaced separately (read-only) in their
  // own card so the two sources stay un-mixed.
  useEffect(() => {
    if (!memoirResolved) return;
    const alreadySeeded = todosDraft.length > 0 || commitmentsDraft.length > 0;
    if (alreadySeeded) return;
    if (!memoir?.action_items?.length) return;

    const decideBucket = (a: {
      type?: string;
      owner?: string;
      owner_email?: string;
    }): "todo" | "commitment" => {
      if (a.type === "rep") return "todo";
      if (a.type === "prospect") return "commitment";
      const emailLower = (a.owner_email || "").toLowerCase();
      if (emailLower && internalEmails.has(emailLower)) return "todo";
      const ownerLower = (a.owner || "").toLowerCase().trim();
      if (ownerLower && internalNames.has(ownerLower)) return "todo";
      return "commitment";
    };

    const todos: ActionItemVM[] = [];
    const commitments: ActionItemVM[] = [];
    for (const a of memoir.action_items) {
      if (!a?.text) continue;
      const bucket = decideBucket(a);
      const vm: ActionItemVM = {
        text: a.text,
        owner: a.owner,
        owner_email: a.owner_email,
        urgency: a.urgency,
        context: a.context,
        done: a.completed,
        bucket,
      };
      if (bucket === "todo") todos.push(vm);
      else commitments.push(vm);
    }
    setTodosDraft(todos);
    setCommitmentsDraft(commitments);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memoirResolved, memoir, internalEmails, internalNames]);

  // Signals seeding from P10
  useEffect(() => {
    if (buyingSignals.length > 0 || riskSignals.length > 0) return;
    if (memoir?.signals) {
      setBuyingSignals(memoir.signals.buying || []);
      setRiskSignals(memoir.signals.risk || []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memoir]);

  // Next steps seeding from P10 (falls back to next_steps_data already
  // loaded by getMeetingNextSteps if memoir hasn't arrived).
  useEffect(() => {
    if (!memoir?.next_steps) return;
    const ns = memoir.next_steps;
    setNextSteps((prev) => ({
      milestone: prev.milestone || ns.next_milestone || "",
      next_meeting_date: prev.next_meeting_date || ns.next_meeting || "",
    }));
  }, [memoir]);

  // ── Audio playback ────────────────────────────────────────────────
  useEffect(() => {
    const el = audioRef.current;
    if (!el || !audioSrc) return;
    el.src = audioSrc;
    el.load();
  }, [audioSrc]);

  const togglePlay = () => {
    const el = audioRef.current;
    if (!el || !audioSrc) return;
    if (audioPlaying) el.pause();
    else el.play().catch(() => {});
  };

  // ── Save handlers ─────────────────────────────────────────────────
  // P10 (memoir) writes flow through PATCH /api/meetings/{id}/memoir.
  // editMemoir is full-document replace on the listed keys, so we always
  // send the FULL current state for the keys we touch.

  // Flip the local memoir.is_edited flag so the reset bar appears
  // immediately after any P10 edit — without waiting for a refetch.
  const markMemoirEdited = () => {
    setRawMemoir((prev) => (prev ? { ...prev, is_edited: true } : prev));
  };

  const saveSummaryDraft = async (next: string) => {
    setSummaryDraft(next);
    try {
      await api.editMemoir(id, { narrative_summary: next });
      // Surface the Reset bar immediately rather than waiting for the
      // next page load to refetch memoir.is_edited.
      markMemoirEdited();
    } catch {
      // ignore; client state still reflects the edit
    }
  };

  // ── Reset handlers ───────────────────────────────────────────────
  // Each reset restores the corresponding section from its original
  // pipeline output. Confirms first because the action is destructive.
  const [resettingMemoir, setResettingMemoir] = useState(false);
  const [resettingTranscript, setResettingTranscript] = useState(false);

  const handleResetMemoir = async () => {
    if (!window.confirm("Reset all P10 edits (Summary, Action Items, Signals, Next Steps) to the original AI output? This cannot be undone.")) return;
    setResettingMemoir(true);
    try {
      await api.resetMemoir(id);
      // Refetch memoir + reset all local drafts so the seed effects run
      // fresh against the restored data.
      const fresh = await api.getMemoir(id).catch(() => null);
      setRawMemoir(fresh);
      setSummaryDraft("");
      setTodosDraft([]);
      setCommitmentsDraft([]);
      setBuyingSignals([]);
      setRiskSignals([]);
      // Pull restored next-steps too so milestone+date refresh.
      try {
        const ns = await api.getMeetingNextSteps(id);
        setNextSteps({
          milestone: ns.milestone || "",
          next_meeting_date: ns.next_meeting_date || "",
        });
      } catch {
        setNextSteps({ milestone: "", next_meeting_date: "" });
      }
    } catch (e) {
      window.alert(
        e instanceof Error ? e.message : "Reset failed — no original backup exists yet, or the request errored.",
      );
    } finally {
      setResettingMemoir(false);
    }
  };

  const handleResetTranscript = async () => {
    if (!window.confirm("Reset the transcript to the original AI output? Your edits will be lost.")) return;
    setResettingTranscript(true);
    try {
      await api.resetTranscript(id);
      const fresh = await api.getTranscript(id);
      setTranscript(fresh);
    } catch (e) {
      window.alert(
        e instanceof Error ? e.message : "Reset failed — no original backup exists yet, or the request errored.",
      );
    } finally {
      setResettingTranscript(false);
    }
  };

  const saveTranscriptSegment = async (segIdx: number, next: string) => {
    // Functional update — avoids stale-closure when several edits land
    // in quick succession (each handler captures the transcript snapshot
    // at the time the click fires; without the functional form, the
    // second edit overwrites the first).
    setTranscript((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        segments: prev.segments.map((s, i) => (i === segIdx ? { ...s, text: next } : s)),
      };
    });
    try {
      await api.editTranscriptSegment(id, segIdx, next);
      // Flip is_edited so the Reset bar shows up immediately on save,
      // without waiting for the user to reload the page.
      setTranscript((prev) => (prev ? { ...prev, is_edited: true } : prev));
    } catch {
      // Surface failure quietly — the visible edit stays; user can retry
      // by re-editing. Avoids data-loss + noisy toasts on transient errors.
    }
  };

  // Action items — keep both ↔ PATCH memoir on every change.
  // To avoid stale closures, accept the *next* arrays and rebuild the
  // memoir payload from them rather than reading state inside the call.
  const persistActionItems = useCallback(
    async (todos: ActionItemVM[], commitments: ActionItemVM[]) => {
      try {
        await api.editMemoir(id, {
          action_items: [...vmToMemoir(todos), ...vmToMemoir(commitments)],
        });
        setRawMemoir((prev) => (prev ? { ...prev, is_edited: true } : prev));
      } catch {
        // ignore
      }
    },
    [id],
  );

  // Signals — PATCH on every change. Same full-replace semantics.
  const persistSignals = useCallback(
    async (buying: string[], risk: string[]) => {
      try {
        await api.editMemoir(id, {
          signals: { buying: buying.filter(Boolean), risk: risk.filter(Boolean) },
        });
        setRawMemoir((prev) => (prev ? { ...prev, is_edited: true } : prev));
      } catch {
        // ignore
      }
    },
    [id],
  );

  const saveNextStepMilestone = async (next: string) => {
    setNextSteps((s) => ({ ...s, milestone: next }));
    try {
      await Promise.allSettled([
        api.saveMeetingNextSteps(id, { milestone: next }),
        api.editMemoir(id, {
          next_steps: {
            next_milestone: next || null,
            next_meeting: nextSteps.next_meeting_date || null,
            suggested_deal_stage: memoir?.next_steps?.suggested_deal_stage || null,
          },
        }),
      ]);
      markMemoirEdited();
    } catch {
      // ignore
    }
  };

  const saveNextStepDate = async (next: string) => {
    setNextSteps((s) => ({ ...s, next_meeting_date: next }));
    try {
      await Promise.allSettled([
        api.saveMeetingNextSteps(id, { next_meeting_date: next }),
        api.editMemoir(id, {
          next_steps: {
            next_milestone: nextSteps.milestone || null,
            next_meeting: next || null,
            suggested_deal_stage: memoir?.next_steps?.suggested_deal_stage || null,
          },
        }),
      ]);
      markMemoirEdited();
    } catch {
      // ignore
    }
  };

  // ── CRM handlers (parity with v1) ─────────────────────────────────
  const handlePushCrm = async (provider: string) => {
    if (!provider) return;
    setCrmAction("pushing");
    try {
      const result = await api.pushToCrm(id, provider);
      setCrmPushResults((prev) => [
        ...prev.filter((r) => r.provider !== provider),
        result,
      ]);
      setMeeting((prev) => (prev ? { ...prev, crm_push_status: "pushed" } : prev));
    } catch {
      // silent
    } finally {
      setCrmAction(null);
    }
  };

  const handleSkipCrm = async (provider: string) => {
    if (!provider) return;
    setCrmAction("skipping");
    try {
      await api.skipCrmPush(id, provider);
      setCrmPushResults((prev) => [
        ...prev.filter((r) => r.provider !== provider),
        { provider, status: "skipped" } as CrmPushResult,
      ]);
      setMeeting((prev) => (prev ? { ...prev, crm_push_status: "skipped" } : prev));
    } catch {
      // silent
    } finally {
      setCrmAction(null);
    }
  };

  const handleEditCrmField = async (provider: string, fieldKey: string, value: string) => {
    try {
      const existing = crmPushResults.find((r) => r.provider === provider);
      const base = existing?.edited_data || existing?.pushed_data || {};
      const data = JSON.parse(JSON.stringify(base)) as Record<string, unknown>;
      const parts = fieldKey.split(".");
      if (parts.length === 2) {
        const [pk, ck] = parts;
        const parent = data[pk];
        if (parent && typeof parent === "object" && !Array.isArray(parent)) {
          data[pk] = { ...(parent as Record<string, unknown>), [ck]: value };
        } else {
          data[pk] = { [ck]: value };
        }
      } else {
        data[fieldKey] = value;
      }
      await api.editCrmPushData(id, provider, data);
      setCrmPushResults((prev) =>
        prev.map((r) => (r.provider === provider ? { ...r, edited_data: data } : r)),
      );
      setEditingCrmField(null);
    } catch {
      // silent
    }
  };

  const handleRegenerateCrm = async () => {
    setRegeneratingCrm(true);
    setCrmExtractError(null);
    try {
      await api.regenerateCrmExtraction(id);
      // Poll once after 2s for an updated extraction result.
      setTimeout(async () => {
        try {
          const fresh = await api.getCallIntelligence(id);
          setCallIntel(fresh);
          const r = await api.getCrmPushResults(id);
          setCrmPushResults(r);
        } catch {
          // silent
        } finally {
          setRegeneratingCrm(false);
        }
      }, 2000);
    } catch (e) {
      setCrmExtractError(e instanceof Error ? e.message : "Failed to regenerate CRM data");
      setRegeneratingCrm(false);
    }
  };

  // ── Find & Replace ────────────────────────────────────────────────
  const handleReplaceAll = async () => {
    if (!findText) return;
    const rx = new RegExp(escRegex(findText), "gi");
    let count = 0;
    const wantSummary = replaceScope === "all" || replaceScope === "summary";
    const wantTranscript = replaceScope === "all" || replaceScope === "transcript";
    const wantActions = replaceScope === "all" || replaceScope === "actions";

    // Build new values up-front from current state, then apply locally
    // AND persist. This keeps the React updates batched and lets us
    // commit each surface through its own backend endpoint.

    // ── Summary (P10 narrative_summary) ──
    let newSummary: string | null = null;
    if (wantSummary && summaryDraft) {
      const matches = (summaryDraft.match(rx) || []).length;
      if (matches > 0) {
        count += matches;
        newSummary = summaryDraft.replace(rx, replaceText);
        setSummaryDraft(newSummary);
      }
    }

    // ── Transcript ── compute from a stable snapshot, THEN call
    // setState. The earlier version pushed touched segments from inside
    // a setState updater, which doesn't run synchronously in React 18 —
    // so by the time the for-loop below tried to fire the PATCHes,
    // `touched` was still empty and the backend never received the
    // F&R changes. The UI re-rendered correctly (giving the illusion
    // of a successful F&R) but a subsequent page reload exposed the
    // unmodified backend file.
    const touchedTranscriptSegments: Array<{ idx: number; text: string }> = [];
    if (wantTranscript && transcript) {
      let anyMatch = false;
      const next: TranscriptSegment[] = [];
      for (let idx = 0; idx < transcript.segments.length; idx++) {
        const s = transcript.segments[idx];
        const matches = (s.text.match(rx) || []).length;
        if (matches > 0) {
          count += matches;
          anyMatch = true;
          const newText = s.text.replace(rx, replaceText);
          touchedTranscriptSegments.push({ idx, text: newText });
          next.push({ ...s, text: newText });
        } else {
          next.push(s);
        }
      }
      if (anyMatch) {
        setTranscript({
          ...transcript,
          is_edited: transcript.is_edited || anyMatch,
          segments: next,
        });
      }
    }

    // ── Action items (P10) ── compute outside setState so we can
    // PATCH the full list once instead of per-item.
    let nextTodos: ActionItemVM[] | null = null;
    let nextCommitments: ActionItemVM[] | null = null;
    if (wantActions) {
      let touched = false;
      nextTodos = todosDraft.map((t) => {
        const matches = (t.text.match(rx) || []).length;
        if (matches > 0) {
          touched = true;
          count += matches;
          return { ...t, text: t.text.replace(rx, replaceText) };
        }
        return t;
      });
      nextCommitments = commitmentsDraft.map((c) => {
        const matches = (c.text.match(rx) || []).length;
        if (matches > 0) {
          touched = true;
          count += matches;
          return { ...c, text: c.text.replace(rx, replaceText) };
        }
        return c;
      });
      if (touched) {
        setTodosDraft(nextTodos);
        setCommitmentsDraft(nextCommitments);
      } else {
        // No matches — skip backend write.
        nextTodos = null;
        nextCommitments = null;
      }
    }

    setReplaceCount(count);
    setTimeout(() => setReplaceCount(null), 3000);

    // ── Backend writes (parallel, fire-and-forget) ──
    // Memoir writes (summary + action_items) can fire in parallel —
    // they hit a single endpoint each. Transcript writes MUST be
    // serialized: the backend handler does read-modify-write on the
    // whole segments JSON file, so two concurrent PATCHes will lose
    // each other's updates.
    const memoirWrites: Promise<unknown>[] = [];
    if (newSummary != null) {
      memoirWrites.push(
        api
          .editMemoir(id, { narrative_summary: newSummary })
          .then(() => markMemoirEdited())
          .catch(() => {}),
      );
    }
    if (nextTodos && nextCommitments) {
      memoirWrites.push(
        api
          .editMemoir(id, {
            action_items: [...vmToMemoir(nextTodos), ...vmToMemoir(nextCommitments)],
          })
          .then(() => markMemoirEdited())
          .catch(() => {}),
      );
    }

    // Sequentially commit transcript edits so each call reads the
    // result of the previous one. Without this, parallel writes
    // overlap on the same JSON file and most F&R changes vanish.
    for (const { idx, text } of touchedTranscriptSegments) {
      try {
        await api.editTranscriptSegment(id, idx, text);
      } catch {
        // continue with the remaining segments; we don't want one
        // transient failure to drop every later edit
      }
    }

    await Promise.allSettled(memoirWrites);
  };

  // ── Share dropdown options ─────────────────────────────────────────
  // Close on outside click + Esc — same pattern as v2 detail.
  useEffect(() => {
    if (!shareOpen) return;
    const onMouseDown = (e: MouseEvent) => {
      if (shareRef.current && !shareRef.current.contains(e.target as Node)) {
        setShareOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShareOpen(false);
    };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [shareOpen]);

  const handleCopyLink = async () => {
    setGeneratingLink(true);
    try {
      const result = await api.createShareLink(id);
      const url = `${window.location.origin}/shared/${result.share_token}`;
      await navigator.clipboard.writeText(url);
      setShareCopied(true);
      setTimeout(() => setShareCopied(false), 2000);
    } catch {
      try {
        await navigator.clipboard.writeText(`${window.location.origin}/meetings/${id}`);
        setShareCopied(true);
        setTimeout(() => setShareCopied(false), 2000);
      } catch {
        // clipboard unavailable
      }
    } finally {
      setGeneratingLink(false);
    }
  };

  const handleDownloadAudio = async () => {
    setShareOpen(false);
    setAudioDownloading(true);
    try {
      // The backend signs a URL with ResponseContentDisposition=attachment;
      // hitting the URL triggers a browser download rather than inline
      // playback. Using <a download href> lets the click happen in the
      // current page without losing scroll position.
      const { url, filename } = await api.getRecordingDownloadUrl(id);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch {
      // Fallback to the inline presigned URL if the new endpoint is
      // unavailable for some reason (no recording on disk, 404, etc.)
      if (audioSrc) window.open(audioSrc, "_blank");
    } finally {
      setAudioDownloading(false);
    }
  };

  const openEmailDialog = async () => {
    setShareOpen(false);
    setEmailDialogOpen(true);
    setEmailSent(false);
    setEmailTo("");
    setEmailTemplate("external");
    setEmailPreviewHtml("");
    setEmailPreviewSubject("");
    // Pre-fetch the external preview + log alongside
    try {
      const [preview, log] = await Promise.all([
        api.getEmailPreview(id, "external"),
        api.getEmailLog(id).catch(() => [] as Array<{ id: string; to_email: string; status: string; created_at: string }>),
      ]);
      setEmailPreviewHtml(preview.html_body);
      setEmailPreviewSubject(preview.subject);
      if (Array.isArray(log)) setEmailLog(log);
    } catch {
      // ignore — preview area will show "Loading…" until user picks template
    }
  };

  const handleSelectEmailTemplate = async (tmpl: "recorder" | "internal" | "external") => {
    setEmailTemplate(tmpl);
    setEmailPreviewHtml("");
    setEmailPreviewSubject("");
    try {
      const preview = await api.getEmailPreview(id, tmpl);
      setEmailPreviewHtml(preview.html_body);
      setEmailPreviewSubject(preview.subject);
    } catch {
      // surface as empty preview
    }
  };

  const handleSendEmail = async () => {
    if (!emailTo || !emailTo.includes("@")) return;
    setEmailSending(true);
    try {
      await api.sendMeetingEmail(id, emailTo, emailTemplate);
      setEmailSent(true);
      // Auto-close after a moment so the user sees the confirmation
      setTimeout(() => setEmailDialogOpen(false), 1200);
    } catch {
      window.alert("Failed to send email. Check the recipient address and try again.");
    } finally {
      setEmailSending(false);
    }
  };

  const handleExportCsv = () => {
    if (!transcript || !meeting) return;
    const rows: string[][] = [["Timestamp", "Speaker", "Text"]];
    for (const seg of transcript.segments) {
      const time = fmtClock(seg.original_start ?? seg.start);
      const rawSpeaker = seg.speaker || "";
      const name =
        (transcript.speaker_map && transcript.speaker_map[rawSpeaker]?.name) ||
        rawSpeaker ||
        "Speaker";
      const text = seg.text.replace(/"/g, '""');
      rows.push([time, name, `"${text}"`]);
    }
    const csv = rows.map((r) => r.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(meeting.title || "transcript").replace(/[^a-zA-Z0-9]/g, "_")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    setShareOpen(false);
  };

  const handleDownloadPdf = async () => {
    if (!meeting) return;
    setPdfDownloading(true);
    try {
      // html2pdf.js is ~180KB; only load it when the user actually exports.
      const html2pdf = (await import("html2pdf.js")).default;

      const dateStr = fmtDate(meeting.started_at || meeting.created_at);
      const durStr = meeting.duration_seconds != null ? fmtDuration(meeting.duration_seconds) : "";
      let html = `<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:710px;margin:0 auto;color:#27272a;font-size:13px;line-height:1.6">`;
      html += `<h1 style="font-size:20px;margin-bottom:4px">${meeting.title || "Untitled Meeting"}</h1>`;
      html += `<div style="color:#71717a;font-size:12px;margin-bottom:24px">${dateStr}${durStr ? ` · ${durStr}` : ""}</div>`;

      // Summary
      if (summaryDraft) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Summary</h2>`;
        summaryDraft.split("\n\n").forEach((p) => {
          html += `<p>${p}</p>`;
        });
      }

      // Next steps
      if (nextSteps.milestone || nextSteps.next_meeting_date) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Next Steps</h2>`;
        if (nextSteps.milestone) html += `<div><strong>Milestone:</strong> ${nextSteps.milestone}</div>`;
        if (nextSteps.next_meeting_date) html += `<div><strong>Next meeting:</strong> ${nextSteps.next_meeting_date}</div>`;
      }

      // Action items
      if (todosDraft.length > 0 || commitmentsDraft.length > 0) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Action Items</h2>`;
        if (todosDraft.length > 0) {
          html += `<div style="font-size:11px;color:#5C5CE0;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Your To-Dos</div>`;
          for (const t of todosDraft) {
            html += `<div style="margin-bottom:4px;padding-left:16px;position:relative"><span style="position:absolute;left:0">${t.done ? "&#9745;" : "&#9744;"}</span>${t.text}</div>`;
          }
        }
        if (commitmentsDraft.length > 0) {
          html += `<div style="font-size:11px;color:#E8683A;text-transform:uppercase;letter-spacing:0.5px;margin:10px 0 6px">Their Commitments</div>`;
          for (const c of commitmentsDraft) {
            html += `<div style="margin-bottom:4px;padding-left:16px">&bull; ${c.text}</div>`;
          }
        }
      }

      // Coaching metrics
      const cmAny = (coaching?.coaching_metrics ?? {}) as Record<string, unknown>;
      const num = (k: string): number | null => (typeof cmAny[k] === "number" ? (cmAny[k] as number) : null);
      const tr = cmAny["talk_ratio"] as
        | { rep_percentage?: number; customer_percentage?: number }
        | undefined;
      const qc = cmAny["question_count"] as { rep?: number; customer?: number } | undefined;
      const fc = cmAny["filler_word_count"] as { rep?: number; customer?: number } | undefined;
      const lm = num("longest_monologue_seconds");
      const ic = num("interruption_count");
      const rt = num("average_response_time_seconds");
      const si = num("silence_percentage");
      if (tr || lm != null || qc || fc || rt != null || si != null) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Coaching Metrics</h2>`;
        if (tr && typeof tr.rep_percentage === "number") {
          html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Talk Ratio</span><span style="font-weight:500">You ${Math.round(tr.rep_percentage)}% / Them ${Math.round(tr.customer_percentage ?? 100 - tr.rep_percentage)}%</span></div>`;
        }
        if (lm != null) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Longest Monologue</span><span style="font-weight:500">${fmtClock(lm)}</span></div>`;
        if (qc) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Questions Asked</span><span style="font-weight:500">You: ${qc.rep ?? 0} / Them: ${qc.customer ?? 0}</span></div>`;
        if (fc) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Filler Words</span><span style="font-weight:500">You: ${fc.rep ?? 0} / Them: ${fc.customer ?? 0}</span></div>`;
        if (ic != null && ic > 0) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Interruptions</span><span style="font-weight:500">${ic}</span></div>`;
        if (rt != null) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Avg Response Time</span><span style="font-weight:500">${rt.toFixed(1)}s</span></div>`;
        if (si != null) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Silence</span><span style="font-weight:500">${si.toFixed(1)}%</span></div>`;
      }

      // Transcript
      if (transcript && transcript.segments.length > 0) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Transcript</h2>`;
        html += `<div style="color:#71717a;margin-bottom:10px;font-size:12px">${transcript.word_count.toLocaleString()} words · ${transcript.speaker_count} speakers</div>`;
        for (const seg of transcript.segments) {
          const time = fmtClock(seg.original_start ?? seg.start);
          const rawSpeaker = seg.speaker || "Speaker";
          const sp = transcript.speaker_map?.[rawSpeaker]?.name || rawSpeaker;
          html += `<div style="margin-bottom:6px;display:flex;gap:10px"><span style="color:#a1a1aa;font-family:monospace;font-size:11px;min-width:40px;flex-shrink:0">${time}</span><div><span style="font-weight:600;color:#5C5CE0">[${sp}]</span> ${seg.text}</div></div>`;
        }
      }

      html += `<div style="margin-top:32px;padding-top:12px;border-top:1px solid #e4e4e7;color:#a1a1aa;font-size:11px;line-height:1.6">Generated by Memoir · ${new Date().toLocaleDateString()}</div></div>`;

      // Wrap and feed to html2pdf
      const wrapper = document.createElement("div");
      wrapper.style.position = "fixed";
      wrapper.style.left = "-9999px";
      wrapper.style.top = "0";
      wrapper.innerHTML = `<div style="width:760px;padding:30px">${html}</div>`;
      document.body.appendChild(wrapper);
      try {
        await html2pdf()
          .from(wrapper.firstElementChild as HTMLElement)
          .set({
            margin: 10,
            filename: `${(meeting.title || "meeting").replace(/[^a-zA-Z0-9]/g, "_")}.pdf`,
            image: { type: "jpeg", quality: 0.92 },
            html2canvas: { scale: 2, useCORS: true },
            jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
          })
          .save();
      } finally {
        document.body.removeChild(wrapper);
      }
    } catch {
      // ignore — user can retry
    } finally {
      setPdfDownloading(false);
      setShareOpen(false);
    }
  };

  // ── Derived data ──────────────────────────────────────────────────
  const platform = platformFromSourceOrUrl(meeting?.source ?? null, meeting?.meeting_url ?? null);
  const cm = (coaching?.coaching_metrics ?? {}) as Record<string, unknown>;
  const cmNum = (k: string): number | null => {
    const v = cm[k];
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  };

  // ── P5 + P10 derived arrays (kept separate — user-visible split) ──
  const p5Decisions = useMemo(() => summary?.decisions ?? [], [summary]);
  const p10Decisions = useMemo(() => memoir?.next_steps?.decisions ?? [], [memoir]);
  const p5OpenQuestions = useMemo(() => summary?.open_questions ?? [], [summary]);
  const p10OpenQuestions = useMemo(() => memoir?.next_steps?.open_questions ?? [], [memoir]);
  const p5Threads = useMemo(() => summary?.discussion_threads ?? [], [summary]);
  const p10Threads = useMemo(() => memoir?.signals?.discussion_threads ?? [], [memoir]);
  const p5ActionItems = useMemo(() => summary?.action_items ?? [], [summary]);

  // All distinct speaker labels for color assignment in the transcript
  const allSpeakerLabels = useMemo(() => {
    if (!transcript) return [];
    const set = new Set<string>();
    for (const s of transcript.segments) if (s.speaker) set.add(s.speaker);
    return Array.from(set);
  }, [transcript]);

  // Display name for a transcript speaker label (uses transcript.speaker_map)
  const speakerDisplay = (label: string | undefined): string => {
    if (!label) return "Speaker";
    if (transcript?.speaker_map && transcript.speaker_map[label]) {
      return transcript.speaker_map[label].name || label;
    }
    return label;
  };

  // Rep / Prospect attribution — multi-signal in priority order:
  //   1. memoir.call_metadata.participants[].role lookup by speaker name
  //      (the P10 extractor explicitly labels each as rep / prospect /
  //      participant / other — this is what v1 displays).
  //   2. internalEmails match on MeetingParticipantInfo.email.
  //   3. internalNames match on the resolved speaker display name.
  //   4. Last-resort: first speaker by appearance == Rep (Memoir
  //      recorder convention).
  const speakerRole = (label: string | undefined): "Rep" | "Prospect" => {
    if (!label) return "Prospect";
    const part = speakers.find((p) => p.diarization_label === label);
    const displayName = (
      (transcript?.speaker_map?.[label]?.name) ||
      part?.display_name ||
      label ||
      ""
    ).toLowerCase().trim();

    // (1) memoir role lookup
    const callMetaRole = memoir?.call_metadata?.participants?.find(
      (cp) => (cp.name || "").toLowerCase().trim() === displayName,
    )?.role;
    if (callMetaRole) {
      const r = callMetaRole.toLowerCase();
      if (r === "rep" || r === "sales" || r === "internal" || r === "host") return "Rep";
      if (r === "prospect" || r === "customer" || r === "external") return "Prospect";
    }

    // (2) email match
    const email = (part?.email || "").toLowerCase();
    if (email && internalEmails.has(email)) return "Rep";

    // (3) name match against internal participant set
    if (displayName && internalNames.has(displayName)) return "Rep";

    // (4) first-label-seen fallback
    return allSpeakerLabels.indexOf(label) === 0 ? "Rep" : "Prospect";
  };

  // Talk-ratio computation: backend coaching_metrics.talk_ratio is an
  // object `{rep_percentage, customer_percentage, assessment}` — matches
  // the v1 detail page schema.
  const talkRatio = useMemo(() => {
    const tr = cm["talk_ratio"] as
      | { rep_percentage?: number; customer_percentage?: number }
      | undefined;
    if (tr && typeof tr === "object") {
      const rep = typeof tr.rep_percentage === "number" ? tr.rep_percentage : null;
      const cust = typeof tr.customer_percentage === "number" ? tr.customer_percentage : null;
      if (rep != null || cust != null) {
        const r = rep ?? (cust != null ? 100 - cust : 0);
        const c = cust ?? (rep != null ? 100 - rep : 0);
        return { rep: Math.round(r), prospect: Math.round(c) };
      }
    }

    // Fallback to summing speakers.talk_time_seconds
    const totals = { rep: 0, prospect: 0 };
    for (const p of speakers) {
      const role = speakerRole(p.diarization_label);
      const t = p.talk_time_seconds || 0;
      if (role === "Rep") totals.rep += t;
      else totals.prospect += t;
    }
    const total = totals.rep + totals.prospect;
    if (total === 0) return null;
    return {
      rep: Math.round((totals.rep / total) * 100),
      prospect: Math.round((totals.prospect / total) * 100),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cm, speakers, internalEmails, internalNames, allSpeakerLabels, memoir]);

  if (loading && !meeting) {
    return (
      <div className="flex h-full items-center justify-center bg-surface-soft">
        <div className="text-sm text-ink-secondary">Loading…</div>
      </div>
    );
  }

  // Back-to-list handler that preserves the source URL's filters/date.
  // sessionStorage key set by MeetingsV3 row click; falls back to the
  // bare /meetings route if missing (direct-link arrivals).
  const goBackToList = () => {
    if (typeof window !== "undefined") {
      const stored = sessionStorage.getItem("memoir_meetings_back");
      if (stored) {
        sessionStorage.removeItem("memoir_meetings_back");
        router.push(stored);
        return;
      }
    }
    router.push("/meetings");
  };

  if (error || !meeting) {
    return (
      <div className="flex h-full items-center justify-center bg-surface-soft">
        <div className="text-center">
          <div className="mb-3 text-sm font-bold text-ink">{error || "Meeting not found"}</div>
          <button
            onClick={goBackToList}
            className="rounded-md bg-brand px-4 py-1.5 text-xs font-extrabold text-white"
          >
            Back to Meetings
          </button>
        </div>
      </div>
    );
  }

  const meetingDate = `${fmtDate(meeting.started_at || meeting.created_at)} · ${fmtTime(
    meeting.started_at || meeting.created_at,
  )} · ${fmtDuration(meeting.duration_seconds)}`;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-white font-sans text-ink">
      {/* ── Top bar ─────────────────────────────────────────────────── */}
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-surface-border bg-white px-7">
        <button
          onClick={goBackToList}
          className="flex items-center gap-1.5 text-[13px] font-extrabold text-brand"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Meetings
        </button>
        <div className="flex-1" />
        <button
          onClick={() => setShowFindReplace((v) => !v)}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold ${
            showFindReplace
              ? "border-brand bg-brand-light text-brand"
              : "border-surface-border bg-surface-soft text-ink-secondary"
          }`}
        >
          <Replace className="h-3.5 w-3.5" /> Find &amp; Replace
        </button>
        <div className="relative" ref={shareRef}>
          <button
            onClick={() => setShareOpen((v) => !v)}
            className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold ${
              shareOpen
                ? "border-brand bg-brand-light text-brand"
                : "border-surface-border bg-surface-soft text-ink-secondary"
            }`}
          >
            {shareCopied ? (
              <>
                <Check className="h-3.5 w-3.5 text-brand-green" /> Link copied
              </>
            ) : (
              <>
                <Share2 className="h-3.5 w-3.5" /> Share
                <ChevronDown
                  className={`h-3 w-3 transition-transform ${shareOpen ? "rotate-180" : ""}`}
                />
              </>
            )}
          </button>
          {shareOpen && (
            <div className="absolute right-0 top-full z-40 mt-1 w-60 rounded-lg border border-surface-border bg-white py-1.5 shadow-lg">
              <button
                onClick={handleCopyLink}
                disabled={generatingLink}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
              >
                {shareCopied ? (
                  <Check className="h-3.5 w-3.5 text-brand-green" />
                ) : (
                  <Link2 className="h-3.5 w-3.5 text-ink-secondary" />
                )}
                {shareCopied
                  ? "Link copied"
                  : generatingLink
                  ? "Generating link..."
                  : "Copy public share link"}
              </button>
              <button
                onClick={handleDownloadPdf}
                disabled={pdfDownloading}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
              >
                <FileText className="h-3.5 w-3.5 text-ink-secondary" />
                {pdfDownloading ? "Generating PDF..." : "Download PDF"}
              </button>
              <button
                onClick={handleDownloadAudio}
                disabled={!audioSrc || audioDownloading}
                title={!audioSrc ? "No audio loaded yet" : "Download recording"}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
              >
                <Music className="h-3.5 w-3.5 text-ink-secondary" />
                {audioDownloading ? "Downloading audio..." : "Download audio"}
              </button>
              <button
                onClick={handleExportCsv}
                disabled={!transcript || transcript.segments.length === 0}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
              >
                <FileSpreadsheet className="h-3.5 w-3.5 text-ink-secondary" />
                Export transcript CSV
              </button>
              <div className="mx-2 my-1 border-t border-surface-border" />
              <button
                onClick={openEmailDialog}
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-brand transition-colors hover:bg-brand-light"
              >
                Send email…
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Pipeline progress strip — component handles its own wrapper so the
          page collapses cleanly when the strip is hidden (no leftover padding). */}
      {/* Pass data as undefined (not null) when parent hasn't polled yet,
          so the strip can fall back to self-polling rather than waiting
          forever on a parent-supplied payload that never arrives. */}
      <PipelineProgressStrip meetingId={id} status={meeting?.status} data={pipelinePhases ?? undefined} />

      {/* ── Find & Replace strip ────────────────────────────────────── */}
      {showFindReplace && (
        <div className="flex shrink-0 items-center gap-2.5 border-b border-surface-border bg-surface-soft px-7 py-3">
          <Search className="h-3.5 w-3.5 text-ink-secondary" />
          <input
            value={findText}
            onChange={(e) => setFindText(e.target.value)}
            placeholder="Find word or name..."
            className="w-[180px] rounded-md border border-surface-border bg-white px-2.5 py-1.5 text-[12.5px] outline-none"
          />
          <span className="text-xs font-bold text-ink-secondary">→</span>
          <input
            value={replaceText}
            onChange={(e) => setReplaceText(e.target.value)}
            placeholder="Replace with..."
            className="w-[180px] rounded-md border border-surface-border bg-white px-2.5 py-1.5 text-[12.5px] outline-none"
          />
          <div className="flex gap-1 rounded-md border border-surface-border bg-white p-[3px]">
            {(
              [
                { k: "all", l: "Everywhere" },
                { k: "summary", l: "Summary" },
                { k: "transcript", l: "Transcript" },
                { k: "actions", l: "Actions" },
              ] as const
            ).map((o) => (
              <button
                key={o.k}
                onClick={() => setReplaceScope(o.k)}
                className={`rounded-[5px] px-2.5 py-1 text-[11px] font-extrabold ${
                  replaceScope === o.k ? "bg-brand text-white" : "text-ink-secondary"
                }`}
              >
                {o.l}
              </button>
            ))}
          </div>
          <button
            onClick={handleReplaceAll}
            disabled={!findText}
            className={`rounded-md px-3.5 py-1.5 text-xs font-extrabold ${
              findText ? "bg-brand text-white" : "bg-surface-soft text-ink-light"
            }`}
          >
            Replace All
          </button>
          {replaceCount !== null && (
            <span className="text-xs font-extrabold text-brand-green">
              ✓ {replaceCount} replaced
            </span>
          )}
          <div className="flex-1" />
          <button onClick={() => setShowFindReplace(false)} className="rounded p-1">
            <X className="h-4 w-4 text-ink-secondary" />
          </button>
        </div>
      )}

      <div className="flex-1 overflow-auto bg-surface-soft">
        {/* ── Meeting header card ─────────────────────────────────── */}
        <div className="border-b border-surface-border bg-white px-8 py-6">
          <div className="mb-2 flex items-center gap-2.5">
            <PlatformBadge platform={platform} />
            <span className="text-xs font-medium text-ink-secondary">{meetingDate}</span>
            {memoir?.call_metadata?.call_type &&
              memoir.call_metadata.call_type !== "Other" &&
              memoir.call_metadata.call_type !== "null" && (
                <span className="rounded bg-surface-soft px-2 py-[2px] text-[11px] font-semibold text-ink-secondary">
                  {memoir.call_metadata.call_type}
                </span>
              )}
            {memoir?.overall_confidence_score != null && (
              <span
                className="rounded px-2 py-[2px] text-[11px] font-extrabold"
                style={{
                  background:
                    memoir.overall_confidence_score >= 70
                      ? "#E6F9F1"
                      : memoir.overall_confidence_score >= 40
                      ? "#FFF8E8"
                      : "#FEECEE",
                  color:
                    memoir.overall_confidence_score >= 70
                      ? "#00B87C"
                      : memoir.overall_confidence_score >= 40
                      ? "#A66400"
                      : "#E5484D",
                }}
                title="Overall confidence score from memoir extraction"
              >
                {memoir.overall_confidence_score}% confidence
              </span>
            )}
          </div>
          <h1 className="mb-1 text-[24px] font-extrabold tracking-tight text-ink">
            {meeting.title || "Untitled meeting"}
          </h1>
          {meeting.primary_participant_name && (
            <div className="text-[13px] font-medium text-ink-secondary">
              {meeting.primary_participant_name}
              {meeting.primary_participant_company && (
                <span> · {meeting.primary_participant_company}</span>
              )}
            </div>
          )}
        </div>

        {/* ── Tabs ─────────────────────────────────────────────────── */}
        <div className="flex gap-0 border-b border-surface-border bg-white px-8">
          {(
            [
              { k: "summary" as const, l: "AI Summary", Icon: Sparkles },
              { k: "transcript" as const, l: "Transcript", Icon: MessageSquare },
              { k: "crm" as const, l: "CRM Update", Icon: Mic },
            ] as const
          ).map((t) => (
            <button
              key={t.k}
              onClick={() => setActiveTab(t.k)}
              className={`flex items-center gap-1.5 border-b-[2.5px] px-5 py-3 text-[13.5px] ${
                activeTab === t.k
                  ? "border-brand font-extrabold text-brand"
                  : "border-transparent font-medium text-ink-secondary"
              }`}
            >
              <t.Icon className="h-3.5 w-3.5" />
              {t.l}
            </button>
          ))}
        </div>

        {/* ── Content grid ────────────────────────────────────────── */}
        <div className="grid" style={{ gridTemplateColumns: "1fr 340px", minHeight: "calc(100% - 200px)" }}>
          {/* ── Left content ────────────────────────────────────── */}
          <div className="border-r border-surface-border px-8 py-6">
            {activeTab === "summary" && (
              <SummaryTab
                summaryText={summaryDraft || memoir?.narrative_summary || ""}
                onSummaryChange={saveSummaryDraft}
                suggestedStage={
                  memoir?.next_steps?.suggested_deal_stage ||
                  callIntel?.crm_stage_label ||
                  callIntel?.validated_stage ||
                  null
                }
                confidenceScore={memoir?.overall_confidence_score ?? null}
                isEdited={memoir?.is_edited ?? false}
                callType={memoir?.call_metadata?.call_type ?? null}
                milestone={nextSteps.milestone}
                onMilestoneChange={saveNextStepMilestone}
                nextMeetingDate={nextSteps.next_meeting_date}
                onNextMeetingChange={saveNextStepDate}
                todos={todosDraft}
                setTodos={setTodosDraft}
                commitments={commitmentsDraft}
                setCommitments={setCommitmentsDraft}
                persistActionItems={persistActionItems}
                buyingSignals={buyingSignals}
                setBuyingSignals={setBuyingSignals}
                riskSignals={riskSignals}
                setRiskSignals={setRiskSignals}
                persistSignals={persistSignals}
                discoveryFields={memoir?.discovery_fields ?? null}
                p5Tldr={summary?.tldr ?? null}
                p5ActionItems={p5ActionItems}
                p5Decisions={p5Decisions}
                p5OpenQuestions={p5OpenQuestions}
                p5DiscussionThreads={p5Threads}
                p10Decisions={p10Decisions}
                p10OpenQuestions={p10OpenQuestions}
                p10DiscussionThreads={p10Threads}
                onResetMemoir={handleResetMemoir}
                resettingMemoir={resettingMemoir}
                memoirIsEdited={memoir?.is_edited ?? false}
                disclaimer={memoir?.disclaimer ?? null}
              />
            )}

            {activeTab === "transcript" && (
              <TranscriptTab
                transcript={transcript}
                allSpeakerLabels={allSpeakerLabels}
                speakerDisplay={speakerDisplay}
                speakerRole={speakerRole}
                onEditSegment={saveTranscriptSegment}
                onReset={handleResetTranscript}
                resetting={resettingTranscript}
                isEdited={transcript?.is_edited ?? false}
                meetingId={id}
                teamId={meeting?.team_id}
                onSpeakerAssigned={() => {
                  api.getMeetingSpeakers(id)
                    .then((resp) => setSpeakers(resp.participants))
                    .catch(() => {});
                  api.getTranscript(id).then((t) => setTranscript(t)).catch(() => {});
                  api.getCoaching(id).then((c) => setCoaching(c)).catch(() => {});
                }}
              />
            )}

            {activeTab === "crm" && (
              <CrmTab
                meeting={meeting}
                crmConnections={crmConnections}
                crmPushResults={crmPushResults}
                callIntel={callIntel}
                discoveryFields={memoir?.discovery_fields ?? null}
                activeCrmProvider={activeCrmProvider}
                setActiveCrmProvider={setActiveCrmProvider}
                crmAction={crmAction}
                regenerating={regeneratingCrm}
                crmExtractError={crmExtractError}
                editingField={editingCrmField}
                editingValue={editingCrmValue}
                setEditingField={setEditingCrmField}
                setEditingValue={setEditingCrmValue}
                onPush={handlePushCrm}
                onSkip={handleSkipCrm}
                onEditField={handleEditCrmField}
                onRegenerate={handleRegenerateCrm}
              />
            )}
          </div>

          {/* ── Right sidebar ───────────────────────────────────── */}
          {/* Sticky so Speakers / Coaching / PMF stay visible while the
              left content scrolls. `self-start` is required for sticky
              children of a grid track. */}
          <aside className="sticky top-0 self-start max-h-screen overflow-auto bg-white px-5 py-6">
            {/* Recording */}
            <div className="mb-5 rounded-xl border border-surface-border bg-surface-soft p-4">
              <div className="mb-3 text-[11px] font-extrabold uppercase tracking-wider text-ink-secondary">
                Recording
              </div>
              {audioSrc ? (
                <div className="flex items-center gap-3">
                  <button
                    onClick={togglePlay}
                    className="flex h-10 w-10 items-center justify-center rounded-full bg-brand text-white shadow-md"
                  >
                    {audioPlaying ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
                  </button>
                  <div className="flex-1">
                    <div
                      role="slider"
                      tabIndex={0}
                      aria-label="Seek audio"
                      aria-valuemin={0}
                      aria-valuemax={audioDuration || 0}
                      aria-valuenow={audioTime}
                      className="-my-2 cursor-pointer py-2"
                      onClick={(e) => {
                        if (!audioRef.current || !audioDuration) return;
                        const rect = e.currentTarget.getBoundingClientRect();
                        const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
                        audioRef.current.currentTime = ratio * audioDuration;
                        setAudioTime(audioRef.current.currentTime);
                      }}
                      onKeyDown={(e) => {
                        if (!audioRef.current || !audioDuration) return;
                        if (e.key === "ArrowRight") {
                          audioRef.current.currentTime = Math.min(audioDuration, audioRef.current.currentTime + 5);
                          setAudioTime(audioRef.current.currentTime);
                        } else if (e.key === "ArrowLeft") {
                          audioRef.current.currentTime = Math.max(0, audioRef.current.currentTime - 5);
                          setAudioTime(audioRef.current.currentTime);
                        }
                      }}
                    >
                      <div className="h-1 rounded-sm bg-surface-border">
                        <div
                          className="h-full rounded-sm bg-brand"
                          style={{
                            width: `${audioDuration ? (audioTime / audioDuration) * 100 : 0}%`,
                          }}
                        />
                      </div>
                    </div>
                    <div className="mt-1 flex justify-between text-[10px] font-medium tabular-nums text-ink-secondary">
                      <span>{fmtClock(audioTime)}</span>
                      <span>{fmtClock(audioDuration)}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-xs text-ink-secondary">No recording available</div>
              )}
            </div>

            {/* Speakers */}
            <div className="mb-5">
              <div className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wider text-ink-secondary">
                Speakers
              </div>
              {speakers.length === 0 && (
                <div className="text-xs text-ink-secondary">Speaker list will appear after diarization.</div>
              )}
              {speakers.map((p) => {
                const role = speakerRole(p.diarization_label);
                const color = speakerColor(p.diarization_label, allSpeakerLabels);
                const name = p.display_name || p.diarization_label || "Speaker";
                // Enrich from P10 call_metadata.participants (title + company)
                const enriched = memoir?.call_metadata?.participants?.find(
                  (cp) => cp.name && cp.name.toLowerCase() === name.toLowerCase(),
                );
                const subtitle = [enriched?.title || p.title, enriched?.company || p.company]
                  .filter(Boolean)
                  .join(" · ");
                return (
                  <div
                    key={p.id}
                    className="flex items-center gap-2.5 border-b border-surface-border py-2"
                  >
                    <div
                      className="flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-extrabold"
                      style={{ background: `${color}20`, color }}
                    >
                      {initials(name)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-semibold text-ink">{name}</div>
                      {subtitle && (
                        <div className="truncate text-[10px] text-ink-secondary">{subtitle}</div>
                      )}
                    </div>
                    <span
                      className="rounded px-1.5 py-[2px] text-[10px] font-extrabold"
                      style={{
                        background: role === "Rep" ? "#EDEDFC" : "#FFF3EE",
                        color: role === "Rep" ? "#5C5CE0" : "#E8683A",
                      }}
                    >
                      {role}
                    </span>
                    <SpeakerMappingDropdown
                      meetingId={id}
                      teamId={meeting?.team_id}
                      diarizationLabel={p.diarization_label}
                      currentName={name}
                      onAssigned={() => {
                        api.getMeetingSpeakers(id)
                          .then((resp) => setSpeakers(resp.participants))
                          .catch(() => {});
                        api.getTranscript(id).then((t) => setTranscript(t)).catch(() => {});
                        api.getCoaching(id).then((c) => setCoaching(c)).catch(() => {});
                      }}
                    />
                    {p.speaker_profile_id && (
                      <button
                        type="button"
                        onClick={() => setMergingProfile({ id: p.speaker_profile_id!, name })}
                        title="Merge into another contact"
                        className="rounded p-1 text-ink-secondary hover:bg-surface-soft hover:text-ink"
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M8 3 4 7l4 4" />
                          <path d="M4 7h16" />
                          <path d="m16 21 4-4-4-4" />
                          <path d="M20 17H4" />
                        </svg>
                      </button>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Coaching Metrics */}
            <div className="mb-5">
              <div className="mb-3 text-[11px] font-extrabold uppercase tracking-wider text-ink-secondary">
                Coaching Metrics
              </div>
              {talkRatio && (
                <div className="mb-3.5">
                  <div className="mb-1.5 flex justify-between text-xs font-semibold">
                    <span style={{ color: "#5C5CE0" }}>You {talkRatio.rep}%</span>
                    <span style={{ color: "#E8683A" }}>Them {talkRatio.prospect}%</span>
                  </div>
                  <div className="flex h-2 overflow-hidden rounded-sm">
                    <div style={{ width: `${talkRatio.rep}%`, background: "#5C5CE0" }} />
                    <div style={{ width: `${talkRatio.prospect}%`, background: "#E8683A" }} />
                  </div>
                </div>
              )}
              {/* Coaching rows — keys match the v1 schema (cm.talk_ratio,
                  cm.question_count, cm.filler_word_count are nested objects;
                  the rest are top-level numbers). */}
              {(() => {
                const rows: Array<{ label: string; value: string }> = [];

                const lm = cmNum("longest_monologue_seconds");
                if (lm != null) rows.push({ label: "Longest monologue", value: fmtClock(lm) });

                const qc = cm["question_count"] as { rep?: number; customer?: number } | undefined;
                if (qc && typeof qc === "object") {
                  rows.push({
                    label: "Questions asked",
                    value: `You: ${qc.rep ?? 0} · Them: ${qc.customer ?? 0}`,
                  });
                }

                const fc = cm["filler_word_count"] as { rep?: number; customer?: number } | undefined;
                if (fc && typeof fc === "object") {
                  rows.push({
                    label: "Filler words",
                    value: `You: ${fc.rep ?? 0} · Them: ${fc.customer ?? 0}`,
                  });
                }

                const ic = cmNum("interruption_count");
                if (ic != null && ic > 0) rows.push({ label: "Interruptions", value: `${ic}` });

                const rt = cmNum("average_response_time_seconds");
                if (rt != null) rows.push({ label: "Avg response time", value: `${rt.toFixed(1)}s` });

                const si = cmNum("silence_percentage");
                if (si != null) rows.push({ label: "Silence", value: `${si.toFixed(1)}%` });

                if (rows.length === 0) {
                  return (
                    <div className="text-xs italic text-ink-light">
                      Coaching metrics will appear after intelligence completes.
                    </div>
                  );
                }

                return rows.map((m, idx) => (
                  <div
                    key={m.label}
                    className={`flex justify-between py-1.5 text-xs ${
                      idx < rows.length - 1 ? "border-b border-surface-border" : ""
                    }`}
                  >
                    <span className="font-medium text-ink-secondary">{m.label}</span>
                    <span className="font-extrabold text-ink">{m.value}</span>
                  </div>
                ));
              })()}
            </div>

            {/* PMF Signals — mirrors v1 sidebar card exactly */}
            {callIntel &&
              (callIntel.pull_score != null ||
                callIntel.icp_fit_score != null ||
                callIntel.pain_fit_score != null ||
                callIntel.overall_pmf_signal ||
                callIntel.deal_momentum) && (
                <PmfSignalsCard callIntel={callIntel} />
              )}
          </aside>
        </div>
      </div>

      {/* Hidden audio element controls playback */}
      {audioSrc && (
        <audio
          ref={audioRef}
          src={audioSrc}
          preload="metadata"
          onPlay={() => setAudioPlaying(true)}
          onPause={() => setAudioPlaying(false)}
          onEnded={() => setAudioPlaying(false)}
          onTimeUpdate={() => audioRef.current && setAudioTime(audioRef.current.currentTime)}
          onLoadedMetadata={() => audioRef.current && setAudioDuration(audioRef.current.duration)}
          style={{ display: "none" }}
        />
      )}

      {/* Send Email dialog — in-page modal so the user doesn't have to
          bounce to /meetings/v1 to email the summary. */}
      {emailDialogOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setEmailDialogOpen(false)}
        >
          <div
            className="flex w-full max-w-2xl flex-col rounded-xl bg-white shadow-2xl"
            style={{ maxHeight: "90vh" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-surface-border px-5 py-4">
              <h2 className="m-0 text-sm font-extrabold text-ink">Send Meeting Summary</h2>
              <button
                onClick={() => setEmailDialogOpen(false)}
                className="text-ink-secondary hover:text-ink"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
              <div>
                <label className="mb-1 block text-xs font-extrabold text-ink">To</label>
                <input
                  type="email"
                  value={emailTo}
                  onChange={(e) => {
                    setEmailTo(e.target.value);
                    setEmailSent(false);
                  }}
                  placeholder="recipient@company.com"
                  className="w-full rounded-lg border border-surface-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-extrabold text-ink">Template</label>
                <select
                  value={emailTemplate}
                  onChange={(e) =>
                    handleSelectEmailTemplate(
                      e.target.value as "recorder" | "internal" | "external",
                    )
                  }
                  className="w-full rounded-lg border border-surface-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                >
                  <option value="recorder">Full Summary (Recorder)</option>
                  <option value="internal">Team Member Summary</option>
                  <option value="external">External Participant Summary</option>
                </select>
              </div>
              {emailPreviewSubject && (
                <div>
                  <label className="mb-1 block text-xs font-extrabold text-ink">Subject</label>
                  <p className="text-sm text-ink-secondary">{emailPreviewSubject}</p>
                </div>
              )}
              <div>
                <label className="mb-1 block text-xs font-extrabold text-ink">Preview</label>
                {emailPreviewHtml ? (
                  <div
                    className="overflow-hidden rounded-lg border border-surface-border bg-white"
                    style={{ maxHeight: 400 }}
                  >
                    <iframe
                      srcDoc={emailPreviewHtml}
                      title="Email preview"
                      className="w-full border-0"
                      style={{ height: 380 }}
                      sandbox="allow-same-origin"
                    />
                  </div>
                ) : (
                  <p className="text-sm italic text-ink-secondary">Loading preview...</p>
                )}
              </div>
              {emailLog.length > 0 && (
                <div>
                  <label className="mb-1 block text-xs font-extrabold text-ink">
                    Previously Sent
                  </label>
                  <div className="space-y-1">
                    {emailLog.map((log) => (
                      <div
                        key={log.id}
                        className="flex items-center justify-between text-xs text-ink-secondary"
                      >
                        <span>{log.to_email}</span>
                        <span>
                          {log.status === "sent" ? "✓" : "✗"}{" "}
                          {new Date(log.created_at).toLocaleDateString()}{" "}
                          {new Date(log.created_at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-3 border-t border-surface-border px-5 py-4">
              {emailSent && (
                <span className="text-xs font-extrabold text-emerald-600">
                  Email sent successfully!
                </span>
              )}
              <button
                onClick={() => setEmailDialogOpen(false)}
                className="rounded-lg border border-surface-border px-4 py-2 text-xs font-extrabold text-ink-secondary hover:bg-surface-soft"
              >
                Cancel
              </button>
              <button
                disabled={
                  !emailTo ||
                  !emailTo.includes("@") ||
                  emailSending ||
                  !emailPreviewHtml
                }
                onClick={handleSendEmail}
                className="rounded-lg bg-brand px-4 py-2 text-xs font-extrabold text-white hover:bg-brand-dark disabled:opacity-50"
              >
                {emailSending ? "Sending..." : "Send Email"}
              </button>
            </div>
          </div>
        </div>
      )}
      {mergingProfile && (
        <SpeakerMergeDialog
          sourceProfileId={mergingProfile.id}
          sourceName={mergingProfile.name}
          teamId={meeting?.team_id}
          onClose={() => setMergingProfile(null)}
          onMerged={() => {
            // After merge, refresh everything that references speakers.
            api.getMeetingSpeakers(id).then((r) => setSpeakers(r.participants)).catch(() => {});
            api.getTranscript(id).then((t) => setTranscript(t)).catch(() => {});
            api.getCoaching(id).then((c) => setCoaching(c)).catch(() => {});
          }}
        />
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Tab: AI Summary
// ────────────────────────────────────────────────────────────────────────────

type ThreadEntry = {
  title?: string;
  priority?: string;
  summary?: string;
  attributions?: Array<{ speaker?: string; action?: string }>;
};

interface SummaryTabProps {
  // ── P10 (memoir) — editable via editMemoir ──
  summaryText: string;
  onSummaryChange: (next: string) => void;
  suggestedStage: string | null;
  confidenceScore: number | null;
  isEdited: boolean;
  callType: string | null;
  milestone: string;
  onMilestoneChange: (next: string) => void;
  nextMeetingDate: string;
  onNextMeetingChange: (next: string) => void;
  todos: ActionItemVM[];
  setTodos: React.Dispatch<React.SetStateAction<ActionItemVM[]>>;
  commitments: ActionItemVM[];
  setCommitments: React.Dispatch<React.SetStateAction<ActionItemVM[]>>;
  persistActionItems: (todos: ActionItemVM[], commitments: ActionItemVM[]) => Promise<void>;
  buyingSignals: string[];
  setBuyingSignals: React.Dispatch<React.SetStateAction<string[]>>;
  riskSignals: string[];
  setRiskSignals: React.Dispatch<React.SetStateAction<string[]>>;
  persistSignals: (buying: string[], risk: string[]) => Promise<void>;
  discoveryFields: CrmField[] | null;
  disclaimer: string | null;

  // ── P10 sections, read-only ──
  p10Decisions: Array<{ title?: string; detail?: string }>;
  p10OpenQuestions: Array<{ text?: string }>;
  p10DiscussionThreads: ThreadEntry[];

  // ── P5 (summary.json) — read-only ──
  p5Tldr: string | null;
  p5ActionItems: Array<{
    text?: string;
    owner?: string;
    owner_email?: string;
    urgency?: string;
    context?: string | null;
    blocking?: string | null;
  }>;
  p5Decisions: Array<{ title?: string; detail?: string }>;
  p5OpenQuestions: Array<{ text?: string }>;
  p5DiscussionThreads: ThreadEntry[];

  // ── Reset action ──
  onResetMemoir: () => void;
  resettingMemoir: boolean;
  memoirIsEdited: boolean;
}

function SummaryTab({
  summaryText,
  onSummaryChange,
  suggestedStage,
  confidenceScore,
  isEdited,
  callType,
  milestone,
  onMilestoneChange,
  nextMeetingDate,
  onNextMeetingChange,
  todos,
  setTodos,
  commitments,
  setCommitments,
  persistActionItems,
  buyingSignals,
  setBuyingSignals,
  riskSignals,
  setRiskSignals,
  persistSignals,
  discoveryFields,
  disclaimer,
  p10Decisions,
  p10OpenQuestions,
  p10DiscussionThreads,
  p5Tldr,
  p5ActionItems,
  p5Decisions,
  p5OpenQuestions,
  p5DiscussionThreads,
  onResetMemoir,
  resettingMemoir,
  memoirIsEdited,
}: SummaryTabProps) {
  // Mutate-then-persist helpers — keep render synchronous and route the
  // PATCH through the parent's stable callbacks.
  const updateTodos = (next: ActionItemVM[]) => {
    setTodos(next);
    void persistActionItems(next, commitments);
  };
  const updateCommitments = (next: ActionItemVM[]) => {
    setCommitments(next);
    void persistActionItems(todos, next);
  };
  const updateBuying = (next: string[]) => {
    setBuyingSignals(next);
    void persistSignals(next, riskSignals);
  };
  const updateRisk = (next: string[]) => {
    setRiskSignals(next);
    void persistSignals(buyingSignals, next);
  };
  return (
    <>
      {/* ── Tab-level reset bar ── */}
      {memoirIsEdited && (
        <div className="mb-4 flex items-center justify-between rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
          <div className="text-[12px] text-amber-700">
            <span className="font-extrabold">P10 edited.</span> Your edits to Summary, Action
            Items, Signals, and Next Steps are overriding the AI output.
          </div>
          <button
            onClick={onResetMemoir}
            disabled={resettingMemoir}
            className="rounded-md border border-amber-300 bg-white px-3 py-1 text-[11px] font-extrabold text-amber-700 hover:bg-amber-100 disabled:opacity-50"
          >
            {resettingMemoir ? "Resetting…" : "Reset all P10 to AI output"}
          </button>
        </div>
      )}

      {/* ── [P10] Narrative Summary — editable via editMemoir ── */}
      <Card>
        <div className="mb-3.5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <SourceBadge source="P10" />
            <h3 className="m-0 text-sm font-extrabold text-ink">Narrative Summary</h3>
            {callType && callType !== "Other" && callType !== "null" && (
              <span className="rounded bg-surface-soft px-2 py-[2px] text-[11px] font-semibold text-ink-secondary">
                {callType}
              </span>
            )}
            {confidenceScore != null && (
              <span
                className="rounded px-2 py-[2px] text-[11px] font-extrabold"
                style={{
                  background: confidenceScore >= 70 ? "#E6F9F1" : confidenceScore >= 40 ? "#FFF8E8" : "#FEECEE",
                  color: confidenceScore >= 70 ? "#00B87C" : confidenceScore >= 40 ? "#A66400" : "#E5484D",
                }}
                title="Overall confidence score from memoir extraction"
              >
                {confidenceScore}% confidence
              </span>
            )}
            {isEdited && (
              <span className="rounded bg-brand-light px-2 py-[2px] text-[11px] font-extrabold text-brand">
                Edited
              </span>
            )}
          </div>
          <span className="flex items-center gap-1 rounded bg-brand-light px-2 py-[2px] text-[11px] font-semibold text-brand">
            <Edit3 className="h-2.5 w-2.5" /> Click to edit
          </span>
        </div>
        <EditableField
          value={summaryText}
          onChange={onSummaryChange}
          multiline
          placeholder="Meeting summary..."
        />
      </Card>

      {/* ── [P5] TL;DR — read-only from summary.json ── */}
      {p5Tldr && (
        <Card>
          <CardHeader title="TL;DR" source="P5" readOnly />
          <p className="m-0 whitespace-pre-wrap text-[13.5px] font-normal leading-[1.7] text-ink">
            {p5Tldr}
          </p>
        </Card>
      )}

      {/* ── [P10] Next Steps — editable (milestone+date in meetings table, suggested_deal_stage from memoir) ── */}
      <Card>
        <div className="mb-3.5 flex items-center gap-2">
          <SourceBadge source="P10" />
          <h3 className="m-0 text-sm font-extrabold text-ink">Next Steps</h3>
          {suggestedStage && (
            <>
              <span className="rounded bg-brand-light px-2 py-[2px] text-[11px] font-extrabold text-brand">
                {suggestedStage}
              </span>
              <span className="text-[11px] font-medium text-ink-secondary">Suggested stage</span>
            </>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <span className="text-ink-secondary">📋</span>
            <span className="font-semibold">Milestone</span>
          </div>
          <EditableField
            value={milestone}
            onChange={onMilestoneChange}
            placeholder="Add milestone..."
          />
          <div className="mt-1 flex items-center gap-2 text-[13px] font-medium">
            <span className="text-ink-secondary">📅</span>
            <span className="font-semibold">Next meeting</span>
          </div>
          <EditableField
            value={nextMeetingDate}
            onChange={onNextMeetingChange}
            placeholder="Add next meeting date..."
          />
        </div>
      </Card>

      {/* ── [P10] Action Items — editable (memoir.action_items) ── */}
      <Card>
        <CardHeader title="Action Items" editHint source="P10" />

        <div className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wider text-brand">
          Your To-Dos
        </div>
        {todos.map((todo, i) => (
          <div
            key={i}
            className={`flex items-center gap-2.5 py-2 ${
              i < todos.length - 1 ? "border-b border-surface-border" : ""
            }`}
          >
            <button
              onClick={() =>
                updateTodos(todos.map((td, j) => (j === i ? { ...td, done: !td.done } : td)))
              }
              className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md ${
                todo.done ? "" : "border-2 border-surface-border"
              }`}
              style={todo.done ? { background: "#E6F9F1" } : {}}
              aria-label={todo.done ? "Mark as not done" : "Mark as done"}
            >
              {todo.done && <Check className="h-3 w-3" style={{ color: "#00B87C" }} strokeWidth={3} />}
            </button>
            <div className="min-w-0 flex-1">
              <EditableField
                value={todo.text}
                onChange={(v) =>
                  updateTodos(todos.map((td, j) => (j === i ? { ...td, text: v } : td)))
                }
                compact
              />
              {(todo.owner || todo.urgency || todo.context) && (
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-ink-secondary">
                  {todo.owner && <span>👤 {todo.owner}</span>}
                  {todo.urgency && (
                    <span className="rounded bg-brand-light px-1.5 py-[1px] text-brand">
                      {todo.urgency.replace(/_/g, " ")}
                    </span>
                  )}
                  {todo.context && <span className="italic text-ink-light">{todo.context}</span>}
                </div>
              )}
            </div>
            <button
              onClick={() => updateTodos(todos.filter((_, j) => j !== i))}
              className="opacity-40 hover:opacity-100"
              title="Remove"
            >
              <Trash2 className="h-3.5 w-3.5 text-brand-red" />
            </button>
          </div>
        ))}
        <button
          onClick={() => updateTodos([...todos, { text: "", done: false, bucket: "todo" }])}
          className="flex items-center gap-1.5 py-2.5 text-xs font-extrabold text-brand"
        >
          <Plus className="h-3 w-3" /> Add to-do
        </button>

        <div className="mt-2 border-t border-surface-border pt-3.5">
          <div className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wider" style={{ color: "#E8683A" }}>
            Their Commitments
          </div>
          {commitments.map((c, i) => (
            <div
              key={i}
              className={`flex items-center gap-2.5 py-2 ${
                i < commitments.length - 1 ? "border-b border-surface-border" : ""
              }`}
            >
              <span
                className="flex h-5 w-5 shrink-0 items-center justify-center text-sm font-extrabold"
                style={{ color: "#E8683A" }}
              >
                •
              </span>
              <div className="min-w-0 flex-1">
                <EditableField
                  value={c.text}
                  onChange={(v) =>
                    updateCommitments(
                      commitments.map((cc, j) => (j === i ? { ...cc, text: v } : cc)),
                    )
                  }
                  compact
                />
                {(c.owner || c.urgency) && (
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-ink-secondary">
                    {c.owner && <span>👤 {c.owner}</span>}
                    {c.urgency && (
                      <span
                        className="rounded px-1.5 py-[1px]"
                        style={{ background: "#FFF3EE", color: "#E8683A" }}
                      >
                        {c.urgency.replace(/_/g, " ")}
                      </span>
                    )}
                  </div>
                )}
              </div>
              <button
                onClick={() => updateCommitments(commitments.filter((_, j) => j !== i))}
                className="opacity-40 hover:opacity-100"
              >
                <Trash2 className="h-3.5 w-3.5 text-brand-red" />
              </button>
            </div>
          ))}
          <button
            onClick={() => updateCommitments([...commitments, { text: "", bucket: "commitment" }])}
            className="flex items-center gap-1.5 py-2.5 text-xs font-extrabold"
            style={{ color: "#E8683A" }}
          >
            <Plus className="h-3 w-3" /> Add commitment
          </button>
        </div>
      </Card>

      {/* ── [P5] Action Items — read-only (summary.json) ── */}
      {p5ActionItems.length > 0 && (
        <Card>
          <CardHeader title="Action Items" source="P5" readOnly />
          <div className="text-[11px] text-ink-secondary mb-2">
            From summary.json. Not editable — edit the P10 list above to persist changes.
          </div>
          {p5ActionItems.map((a, i) => (
            <div
              key={i}
              className={`flex items-start gap-2.5 py-2 ${
                i < p5ActionItems.length - 1 ? "border-b border-surface-border" : ""
              }`}
            >
              <span
                className="mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ background: "#7C7C8A" }}
              />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] leading-[1.5] text-ink">{a.text}</div>
                {(a.owner || a.urgency || a.context || a.blocking) && (
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-ink-secondary">
                    {a.owner && <span>👤 {a.owner}</span>}
                    {a.urgency && (
                      <span className="rounded bg-brand-light px-1.5 py-[1px] text-brand">
                        {a.urgency.replace(/_/g, " ")}
                      </span>
                    )}
                    {a.blocking && (
                      <span className="text-brand-red">Blocking: {a.blocking}</span>
                    )}
                    {a.context && (
                      <span className="italic text-ink-light">{a.context}</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
        </Card>
      )}

      {/* ── [P10] Signals — editable buying / risk lists (memoir.signals) ── */}
      <Card>
        <CardHeader title="Signals" editHint source="P10" />
        <SignalList
          label="Buying signals"
          tone="green"
          items={buyingSignals}
          onChange={updateBuying}
          addLabel="Add buying signal"
        />
        <div className="mt-3 border-t border-surface-border pt-3">
          <SignalList
            label="Risk signals"
            tone="red"
            items={riskSignals}
            onChange={updateRisk}
            addLabel="Add risk signal"
          />
        </div>
      </Card>

      {/* ── [P10] Decisions — read-only (memoir.next_steps.decisions) ── */}
      {p10Decisions.length > 0 && (
        <Card>
          <CardHeader title="Decisions" source="P10" readOnly />
          <DecisionsList decisions={p10Decisions} />
        </Card>
      )}

      {/* ── [P5] Decisions — read-only (summary.json) ── */}
      {p5Decisions.length > 0 && (
        <Card>
          <CardHeader title="Decisions" source="P5" readOnly />
          <DecisionsList decisions={p5Decisions} />
        </Card>
      )}

      {/* ── [P10] Open Questions ── */}
      {p10OpenQuestions.length > 0 && (
        <Card>
          <CardHeader title="Open Questions" source="P10" readOnly />
          <OpenQuestionsList items={p10OpenQuestions} />
        </Card>
      )}

      {/* ── [P5] Open Questions ── */}
      {p5OpenQuestions.length > 0 && (
        <Card>
          <CardHeader title="Open Questions" source="P5" readOnly />
          <OpenQuestionsList items={p5OpenQuestions} />
        </Card>
      )}

      {/* ── [P10] Discussion Threads ── */}
      {p10DiscussionThreads.length > 0 && (
        <Card>
          <CardHeader title="Discussion Threads" source="P10" readOnly />
          <DiscussionThreadsList items={p10DiscussionThreads} />
        </Card>
      )}

      {/* ── [P5] Discussion Threads ── */}
      {p5DiscussionThreads.length > 0 && (
        <Card>
          <CardHeader title="Discussion Threads" source="P5" readOnly />
          <DiscussionThreadsList items={p5DiscussionThreads} />
        </Card>
      )}

      {/* ── [P10] Discovery Fields — read-only memoir.discovery_fields ── */}
      {discoveryFields && discoveryFields.length > 0 && (
        <Card>
          <CardHeader title="Discovery Fields" source="P10" readOnly />
          <div className="space-y-2">
            {discoveryFields.map((f, i) => (
              <DiscoveryFieldRow key={`${f.name}-${i}`} field={f} />
            ))}
          </div>
        </Card>
      )}

      {/* ── [P10] Disclaimer ── */}
      {disclaimer && (
        <div className="mb-5 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <div className="mb-1 flex items-center gap-2 text-[11px] font-extrabold uppercase tracking-wider text-amber-700">
            <SourceBadge source="P10" />
            Disclaimer
          </div>
          <p className="m-0 text-[12px] leading-relaxed text-amber-700">{disclaimer}</p>
        </div>
      )}
    </>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Shared read-only renderers (used by both P5 and P10 source cards)
// ────────────────────────────────────────────────────────────────────────────

function DecisionsList({
  decisions,
}: {
  decisions: Array<{ title?: string; detail?: string }>;
}) {
  return (
    <>
      {decisions.map((d, i) => (
        <div
          key={i}
          className={`py-2 ${i < decisions.length - 1 ? "border-b border-surface-border" : ""}`}
        >
          {d.title && <div className="text-[13px] font-bold text-ink">{d.title}</div>}
          {d.detail && (
            <div className="mt-0.5 text-[12px] leading-relaxed text-ink-secondary">{d.detail}</div>
          )}
        </div>
      ))}
    </>
  );
}

function OpenQuestionsList({ items }: { items: Array<{ text?: string }> }) {
  return (
    <>
      {items.map((q, i) => (
        <div
          key={i}
          className={`flex items-center gap-2.5 py-2 ${
            i < items.length - 1 ? "border-b border-surface-border" : ""
          }`}
        >
          <span className="flex h-5 w-5 shrink-0 items-center justify-center">
            <span className="h-1.5 w-1.5 rounded-full bg-brand" />
          </span>
          <span className="text-[13px] leading-[1.5] text-ink">{q.text}</span>
        </div>
      ))}
    </>
  );
}

function DiscussionThreadsList({ items }: { items: ThreadEntry[] }) {
  return (
    <>
      {items.map((t, i) => {
        const priority = (t.priority || "").toLowerCase();
        return (
          <div
            key={i}
            className={`py-2.5 ${i < items.length - 1 ? "border-b border-surface-border" : ""}`}
          >
            <div className="mb-1 flex items-center justify-between gap-2">
              {t.title && <div className="text-[13px] font-bold text-ink">{t.title}</div>}
              {priority && (
                <span
                  className="shrink-0 rounded px-1.5 py-[1px] text-[10px] font-extrabold uppercase"
                  style={{
                    background:
                      priority === "high" ? "#FEECEE" : priority === "medium" ? "#FFF8E8" : "#F8F8FC",
                    color:
                      priority === "high" ? "#E5484D" : priority === "medium" ? "#A66400" : "#7C7C8A",
                  }}
                >
                  {priority}
                </span>
              )}
            </div>
            {t.summary && (
              <div className="text-[12px] leading-relaxed text-ink-secondary">{t.summary}</div>
            )}
            {t.attributions && t.attributions.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {t.attributions.map((attr, ai) => (
                  <span
                    key={ai}
                    className="rounded border border-surface-border bg-surface-soft px-1.5 py-[1px] text-[10px] text-ink-secondary"
                  >
                    <span className="font-extrabold text-brand">{attr.speaker}</span> {attr.action}
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// PMF Signals — right-sidebar card matching v1
// ────────────────────────────────────────────────────────────────────────────

interface PmfSignalsCardProps {
  callIntel: CallIntelligenceResponse;
}

function PmfSignalsCard({ callIntel }: PmfSignalsCardProps) {
  // /10 score row with traffic-light fill (green ≥7 · amber ≥4 · red <4)
  const scoreRow = (label: string, value: number) => {
    const fill =
      value >= 7 ? "#10B981" : value >= 4 ? "#F59E0B" : "#EF4444";
    return (
      <div key={label}>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs text-ink-secondary">{label}</span>
          <span className="text-xs font-extrabold text-ink">{value}/10</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-border">
          <div
            className="h-full rounded-full transition-all"
            style={{ width: `${value * 10}%`, background: fill }}
          />
        </div>
      </div>
    );
  };

  const pmf = callIntel.pmf_signals as Record<string, unknown> | undefined;
  const coachingInsight = pmf?.coaching_insight as Record<string, string> | undefined;
  const forRep = coachingInsight?.for_rep;
  const hesitation = pmf?.hesitation_signals as
    | Array<string | Record<string, unknown>>
    | undefined;
  const momentum = callIntel.deal_momentum || "";
  const overall = callIntel.overall_pmf_signal || "";

  const momentumChipStyles =
    momentum === "accelerating"
      ? { background: "#ECFDF5", color: "#047857", borderColor: "#A7F3D0" }
      : momentum === "steady"
      ? { background: "#EFF6FF", color: "#1D4ED8", borderColor: "#BFDBFE" }
      : momentum === "decelerating"
      ? { background: "#FFFBEB", color: "#B45309", borderColor: "#FDE68A" }
      : { background: "#FEF2F2", color: "#B91C1C", borderColor: "#FECACA" };

  const overallChipStyles =
    overall === "strong"
      ? { background: "#ECFDF5", color: "#047857", borderColor: "#A7F3D0" }
      : overall === "moderate"
      ? { background: "#EFF6FF", color: "#1D4ED8", borderColor: "#BFDBFE" }
      : overall === "weak"
      ? { background: "#FFFBEB", color: "#B45309", borderColor: "#FDE68A" }
      : { background: "#F8F8FC", color: "#7C7C8A", borderColor: "#EBEBF0" };

  return (
    <div className="mb-5">
      <div className="mb-3 text-[11px] font-extrabold uppercase tracking-wider text-ink-secondary">
        PMF Signals
      </div>

      {/* Score bars (P10 schema uses /10) */}
      <div className="mb-3 space-y-2.5">
        {callIntel.pull_score != null && scoreRow("Pull Score", callIntel.pull_score)}
        {callIntel.pain_fit_score != null && scoreRow("Pain Fit", callIntel.pain_fit_score)}
        {callIntel.icp_fit_score != null && scoreRow("ICP Fit", callIntel.icp_fit_score)}
      </div>

      {/* Badges row */}
      <div className="mb-3 flex flex-wrap gap-1.5">
        {momentum && (
          <span
            className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-extrabold"
            style={momentumChipStyles}
          >
            {momentum === "accelerating"
              ? "↑"
              : momentum === "steady"
              ? "→"
              : momentum === "decelerating"
              ? "↓"
              : "●"}{" "}
            {momentum.charAt(0).toUpperCase() + momentum.slice(1)}
          </span>
        )}
        {overall && (
          <span
            className="inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-extrabold"
            style={overallChipStyles}
          >
            PMF: {overall.charAt(0).toUpperCase() + overall.slice(1)}
          </span>
        )}
      </div>

      {/* Committed-next-step pill */}
      {callIntel.has_committed_next_step != null && (
        <div className="mb-3 flex items-center gap-2 rounded-md bg-surface-soft p-2">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${
              callIntel.has_committed_next_step ? "bg-emerald-500" : "bg-ink-light/40"
            }`}
          />
          <span className="text-xs text-ink-secondary">
            {callIntel.has_committed_next_step
              ? `Committed next step${
                  callIntel.next_step_type ? `: ${callIntel.next_step_type}` : ""
                }`
              : "No committed next step"}
          </span>
        </div>
      )}

      {/* Coaching Insight */}
      {forRep && (
        <div className="mb-3">
          <p className="mb-1 text-xs text-ink-secondary">Coaching Insight</p>
          <p className="text-xs leading-relaxed text-ink-secondary/90">{forRep}</p>
        </div>
      )}

      {/* Hesitation Signals */}
      {hesitation && hesitation.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs text-ink-secondary">Hesitation Signals</p>
          <div className="space-y-1">
            {hesitation.map((h, i) => {
              const text =
                typeof h === "string"
                  ? h
                  : ((h as Record<string, unknown>)?.signal as string) ||
                    JSON.stringify(h);
              return (
                <div key={i} className="flex items-start gap-1.5">
                  <span className="mt-px shrink-0 text-xs text-amber-500">•</span>
                  <span className="text-xs leading-relaxed text-ink-secondary/90">
                    {text}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Signals — editable list per tone
// ────────────────────────────────────────────────────────────────────────────

interface SignalListProps {
  label: string;
  tone: "green" | "red";
  items: string[];
  onChange: (next: string[]) => void;
  addLabel: string;
}

function SignalList({ label, tone, items, onChange, addLabel }: SignalListProps) {
  const toneColor = tone === "green" ? "#00B87C" : "#E5484D";
  const toneBg = tone === "green" ? "#E6F9F1" : "#FEECEE";
  return (
    <div>
      <div
        className="mb-1.5 flex items-center justify-between text-[11px] font-extrabold uppercase tracking-wider"
        style={{ color: toneColor }}
      >
        <span>{label}</span>
        <span className="text-[10px] font-bold text-ink-secondary">{items.length}</span>
      </div>
      {items.length === 0 && (
        <div className="mb-1.5 text-[12px] italic text-ink-light">No signals yet.</div>
      )}
      {items.map((s, i) => (
        <div key={i} className="flex items-center gap-2 py-1.5">
          <span
            className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: toneColor }}
          />
          <div className="min-w-0 flex-1">
            <EditableField
              value={s}
              onChange={(v) => onChange(items.map((it, j) => (j === i ? v : it)))}
              placeholder="Signal text..."
              compact
            />
          </div>
          <button
            onClick={() => onChange(items.filter((_, j) => j !== i))}
            className="opacity-40 hover:opacity-100"
            title="Remove"
          >
            <Trash2 className="h-3.5 w-3.5 text-brand-red" />
          </button>
        </div>
      ))}
      <button
        onClick={() => onChange([...items, ""])}
        className="mt-1 flex items-center gap-1.5 text-xs font-extrabold"
        style={{ color: toneColor }}
      >
        <Plus className="h-3 w-3" />
        {addLabel}
      </button>
      {void toneBg}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// DiscoveryField row — read-only, with confidence badge + quote
// ────────────────────────────────────────────────────────────────────────────

function DiscoveryFieldRow({ field }: { field: CrmField }) {
  const conf = field.confidence;
  const tone =
    conf === "definitive" ? "#00B87C" : conf === "indicative" ? "#F5A623" : "#7C7C8A";
  const toneBg =
    conf === "definitive" ? "#E6F9F1" : conf === "indicative" ? "#FFF8E8" : "#F8F8FC";
  return (
    <div className="rounded-md border border-surface-border bg-surface-soft px-3 py-2.5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-[11px] font-extrabold uppercase tracking-wider text-ink-secondary">
          {field.name}
        </span>
        <span
          className="rounded px-1.5 py-[1px] text-[10px] font-extrabold uppercase"
          style={{ background: toneBg, color: tone }}
        >
          {conf}
        </span>
      </div>
      <div className="text-[13px] font-medium text-ink">{field.value || "—"}</div>
      {field.quote && (
        <div className="mt-1 text-[11px] italic text-ink-light">
          “{field.quote}”
          {field.speaker && <span className="ml-1 not-italic font-semibold text-ink-secondary">— {field.speaker}</span>}
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Tab: Transcript
// ────────────────────────────────────────────────────────────────────────────

interface TranscriptTabProps {
  transcript: TranscriptResponse | null;
  allSpeakerLabels: string[];
  speakerDisplay: (label: string | undefined) => string;
  speakerRole: (label: string | undefined) => "Rep" | "Prospect";
  onEditSegment: (idx: number, text: string) => void;
  onReset: () => void;
  resetting: boolean;
  isEdited: boolean;
  meetingId: string | undefined;
  teamId: string | undefined;
  onSpeakerAssigned: () => void;
}

function TranscriptTab({
  transcript,
  allSpeakerLabels,
  speakerDisplay,
  speakerRole,
  onEditSegment,
  onReset,
  resetting,
  isEdited,
  meetingId,
  teamId,
  onSpeakerAssigned,
}: TranscriptTabProps) {
  if (!transcript) {
    return (
      <div className="rounded-xl border border-dashed border-surface-border bg-white px-6 py-10 text-center text-xs text-ink-secondary">
        Transcript not yet available.
      </div>
    );
  }

  if (transcript.segments.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-surface-border bg-white px-6 py-10 text-center text-xs text-ink-secondary">
        Transcript is empty.
      </div>
    );
  }

  return (
    <div>
      {isEdited && (
        <div className="mb-4 flex items-center justify-between rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
          <div className="text-[12px] text-amber-700">
            <span className="font-extrabold">Transcript edited.</span> Your edits override the
            AI-generated text on this page, in summaries, and in CRM pushes.
          </div>
          <button
            onClick={onReset}
            disabled={resetting}
            className="rounded-md border border-amber-300 bg-white px-3 py-1 text-[11px] font-extrabold text-amber-700 hover:bg-amber-100 disabled:opacity-50"
          >
            {resetting ? "Resetting…" : "Reset transcript to AI output"}
          </button>
        </div>
      )}
      {transcript.segments.map((seg, i) => {
        const color = speakerColor(seg.speaker, allSpeakerLabels);
        const name = speakerDisplay(seg.speaker);
        const role = speakerRole(seg.speaker);
        // Compose a stable React key. TranscriptSegment.id is unreliable
        // — the backend schema defaults it to 0, so the on-disk JSON
        // typically has every segment at id=0 (duplicate keys would
        // collapse the whole list onto one rendered row). Use the start
        // timestamp (which IS unique per segment) plus index as a
        // belt-and-braces fallback.
        const segKey = `seg-${seg.start ?? i}-${i}`;
        return (
          <div key={segKey} className="mb-4 flex gap-3.5">
            <div className="w-9 shrink-0 pt-0.5 text-right">
              <span className="text-[11px] font-semibold tabular-nums text-ink-secondary">
                {fmtSegTime(seg)}
              </span>
            </div>
            <div className="w-[3px] shrink-0 rounded-sm" style={{ background: color }} />
            <div className="flex-1 rounded-xl border border-surface-border bg-white px-3.5 py-3 shadow-sm">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs font-extrabold" style={{ color }}>
                  {name}
                </span>
                <span className="rounded bg-surface-soft px-1.5 py-[1px] text-[10px] font-semibold text-ink-secondary">
                  {role}
                </span>
                {meetingId && (
                  <SpeakerMappingDropdown
                    meetingId={meetingId}
                    teamId={teamId}
                    diarizationLabel={seg.speaker}
                    currentName={name}
                    onAssigned={onSpeakerAssigned}
                  />
                )}
              </div>
              <EditableField value={seg.text} onChange={(v) => onEditSegment(i, v)} multiline />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Tab: CRM Update
// ────────────────────────────────────────────────────────────────────────────

interface CrmTabProps {
  meeting: MeetingDetail;
  crmConnections: CrmConnection[];
  crmPushResults: CrmPushResult[];
  callIntel: CallIntelligenceResponse | null;
  discoveryFields: CrmField[] | null;
  activeCrmProvider: string;
  setActiveCrmProvider: (p: string) => void;
  crmAction: "pushing" | "skipping" | null;
  regenerating: boolean;
  crmExtractError: string | null;
  editingField: string | null;
  editingValue: string;
  setEditingField: (k: string | null) => void;
  setEditingValue: (v: string) => void;
  onPush: (provider: string) => void;
  onSkip: (provider: string) => void;
  onEditField: (provider: string, fieldKey: string, value: string) => void;
  onRegenerate: () => void;
}

interface DisplayField {
  key: string;
  label: string;
  value: string;
  section?: string;
}

function extractDisplayFields(source: Record<string, unknown>): DisplayField[] {
  const out: DisplayField[] = [];
  for (const [key, value] of Object.entries(source)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [subKey, subVal] of Object.entries(value as Record<string, unknown>)) {
        if (subVal && subVal !== "null" && subVal !== "Unknown") {
          out.push({
            key: `${key}.${subKey}`,
            label: subKey.replace(/_/g, " "),
            value: String(subVal),
            section: key,
          });
        }
      }
    } else if (key === "call_summary" && value) {
      out.push({ key: "call_summary", label: "Call Summary", value: String(value) });
    }
  }
  return out;
}

function timeAgoLocal(iso: string): string {
  const then = new Date(iso).getTime();
  const diffSec = Math.floor((Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

function CrmTab({
  meeting,
  crmConnections,
  crmPushResults,
  callIntel,
  discoveryFields,
  activeCrmProvider,
  setActiveCrmProvider,
  crmAction,
  regenerating,
  crmExtractError,
  editingField,
  editingValue,
  setEditingField,
  setEditingValue,
  onPush,
  onSkip,
  onEditField,
  onRegenerate,
}: CrmTabProps) {
  // ── State gates (intelligence still running / failed) ──
  if (
    meeting.intelligence_status !== "completed" &&
    meeting.intelligence_status !== "failed"
  ) {
    return (
      <div className="rounded-xl border border-surface-border bg-white px-8 py-10 text-center">
        <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-brand border-t-transparent" />
        <h3 className="mb-2 text-sm font-extrabold text-ink">AI Analysis in Progress</h3>
        <p className="text-xs text-ink-secondary">
          CRM data will be available once the intelligence analysis is complete.
        </p>
      </div>
    );
  }

  const connectedProviders = crmConnections
    .filter((c) => c.is_active)
    .map((c) => c.provider);

  if (connectedProviders.length === 0) {
    return (
      <div className="rounded-xl border border-surface-border bg-white px-8 py-10 text-center">
        <div className="mx-auto mb-4 inline-flex h-12 w-12 items-center justify-center rounded-full bg-surface-soft">
          <ChevronRight className="h-6 w-6 text-ink-light" />
        </div>
        <h3 className="mb-2 text-sm font-extrabold text-ink">No CRM Connected</h3>
        <p className="mb-4 text-xs text-ink-secondary">
          Connect HubSpot or Zoho CRM to push call insights automatically.
        </p>
        <a
          href="/integrations"
          className="inline-block rounded-md bg-brand px-5 py-2.5 text-xs font-extrabold text-white"
        >
          Go to Integrations
        </a>
      </div>
    );
  }

  const provider = connectedProviders.includes(activeCrmProvider)
    ? activeCrmProvider
    : connectedProviders[0];
  const providerLabel = provider === "hubspot" ? "HubSpot" : "Zoho CRM";
  const pushResult = crmPushResults.find((r) => r.provider === provider);
  const isProcessing = pushResult?.status === "processing";

  // Build the field list to display.
  const dataSource = pushResult?.edited_data || pushResult?.pushed_data;
  const hasPushedData =
    !!dataSource && typeof dataSource === "object" && Object.keys(dataSource).length > 0;
  let displayFields: DisplayField[] = [];
  if (hasPushedData) {
    displayFields = extractDisplayFields(dataSource as Record<string, unknown>);
  } else if (callIntel?.crm_extraction && Object.keys(callIntel.crm_extraction).length > 0) {
    displayFields = extractDisplayFields(
      callIntel.crm_extraction as Record<string, unknown>,
    );
  } else if (discoveryFields && discoveryFields.length > 0) {
    displayFields = discoveryFields.map((f) => ({
      key: `discovery.${f.name}`,
      label: f.name,
      value: f.value,
    }));
  }
  const hasFields = displayFields.length > 0;

  // Push button state.
  let pushDisabled = true;
  let pushDisabledReason = "";
  if (!hasFields) {
    pushDisabledReason = "No CRM fields extracted for this call";
  } else if (isProcessing) {
    pushDisabledReason = "Pushing...";
  } else if (crmAction !== null) {
    pushDisabledReason = "Please wait...";
  } else if (pushResult?.status === "completed" && !pushResult?.edited_data) {
    pushDisabledReason = `Already pushed to ${providerLabel}`;
  } else {
    pushDisabled = false;
  }

  return (
    <>
      {/* Provider sub-tabs */}
      {connectedProviders.length > 1 && (
        <div className="mb-4 flex gap-1">
          {connectedProviders.map((p) => {
            const label = p === "hubspot" ? "HubSpot" : "Zoho CRM";
            const active = (activeCrmProvider || connectedProviders[0]) === p;
            return (
              <button
                key={p}
                onClick={() => setActiveCrmProvider(p)}
                className={`rounded-md px-3 py-1.5 text-xs font-extrabold ${
                  active ? "bg-brand text-white" : "bg-surface-soft text-ink-secondary"
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}

      <Card>
        {/* Status banners */}
        {isProcessing && (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-brand/20 bg-brand-light p-3">
            <div className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-brand border-t-transparent" />
            <span className="text-sm font-extrabold text-brand">Pushing to {providerLabel}...</span>
          </div>
        )}
        {pushResult?.status === "completed" && (
          <div className="mb-4 flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50 p-3">
            <div className="flex items-center gap-2">
              <Check className="h-4 w-4 text-emerald-600" />
              <span className="text-sm font-extrabold text-emerald-700">
                Pushed to {providerLabel}
              </span>
              {pushResult.pushed_at && (
                <span className="text-xs text-emerald-600/70">
                  {timeAgoLocal(pushResult.pushed_at)}
                </span>
              )}
            </div>
            {pushResult.crm_record_url && (
              <a
                href={pushResult.crm_record_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs font-extrabold text-brand"
              >
                View in {providerLabel} →
              </a>
            )}
          </div>
        )}
        {pushResult?.status === "failed" && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3">
            <div className="mb-1 flex items-center gap-2">
              <X className="h-4 w-4 text-red-600" />
              <span className="text-sm font-extrabold text-red-700">Push failed</span>
            </div>
            {pushResult.error_message && (
              <p className="ml-6 text-xs text-red-600">{pushResult.error_message}</p>
            )}
          </div>
        )}
        {pushResult?.status === "skipped" && (
          <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3">
            <span className="text-sm text-amber-700">CRM push was skipped for this call</span>
          </div>
        )}
        {crmExtractError && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3">
            <div className="mb-1 flex items-center gap-2">
              <X className="h-4 w-4 text-red-600" />
              <span className="text-sm font-extrabold text-red-700">CRM extraction failed</span>
            </div>
            <p className="ml-6 text-xs text-red-600">{crmExtractError}</p>
          </div>
        )}

        {/* Contact + deal IDs */}
        {pushResult?.status === "completed" &&
          (pushResult.crm_contact_id || pushResult.crm_deal_id) && (
            <div className="mb-4 flex gap-4 text-xs text-ink-secondary">
              {pushResult.crm_contact_id && (
                <span>
                  Contact:{" "}
                  <span className="font-mono text-ink">{pushResult.crm_contact_id}</span>
                </span>
              )}
              {pushResult.crm_deal_id && (
                <span>
                  Deal: <span className="font-mono text-ink">{pushResult.crm_deal_id}</span>
                </span>
              )}
            </div>
          )}

        {/* Deal intelligence */}
        {callIntel &&
          (callIntel.validated_stage ||
            callIntel.crm_stage_label ||
            callIntel.deal_momentum ||
            callIntel.has_committed_next_step != null) && (
            <div className="mb-4 rounded-md border border-surface-border bg-surface-soft p-3">
              <div className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wider text-ink-secondary">
                Deal Intelligence
              </div>
              {(callIntel.validated_stage || callIntel.crm_stage_label) && (
                <div className="mb-2.5 flex items-center gap-3">
                  {callIntel.validated_stage && (
                    <div className="min-w-0 flex-1">
                      <p className="mb-0.5 text-[10px] text-ink-secondary">AI Validated Stage</p>
                      <span className="inline-flex items-center rounded-full border border-brand/20 bg-brand-light px-2 py-0.5 text-xs font-extrabold text-brand">
                        {callIntel.validated_stage}
                      </span>
                    </div>
                  )}
                  {callIntel.validated_stage && callIntel.crm_stage_label && (
                    <ChevronRight className="h-4 w-4 shrink-0 text-ink-light" />
                  )}
                  {callIntel.crm_stage_label && (
                    <div className="min-w-0 flex-1">
                      <p className="mb-0.5 text-[10px] text-ink-secondary">CRM Stage</p>
                      <span className="inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-extrabold text-blue-700">
                        {callIntel.crm_stage_label}
                      </span>
                    </div>
                  )}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                {callIntel.deal_momentum && (
                  <span
                    className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-extrabold"
                    style={{
                      background:
                        callIntel.deal_momentum === "accelerating"
                          ? "#ECFDF5"
                          : callIntel.deal_momentum === "steady"
                          ? "#EFF6FF"
                          : callIntel.deal_momentum === "decelerating"
                          ? "#FFFBEB"
                          : "#FEF2F2",
                      borderColor:
                        callIntel.deal_momentum === "accelerating"
                          ? "#A7F3D0"
                          : callIntel.deal_momentum === "steady"
                          ? "#BFDBFE"
                          : callIntel.deal_momentum === "decelerating"
                          ? "#FDE68A"
                          : "#FECACA",
                      color:
                        callIntel.deal_momentum === "accelerating"
                          ? "#047857"
                          : callIntel.deal_momentum === "steady"
                          ? "#1D4ED8"
                          : callIntel.deal_momentum === "decelerating"
                          ? "#B45309"
                          : "#B91C1C",
                    }}
                  >
                    {callIntel.deal_momentum === "accelerating"
                      ? "↑"
                      : callIntel.deal_momentum === "steady"
                      ? "→"
                      : callIntel.deal_momentum === "decelerating"
                      ? "↓"
                      : "●"}{" "}
                    {callIntel.deal_momentum.charAt(0).toUpperCase() + callIntel.deal_momentum.slice(1)}
                  </span>
                )}
                {callIntel.has_committed_next_step != null && (
                  <span
                    className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-extrabold ${
                      callIntel.has_committed_next_step
                        ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                        : "border-surface-border bg-surface-soft text-ink-secondary"
                    }`}
                  >
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        callIntel.has_committed_next_step ? "bg-emerald-500" : "bg-ink-light/40"
                      }`}
                    />
                    {callIntel.has_committed_next_step
                      ? `Next step${callIntel.next_step_type ? `: ${callIntel.next_step_type}` : ""}`
                      : "No next step"}
                  </span>
                )}
              </div>
            </div>
          )}

        {/* Field list */}
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-extrabold text-ink">
            {hasPushedData ? "CRM Data" : "Extracted Fields"}
          </h2>
          {hasFields && (
            <button
              onClick={onRegenerate}
              disabled={regenerating}
              className="text-xs font-semibold text-brand hover:underline disabled:opacity-50"
            >
              {regenerating ? "Generating..." : "Regenerate"}
            </button>
          )}
        </div>

        {hasFields ? (
          <div className="mb-4 space-y-2">
            {displayFields.map((field) => (
              <div
                key={field.key}
                className="rounded-md border border-surface-border/60 px-3 py-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <span className="text-xs capitalize text-ink-secondary">{field.label}</span>
                    {editingField === field.key ? (
                      <div className="mt-1 flex items-center gap-1">
                        {field.key === "call_summary" ? (
                          <textarea
                            value={editingValue}
                            onChange={(e) => setEditingValue(e.target.value)}
                            className="w-full resize-y rounded border border-surface-border px-2 py-1 text-xs"
                            rows={6}
                            autoFocus
                          />
                        ) : (
                          <input
                            value={editingValue}
                            onChange={(e) => setEditingValue(e.target.value)}
                            className="flex-1 rounded border border-surface-border px-2 py-1 text-xs"
                            autoFocus
                          />
                        )}
                        <button
                          onClick={() => onEditField(provider, field.key, editingValue)}
                          className="whitespace-nowrap text-xs font-extrabold text-emerald-600"
                        >
                          Save
                        </button>
                        <button
                          onClick={() => setEditingField(null)}
                          className="whitespace-nowrap text-xs text-ink-secondary"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <p
                        className={`mt-0.5 text-sm text-ink ${
                          field.key === "call_summary" ? "whitespace-pre-line text-xs leading-relaxed" : ""
                        }`}
                      >
                        {field.value}
                      </p>
                    )}
                  </div>
                  {editingField !== field.key && (
                    <button
                      onClick={() => {
                        setEditingField(field.key);
                        setEditingValue(field.value);
                      }}
                      className="shrink-0 text-xs font-semibold text-brand hover:underline"
                    >
                      Edit
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="mb-4">
            <p className="mb-3 text-sm italic text-ink-secondary">
              No CRM fields extracted for this call.
            </p>
            <button
              onClick={onRegenerate}
              disabled={regenerating}
              className="rounded-md bg-brand px-4 py-2 text-xs font-extrabold text-white disabled:opacity-50"
            >
              {regenerating ? "Generating..." : "Extract CRM Data"}
            </button>
          </div>
        )}

        {/* Push / re-push / retry */}
        {hasFields && (
          <div className="space-y-2 border-t border-surface-border pt-3">
            {pushDisabled ? (
              <div className="space-y-2">
                <button
                  disabled
                  className="w-full cursor-not-allowed rounded-md bg-brand/40 py-2.5 text-sm font-extrabold text-white"
                >
                  {isProcessing
                    ? "Pushing..."
                    : pushResult?.status === "completed" && pushResult?.edited_data
                    ? `Re-push to ${providerLabel}`
                    : `Push to ${providerLabel}`}
                </button>
                <p className="text-center text-xs text-ink-secondary">{pushDisabledReason}</p>
              </div>
            ) : (
              <button
                onClick={() => onPush(provider)}
                disabled={crmAction !== null}
                className={`w-full rounded-md py-2.5 text-sm font-extrabold text-white disabled:opacity-50 ${
                  pushResult?.edited_data
                    ? "bg-amber-500"
                    : pushResult?.status === "failed"
                    ? "bg-red-500"
                    : "bg-brand"
                }`}
              >
                {crmAction === "pushing"
                  ? "Pushing..."
                  : pushResult?.edited_data
                  ? `Re-push to ${providerLabel}`
                  : pushResult?.status === "failed"
                  ? "Retry Push"
                  : pushResult?.status === "skipped"
                  ? "Push Now"
                  : `Push to ${providerLabel}`}
              </button>
            )}

            {(!pushResult || pushResult.status === "pending") && (
              <button
                onClick={() => onSkip(provider)}
                disabled={crmAction !== null}
                className="w-full py-2 text-xs text-ink-secondary disabled:opacity-50"
              >
                {crmAction === "skipping" ? "Skipping..." : "Skip CRM push"}
              </button>
            )}
          </div>
        )}
      </Card>
    </>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Card + header helpers
// ────────────────────────────────────────────────────────────────────────────

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-5 rounded-xl border border-surface-border bg-white px-5 py-5 shadow-sm">
      {children}
    </div>
  );
}

/**
 * Tag every section with its underlying data source so the user can see
 * at a glance whether the content (and any edits) flow through P5
 * (summary.json — read-only on this page) or P10 (memoir extraction —
 * editable via PATCH /api/meetings/{id}/memoir).
 */
function SourceBadge({ source }: { source: "P5" | "P10" }) {
  const isP10 = source === "P10";
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-full px-2 py-[2px] text-[10px] font-extrabold uppercase tracking-wider"
      style={{
        background: isP10 ? "#EDEDFC" : "#FFF3EE",
        color: isP10 ? "#5C5CE0" : "#E8683A",
      }}
      title={
        isP10
          ? "Sourced from P10 memoir extraction — edits PATCH /api/meetings/{id}/memoir"
          : "Sourced from P5 summary.json — read-only on this page"
      }
    >
      {source}
    </span>
  );
}

function CardHeader({
  title,
  editHint = false,
  source,
  readOnly = false,
}: {
  title: string;
  editHint?: boolean;
  source?: "P5" | "P10";
  readOnly?: boolean;
}) {
  return (
    <div className="mb-3.5 flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-2">
        {source && <SourceBadge source={source} />}
        <h3 className="m-0 truncate text-sm font-extrabold text-ink">{title}</h3>
        {readOnly && (
          <span className="rounded bg-surface-soft px-1.5 py-[1px] text-[10px] font-bold uppercase text-ink-secondary">
            Read-only
          </span>
        )}
      </div>
      {editHint && !readOnly && (
        <span className="flex shrink-0 items-center gap-1 rounded bg-brand-light px-2 py-[2px] text-[11px] font-semibold text-brand">
          <Edit3 className="h-2.5 w-2.5" /> Click to edit
        </span>
      )}
    </div>
  );
}

