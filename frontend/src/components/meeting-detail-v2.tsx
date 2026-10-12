"use client";

/**
 * Meeting detail page v2 — revamp build per plan/ui_revamp_detail_page.md.
 *
 * D1 — Data layer: ports the essential api.* calls from v1
 *      (meeting, transcript, memoir, intel, coaching, audio, processing
 *       status, deal timeline). The advanced workflows that aren't shown
 *       in the mockup (CRM push tab, email dialog, share approval,
 *       speaker editing, multi-version transcript switcher) stay in v1
 *       and are linked via the More menu (D2).
 * D2 — Sticky top toolbar: back, title block, status pill, play, share,
 *      More menu (route to /meetings/v1/[id] for legacy workflows).
 * D3 — Left column transcript: wraps EditableTranscript in a mockup-style
 *      speaker color-bar layout.
 * D4 — Right sidebar: AI Summary (SummaryEditor), Key Moments, Action
 *      Items (EditableActionItems), Topics, Coaching, Deal Timeline,
 *      Participants.
 * D5 — CRM drawer: deferred — More menu routes to v1 page for the
 *      tabbed CRM push workspace.
 * D6 — Banners + edge states: loading skeleton, error card, processing
 *      placeholder, fast-email-only.
 * D7 — Polish: focus rings, Esc-closes-share, audio scrubber.
 */

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Check,
  Download,
  Link2,
  MoreHorizontal,
  Pause,
  Play,
  Share2,
  Sparkles,
} from "lucide-react";

import { api } from "@/lib/api";
import type {
  ActionItem,
  CallIntelligenceResponse,
  CoachingResponse,
  DealTimelineResponse,
  MeetingDetail,
  MemoirResponse,
  SignalData,
  SpeakerProfile,
  TranscriptResponse,
  TranscriptionVersion,
} from "@/lib/types";
import { EditableActionItems } from "@/components/editable-action-items";
import { EditableSignals } from "@/components/editable-signals";
import { EditableTranscript } from "@/components/editable-transcript";
import { PlatformBadge, platformFromSourceOrUrl } from "@/components/ui/platform-badge";
import { Tag } from "@/components/ui/tag";

const SummaryEditor = dynamic(
  () => import("@/components/summary-editor").then((m) => m.SummaryEditor),
  { ssr: false },
);


function dateLabel(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function timeLabel(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function durationLabel(seconds: number | null): string {
  if (!seconds) return "—";
  const m = Math.round(seconds / 60);
  return `${m} min`;
}

function fmtTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

interface ProcessingStatusShape {
  transcription?: string;
  summary?: string;
  intelligence?: string;
  crm_extraction?: string;
  active_stage?: string;
  stages?: Record<string, { percent: number; label: string }>;
}

export function MeetingDetailV2() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = params?.id ?? "";

  // ── D1 data layer ────────────────────────────────────────────────
  const [meeting, setMeeting] = useState<MeetingDetail | null>(null);
  const [memoir, setMemoir] = useState<MemoirResponse | null>(null);
  const [coaching, setCoaching] = useState<CoachingResponse | null>(null);
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);
  const [versions, setVersions] = useState<TranscriptionVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [callIntel, setCallIntel] = useState<CallIntelligenceResponse | null>(null);
  const [dealTimeline, setDealTimeline] = useState<DealTimelineResponse | null>(null);
  const [processingStatus, setProcessingStatus] = useState<ProcessingStatusShape | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Audio
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [audioUrls, setAudioUrls] = useState<Record<string, string>>({});
  const [audioSrc, setAudioSrc] = useState<string | null>(null);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [audioTime, setAudioTime] = useState(0);
  const [audioDuration, setAudioDuration] = useState(0);

  // Top-bar dropdowns
  const [shareOpen, setShareOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [generatingLink, setGeneratingLink] = useState(false);
  const shareRef = useRef<HTMLDivElement>(null);
  const moreRef = useRef<HTMLDivElement>(null);

  // Speaker editing (pass-through state for EditableTranscript)
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [speakerInput, setSpeakerInput] = useState("");
  const [teamSpeakers, setTeamSpeakers] = useState<SpeakerProfile[]>([]);
  const [savingSpeaker, setSavingSpeaker] = useState(false);
  const speakerInputRef = useRef<HTMLInputElement>(null);

  // Initial-jump-to timestamp from ?t=
  const initialT = useMemo(() => {
    const raw = searchParams?.get("t");
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  }, [searchParams]);

  // ── Initial data load + processing-status polling ────────────────
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let pollInterval: ReturnType<typeof setInterval> | null = null;

    async function load() {
      try {
        const m = await api.getMeeting(id);
        if (cancelled) return;
        setMeeting(m);

        // Versions + version-pinned content
        api.getVersions(id).then((v) => {
          if (cancelled || v.versions.length === 0) return;
          setVersions(v.versions);
          const isIncomplete = m.status === "failed" || ["pending", "scheduled", "joining", "recording", "processing", "waiting_for_transcription"].includes(m.status);
          const defaultVer =
            isIncomplete && v.versions.length > 1
              ? v.versions[v.versions.length - 2].version
              : v.versions[v.versions.length - 1].version;
          setSelectedVersion(defaultVer);
          api.getTranscript(id, defaultVer).then((t) => !cancelled && setTranscript(t)).catch(() => {});
          api.getMemoir(id, defaultVer).then((mem) => !cancelled && setMemoir(mem)).catch(() => {});
          api.getCoaching(id, defaultVer).then((c) => !cancelled && setCoaching(c)).catch(() => {});
        }).catch(() => {
          if (m.has_transcript) api.getTranscript(id).then((t) => !cancelled && setTranscript(t)).catch(() => {});
          if (m.has_memoir) api.getMemoir(id).then((mem) => !cancelled && setMemoir(mem)).catch(() => {});
        });

        // Audio (multi-version, default merged/preprocessed first)
        if (m.has_recording) {
          api.getAudioUrls(id).then((urls) => {
            if (cancelled) return;
            setAudioUrls(urls);
            const defaultVersion = urls.preprocessed ? "preprocessed" : urls.raw ? "raw" : Object.keys(urls)[0];
            if (defaultVersion) setAudioSrc(urls[defaultVersion]);
          }).catch(() => {
            api.getRecordingPresignedUrl(id).then((u) => !cancelled && setAudioSrc(u)).catch(() => {});
          });
        }

        // Intelligence + deal timeline (non-blocking)
        api.getCallIntelligence(id).then((ci) => !cancelled && setCallIntel(ci)).catch(() => {});
        if (m.memoir_deal_id) {
          api.getDealTimeline(m.memoir_deal_id).then((tl) => {
            if (!cancelled && tl && tl.entries.length > 1) setDealTimeline(tl);
          }).catch(() => {});
        }
      } catch (e: unknown) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : "Failed to load meeting";
        setError(msg);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();

    // Poll processing status every 4s while the meeting is still pre-terminal.
    // Terminal states (completed / failed / fast_email_only) live on the
    // Meeting itself (we read meeting.status); this poll just surfaces the
    // "active stage" pill in the toolbar while work is in flight.
    const pollProcessing = async () => {
      try {
        const status = await api.getProcessingStatus(id);
        if (cancelled) return;
        setProcessingStatus(status);
      } catch {
        // ignore
      }
    };
    pollProcessing();
    pollInterval = setInterval(pollProcessing, 4000);

    return () => {
      cancelled = true;
      if (pollInterval) clearInterval(pollInterval);
    };
  }, [id]);

  // ── Audio playback ───────────────────────────────────────────────
  useEffect(() => {
    const el = audioRef.current;
    if (!el || !audioSrc) return;
    el.src = audioSrc;
    el.load();
    if (initialT != null) {
      const onMeta = () => {
        el.currentTime = initialT;
        el.removeEventListener("loadedmetadata", onMeta);
      };
      el.addEventListener("loadedmetadata", onMeta);
    }
  }, [audioSrc, initialT]);

  const togglePlay = () => {
    const el = audioRef.current;
    if (!el || !audioSrc) return;
    if (audioPlaying) el.pause();
    else el.play().catch(() => {});
  };

  const seekTo = (sec: number) => {
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = sec;
    el.play().catch(() => {});
  };

  // Click handler for key moments / transcript timestamps
  const handleSeek = (sec: number) => seekTo(sec);

  // Share + More outside-click handling
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (shareRef.current && !shareRef.current.contains(e.target as Node)) setShareOpen(false);
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) setMoreOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Esc closes any open dropdown
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setShareOpen(false);
        setMoreOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const handleCopyLink = async () => {
    setGeneratingLink(true);
    try {
      const result = await api.createShareLink(id);
      const publicUrl = `${window.location.origin}/shared/${result.share_token}`;
      await navigator.clipboard.writeText(publicUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      try {
        await navigator.clipboard.writeText(window.location.href);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch {
        // noop
      }
    } finally {
      setGeneratingLink(false);
    }
  };

  // ── Render: loading / error states ───────────────────────────────
  if (loading) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-surface-soft py-16 font-sans text-ink-secondary">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-brand border-t-transparent" />
        <span className="text-sm font-medium">Loading meeting…</span>
      </div>
    );
  }

  if (error || !meeting) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-surface-soft py-16 font-sans">
        <h1 className="text-lg font-extrabold text-ink">Meeting not found</h1>
        {error && <p className="text-sm text-ink-secondary">{error}</p>}
        <button onClick={() => router.push("/meetings")} className="rounded-button bg-brand px-4 py-2 text-sm font-bold text-white hover:bg-brand-dark">
          Back to Meetings
        </button>
      </div>
    );
  }

  const platform = platformFromSourceOrUrl(meeting.source, meeting.meeting_url);
  const summaryNarrative = memoir?.narrative_summary ?? "";
  const actionItems: ActionItem[] = memoir?.action_items ?? [];
  const signals: SignalData = memoir?.signals ?? { buying: [], risk: [] };
  const keyMoments = ((callIntel?.summary as Record<string, unknown> | undefined)?.key_moments as Array<Record<string, unknown>> | undefined) || [];
  const topics = ((callIntel?.summary as Record<string, unknown> | undefined)?.topics as string[] | undefined) || [];
  // CoachingResponse buries metrics under `coaching_metrics` — flatten common fields
  const coachingMetrics = (coaching?.coaching_metrics ?? {}) as Record<string, unknown>;
  const cmNum = (key: string): number | null => {
    const v = coachingMetrics[key];
    return typeof v === "number" ? v : null;
  };

  // Status pill
  const statusBucket = (() => {
    const s = meeting.status;
    if (s === "completed") return null;
    if (s === "failed") return { label: "Failed", tone: "red" as const };
    if (s === "fast_email_only") return { label: "Fast email only", tone: "amber" as const };
    if (["pending", "scheduled", "joining", "recording", "processing", "waiting_for_transcription"].includes(s))
      return { label: "Processing", tone: "amber" as const };
    return null;
  })();

  const hasRecording = meeting.has_recording;

  return (
    <div className="flex h-full flex-col bg-surface-soft font-sans text-ink" style={{ fontSize: 14 }}>
      {/* ───────── D2: Sticky top toolbar ───────── */}
      <header className="sticky top-0 z-30 flex flex-col gap-3 border-b border-surface-border bg-white px-6 py-3 lg:px-8">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/meetings")}
            className="flex items-center gap-1.5 rounded-button px-2 py-1.5 text-xs font-bold text-brand transition-colors hover:bg-brand-light"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to Meetings
          </button>
          <div className="flex-1" />

          {/* Play button (circular) */}
          <button
            onClick={togglePlay}
            disabled={!hasRecording || !audioSrc}
            aria-label={audioPlaying ? "Pause" : "Play recording"}
            title={!hasRecording ? "No recording available" : audioPlaying ? "Pause" : "Play recording"}
            className={`flex h-9 w-9 items-center justify-center rounded-full transition-colors ${
              !hasRecording || !audioSrc
                ? "cursor-not-allowed bg-surface-soft text-ink-light"
                : "bg-brand text-white hover:bg-brand-dark"
            }`}
          >
            {audioPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 translate-x-px" />}
          </button>

          {/* Share dropdown */}
          <div className="relative" ref={shareRef}>
            <button
              onClick={() => setShareOpen((v) => !v)}
              className="flex items-center gap-1.5 rounded-button bg-brand-light px-3 py-2 text-xs font-bold text-brand hover:bg-brand-light/80"
            >
              <Share2 className="h-3.5 w-3.5" /> Share
              <ChevronDown className={`h-3 w-3 transition-transform ${shareOpen ? "rotate-180" : ""}`} />
            </button>
            {shareOpen && (
              <div className="absolute right-0 top-full z-40 mt-1 w-60 rounded-card border border-surface-border bg-white py-1.5 shadow-elevated">
                <button
                  onClick={handleCopyLink}
                  disabled={generatingLink}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
                >
                  {copied ? <Check className="h-3.5 w-3.5 text-brand-green" /> : <Link2 className="h-3.5 w-3.5 text-ink-secondary" />}
                  {copied ? "Link copied" : generatingLink ? "Generating link..." : "Copy public share link"}
                </button>
                <button
                  onClick={() => { if (audioSrc) window.open(audioSrc, "_blank"); setShareOpen(false); }}
                  disabled={!audioSrc}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
                >
                  <Download className="h-3.5 w-3.5 text-ink-secondary" />
                  Download recording
                </button>
              </div>
            )}
          </div>

          {/* More menu — links to v1 for legacy workflows */}
          <div className="relative" ref={moreRef}>
            <button
              onClick={() => setMoreOpen((v) => !v)}
              aria-label="More actions"
              className="flex h-9 w-9 items-center justify-center rounded-button text-ink-secondary transition-colors hover:bg-surface-soft hover:text-ink"
            >
              <MoreHorizontal className="h-4 w-4" />
            </button>
            {moreOpen && (
              <div className="absolute right-0 top-full z-40 mt-1 w-64 rounded-card border border-surface-border bg-white py-1.5 shadow-elevated">
                <button
                  onClick={() => router.push(`/meetings/v1/${id}`)}
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft"
                >
                  Push to CRM, Send email, Edit speakers…
                  <ChevronRight className="h-3.5 w-3.5 text-ink-secondary" />
                </button>
                <button
                  onClick={() => router.push(`/meetings/${id}/raw-asr${selectedVersion ? `?version=${selectedVersion}` : ""}`)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft"
                >
                  View raw ASR
                </button>
                <div className="mx-2 my-1 border-t border-surface-border" />
                <button
                  onClick={() => router.push(`/meetings/v1/${id}`)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-brand transition-colors hover:bg-brand-light"
                >
                  Open classic detail page
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Title block */}
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="mb-1.5 flex flex-wrap items-center gap-2">
              <PlatformBadge platform={platform} />
              <span className="text-xs font-medium text-ink-secondary">
                {dateLabel(meeting.started_at)} · {timeLabel(meeting.started_at)} · {durationLabel(meeting.duration_seconds)}
              </span>
              {statusBucket && (
                <Tag tone={statusBucket.tone} label={statusBucket.label} />
              )}
              {meeting.confidence_score != null && (
                <span
                  className={`rounded-md px-2 py-[2px] text-[10px] font-bold ${
                    meeting.confidence_score >= 75
                      ? "bg-brand-green-light text-brand-green"
                      : meeting.confidence_score >= 50
                        ? "bg-brand-amber-light text-brand-amber"
                        : "bg-brand-red-light text-brand-red"
                  }`}
                >
                  {meeting.confidence_score}% conf
                </span>
              )}
            </div>
            <h1 className="truncate text-[22px] font-extrabold leading-tight tracking-tight text-ink">
              {meeting.title || "Untitled meeting"}
            </h1>
            {meeting.primary_participant_name && (
              <div className="mt-1 text-xs font-medium text-ink-secondary">
                With {meeting.primary_participant_name}
                {meeting.primary_participant_company && <span> · {meeting.primary_participant_company}</span>}
              </div>
            )}
          </div>
        </div>

        {/* Audio scrubber strip */}
        {hasRecording && audioSrc && (
          <div className="flex items-center gap-3">
            <span className="w-12 text-[11px] font-semibold tabular-nums text-ink-secondary">{fmtTime(audioTime)}</span>
            <input
              type="range"
              min={0}
              max={Math.max(audioDuration, 0)}
              step={0.1}
              value={audioTime}
              onChange={(e) => seekTo(Number(e.target.value))}
              className="flex-1 accent-brand"
              aria-label="Seek audio"
            />
            <span className="w-12 text-right text-[11px] font-semibold tabular-nums text-ink-secondary">{fmtTime(audioDuration)}</span>
            {Object.keys(audioUrls).length > 1 && (
              <select
                value={Object.entries(audioUrls).find(([, v]) => v === audioSrc)?.[0] ?? ""}
                onChange={(e) => {
                  const key = e.target.value;
                  const next = audioUrls[key];
                  if (next) {
                    setAudioSrc(next);
                  }
                }}
                className="rounded-button border border-surface-border bg-white px-2 py-1 text-[11px] font-semibold text-ink-secondary"
              >
                {Object.keys(audioUrls).map((key) => (
                  <option key={key} value={key}>{key}</option>
                ))}
              </select>
            )}
          </div>
        )}

        {/* Processing strip — shown whenever meeting hasn't reached a terminal state */}
        {!["completed", "failed", "fast_email_only"].includes(meeting.status) && (
          <div className="-mb-1 flex items-center gap-2 rounded-button border border-brand-amber/30 bg-brand-amber-light px-3 py-1.5 text-[11px] font-bold text-brand-amber">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-brand-amber" />
            Processing — {processingStatus?.active_stage || meeting.status}
          </div>
        )}

        {/* Fast email only banner */}
        {meeting.status === "fast_email_only" && (
          <div className="rounded-button border border-brand-amber/30 bg-brand-amber-light px-3 py-2 text-[11px] font-bold text-brand-amber">
            Fast email sent — diarization didn&apos;t complete. <button onClick={() => router.push(`/meetings/v1/${id}`)} className="ml-1 underline">Open classic to retranscribe.</button>
          </div>
        )}
      </header>

      {/* Hidden audio element controlled by the toolbar */}
      {audioSrc && (
        <audio
          ref={audioRef}
          preload="metadata"
          onPlay={() => setAudioPlaying(true)}
          onPause={() => setAudioPlaying(false)}
          onTimeUpdate={(e) => setAudioTime((e.target as HTMLAudioElement).currentTime)}
          onLoadedMetadata={(e) => setAudioDuration((e.target as HTMLAudioElement).duration || 0)}
          onEnded={() => setAudioPlaying(false)}
          className="sr-only"
        />
      )}

      {/* ───────── Main 2-column body ───────── */}
      <div className="flex flex-1 overflow-hidden">
        {/* D3: transcript column */}
        <main className="flex-1 overflow-auto px-6 py-6 lg:px-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-extrabold uppercase tracking-wider text-ink-secondary">Transcript</h2>
            {versions.length > 1 && (
              <select
                value={selectedVersion ?? ""}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setSelectedVersion(v);
                  api.getTranscript(id, v).then(setTranscript).catch(() => {});
                  api.getMemoir(id, v).then(setMemoir).catch(() => {});
                  api.getCoaching(id, v).then(setCoaching).catch(() => {});
                }}
                className="rounded-button border border-surface-border bg-white px-2.5 py-1 text-xs font-semibold text-ink-secondary"
              >
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>v{v.version} of {versions.length}</option>
                ))}
              </select>
            )}
          </div>

          {!transcript && (
            <div className="rounded-card border border-dashed border-surface-border bg-white px-6 py-10 text-center text-sm text-ink-secondary">
              {meeting.status === "completed" ? "Transcript not available yet." : "Transcript will appear when processing completes."}
            </div>
          )}

          {transcript && (
            <div className="rounded-card border border-surface-border bg-white p-5 shadow-sm">
              <EditableTranscript
                meetingId={id}
                transcript={transcript}
                onSeek={handleSeek}
                onTranscriptUpdate={(t) => setTranscript(t)}
                editingSpeaker={editingSpeaker}
                speakerInput={speakerInput}
                teamSpeakers={teamSpeakers}
                savingSpeaker={savingSpeaker}
                speakerInputRef={speakerInputRef}
                onSpeakerClick={(speaker) => {
                  setEditingSpeaker(speaker);
                  setSpeakerInput(transcript.speaker_map?.[speaker]?.name ?? speaker);
                  if (meeting.team_id) {
                    api.getTeamSpeakers(meeting.team_id).then(setTeamSpeakers).catch(() => {});
                  }
                }}
                onSpeakerInputChange={setSpeakerInput}
                onSpeakerSave={async (original, newName, profileId) => {
                  setSavingSpeaker(true);
                  try {
                    await api.assignSpeaker(id, {
                      diarization_label: original,
                      speaker_name: newName,
                      speaker_profile_id: profileId,
                    });
                    const t = await api.getTranscript(id, selectedVersion ?? undefined);
                    setTranscript(t);
                    setEditingSpeaker(null);
                  } catch {
                    // surface failure silently for now; v1 has full error UX
                  } finally {
                    setSavingSpeaker(false);
                  }
                }}
                onSpeakerEditCancel={() => setEditingSpeaker(null)}
              />
            </div>
          )}
        </main>

        {/* D4: intelligence sidebar */}
        <aside className="hidden w-[380px] shrink-0 overflow-auto border-l border-surface-border bg-white px-5 py-6 lg:block">
          {/* AI Summary */}
          <section className="mb-6">
            <div className="mb-2 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">
              <Sparkles className="h-3.5 w-3.5 text-brand" /> AI Summary
            </div>
            {summaryNarrative ? (
              <SummaryEditor
                initialContent={summaryNarrative}
                meetingId={id}
                isEdited={memoir?.is_edited ?? false}
                onReset={async () => {
                  await api.resetMemoir(id);
                  const fresh = await api.getMemoir(id, selectedVersion ?? undefined);
                  setMemoir(fresh);
                }}
              />
            ) : (
              <p className="text-xs text-ink-secondary">No summary yet.</p>
            )}
          </section>

          {/* Key Moments */}
          {keyMoments.length > 0 && (
            <section className="mb-6">
              <div className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">Key Moments</div>
              {keyMoments.slice(0, 8).map((m, i) => {
                const time = (m.timestamp as string) || (m.time as string) || "";
                const label = (m.label as string) || (m.text as string) || "—";
                const secs = (() => {
                  const parts = time.split(":").map(Number);
                  if (parts.length === 2) return parts[0] * 60 + parts[1];
                  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
                  return null;
                })();
                return (
                  <button
                    key={i}
                    onClick={() => secs != null && handleSeek(secs)}
                    disabled={secs == null}
                    className={`flex w-full items-center gap-2.5 py-2 text-left ${
                      i < keyMoments.length - 1 ? "border-b border-surface-border" : ""
                    } ${secs != null ? "hover:bg-surface-soft" : "cursor-default"}`}
                  >
                    <span className="shrink-0 rounded-md bg-brand-light px-2 py-[3px] text-[11px] font-bold tabular-nums text-brand">
                      {time || "—"}
                    </span>
                    <span className="text-[13px] font-medium text-ink">{label}</span>
                  </button>
                );
              })}
            </section>
          )}

          {/* Action Items */}
          {actionItems.length > 0 && (
            <section className="mb-6">
              <div className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">Action Items</div>
              <EditableActionItems meetingId={id} initialItems={actionItems} />
            </section>
          )}

          {/* Signals */}
          {(signals.buying.length > 0 || signals.risk.length > 0) && (
            <section className="mb-6">
              <div className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">Signals</div>
              <EditableSignals meetingId={id} initialSignals={signals} />
            </section>
          )}

          {/* Topics */}
          {topics.length > 0 && (
            <section className="mb-6">
              <div className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">Topics Detected</div>
              <div className="flex flex-wrap gap-1.5">
                {topics.map((t) => (
                  <Tag key={t} label={t} tone="brand" />
                ))}
              </div>
            </section>
          )}

          {/* Coaching metrics */}
          {coaching && Object.keys(coachingMetrics).length > 0 && (
            <section className="mb-6">
              <div className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">Coaching</div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {cmNum("talk_ratio_pct") != null && (
                  <div className="rounded-button border border-surface-border bg-surface-soft px-3 py-2">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-ink-secondary">Talk ratio</div>
                    <div className="mt-0.5 text-sm font-extrabold text-ink">{Math.round(cmNum("talk_ratio_pct")!)}%</div>
                  </div>
                )}
                {cmNum("questions_asked") != null && (
                  <div className="rounded-button border border-surface-border bg-surface-soft px-3 py-2">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-ink-secondary">Questions</div>
                    <div className="mt-0.5 text-sm font-extrabold text-ink">{cmNum("questions_asked")}</div>
                  </div>
                )}
                {cmNum("filler_word_count") != null && (
                  <div className="rounded-button border border-surface-border bg-surface-soft px-3 py-2">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-ink-secondary">Fillers</div>
                    <div className="mt-0.5 text-sm font-extrabold text-ink">{cmNum("filler_word_count")}</div>
                  </div>
                )}
                {cmNum("interruption_count") != null && (
                  <div className="rounded-button border border-surface-border bg-surface-soft px-3 py-2">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-ink-secondary">Interruptions</div>
                    <div className="mt-0.5 text-sm font-extrabold text-ink">{cmNum("interruption_count")}</div>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Call intelligence highlights */}
          {callIntel && (callIntel.pull_score != null || callIntel.deal_momentum) && (
            <section className="mb-6">
              <div className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">Intelligence</div>
              <div className="space-y-2 text-xs">
                {callIntel.pull_score != null && (
                  <div className="flex items-center justify-between rounded-button border border-surface-border bg-surface-soft px-3 py-2">
                    <span className="text-ink-secondary">Pull score</span>
                    <span className="font-extrabold text-ink">{callIntel.pull_score}</span>
                  </div>
                )}
                {callIntel.deal_momentum && (
                  <div className="flex items-center justify-between rounded-button border border-surface-border bg-surface-soft px-3 py-2">
                    <span className="text-ink-secondary">Deal momentum</span>
                    <span className="font-extrabold text-ink">{callIntel.deal_momentum}</span>
                  </div>
                )}
                {callIntel.call_health && (
                  <div className="flex items-center justify-between rounded-button border border-surface-border bg-surface-soft px-3 py-2">
                    <span className="text-ink-secondary">Call health</span>
                    <span className="font-extrabold text-ink">{callIntel.call_health}</span>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Deal timeline (when linked to a deal) */}
          {dealTimeline && dealTimeline.entries.length > 1 && (
            <section className="mb-6">
              <div className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">Deal Timeline</div>
              <div className="space-y-1.5">
                {dealTimeline.entries.map((entry) => (
                  <button
                    key={entry.meeting_id}
                    onClick={() => entry.meeting_id !== id && router.push(`/meetings/${entry.meeting_id}`)}
                    className={`flex w-full items-center gap-2 rounded-button border px-3 py-2 text-left text-xs transition-colors ${
                      entry.meeting_id === id
                        ? "border-brand bg-brand-light"
                        : "border-surface-border bg-surface-soft hover:border-brand/40 hover:bg-white"
                    }`}
                  >
                    <span className="text-[10px] tabular-nums text-ink-secondary">
                      {entry.date ? new Date(entry.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—"}
                    </span>
                    <span className="truncate font-bold text-ink">{entry.title || "Untitled"}</span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
