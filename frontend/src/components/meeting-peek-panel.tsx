"use client";

/**
 * Right slide-in peek panel for meeting cards (revamp v2).
 *
 * Lightweight preview without route navigation: opens on row click in
 * MeetingsV2, shows summary + key moments + action items, with a banner
 * to navigate to the full detail page.
 *
 * Data: GET /api/intelligence/call/{meetingId} (existing endpoint).
 * Fields are loosely typed (Record<string, unknown>) because the underlying
 * intelligence.json shape varies — extractors only set fields they produce.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Sparkles, X, Play, Pause, Share2, Maximize2, ChevronRight, CheckCircle2, Link2, Check, MessageSquare, HelpCircle, Gavel, FileText, FileSpreadsheet, Music, Mail, Loader2 } from "lucide-react";

import { api } from "@/lib/api";
import type { Meeting, TranscriptResponse } from "@/lib/types";
import { PlatformBadge, platformFromSourceOrUrl } from "@/components/ui/platform-badge";

interface MeetingPeekPanelProps {
  meeting: Meeting;
  onClose: () => void;
}

const PEEK_MIN_WIDTH = 320;
const PEEK_MAX_WIDTH = 900;
const PEEK_DEFAULT_WIDTH = 380;
const PEEK_WIDTH_STORAGE_KEY = "memoir:peek-width";

function dateLabel(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
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

/**
 * P5 summary shape as written by the pipeline to summary.json.
 * Schema is the inner result of run_summary_only / run_full_pipeline P5 prompt.
 * Fields are optional — Type 7 (fast) may omit some when transcript is short.
 */
interface P5Summary {
  tldr?: string;
  decisions?: Array<{ title?: string; detail?: string }>;
  open_questions?: Array<{ text?: string }>;
  action_items?: Array<{
    text?: string;
    owner?: string;
    owner_email?: string;
    urgency?: "today" | "this_week" | "next_week" | "upcoming" | "async" | string;
    context?: string | null;
    blocking?: string | null;
  }>;
  discussion_threads?: Array<{
    title?: string;
    priority?: "high" | "medium" | "low" | string;
    summary?: string;
    attributions?: Array<{ speaker?: string; action?: string }>;
  }>;
}

const URGENCY_LABEL: Record<string, string> = {
  today: "Today",
  this_week: "This week",
  next_week: "Next week",
  upcoming: "Upcoming",
  async: "Async",
};

const URGENCY_TONE: Record<string, string> = {
  today: "bg-brand-red/10 text-brand-red",
  this_week: "bg-brand/10 text-brand",
  next_week: "bg-amber-100 text-amber-700",
  upcoming: "bg-surface-soft text-ink-secondary",
  async: "bg-surface-soft text-ink-secondary",
};

const PRIORITY_TONE: Record<string, string> = {
  high: "bg-brand-red/10 text-brand-red",
  medium: "bg-amber-100 text-amber-700",
  low: "bg-surface-soft text-ink-secondary",
};

export function MeetingPeekPanel({ meeting, onClose }: MeetingPeekPanelProps) {
  const router = useRouter();
  const [summary, setSummary] = useState<P5Summary | null>(null);
  const [loading, setLoading] = useState(true);

  // Audio playback state
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [audioSrc, setAudioSrc] = useState<string | null>(null);
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [audioError, setAudioError] = useState<string | null>(null);

  // Share popover
  const [shareOpen, setShareOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [generatingLink, setGeneratingLink] = useState(false);
  const [audioDownloading, setAudioDownloading] = useState(false);
  const [pdfDownloading, setPdfDownloading] = useState(false);
  const [csvLoading, setCsvLoading] = useState(false);
  const shareRef = useRef<HTMLDivElement>(null);

  // Email modal — mirrors the detail-page flow (To / Template / live HTML
  // preview / send). Reuses the same backend endpoints so behavior matches.
  const [emailDialogOpen, setEmailDialogOpen] = useState(false);
  const [emailTo, setEmailTo] = useState("");
  const [emailTemplate, setEmailTemplate] = useState<"recorder" | "internal" | "external">("external");
  const [emailPreviewHtml, setEmailPreviewHtml] = useState("");
  const [emailPreviewSubject, setEmailPreviewSubject] = useState("");
  const [emailLog, setEmailLog] = useState<Array<{ id: string; to_email: string; status: string; created_at: string }>>([]);
  const [emailSending, setEmailSending] = useState(false);
  const [emailSent, setEmailSent] = useState(false);

  // Lazy transcript cache — only fetched when the user asks for CSV / PDF
  // since the peek itself doesn't render the transcript.
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);

  // ── Resizable width ─────────────────────────────────────────────
  // Drag the left edge of the panel to widen or narrow it. Width is
  // clamped to [PEEK_MIN_WIDTH, PEEK_MAX_WIDTH] and persisted across
  // sessions in localStorage.
  const [panelWidth, setPanelWidth] = useState<number>(() => {
    if (typeof window === "undefined") return PEEK_DEFAULT_WIDTH;
    const raw = window.localStorage.getItem(PEEK_WIDTH_STORAGE_KEY);
    const n = raw ? Number(raw) : NaN;
    if (Number.isFinite(n)) {
      return Math.min(PEEK_MAX_WIDTH, Math.max(PEEK_MIN_WIDTH, n));
    }
    return PEEK_DEFAULT_WIDTH;
  });
  const [resizing, setResizing] = useState(false);

  const beginResize = (e: React.MouseEvent) => {
    e.preventDefault();
    setResizing(true);
    const startX = e.clientX;
    const startWidth = panelWidth;

    const onMove = (ev: MouseEvent) => {
      // Panel grows when the cursor moves LEFT (closer to viewport left
      // edge) because the panel is anchored on the right.
      const delta = startX - ev.clientX;
      const next = Math.min(
        PEEK_MAX_WIDTH,
        Math.max(PEEK_MIN_WIDTH, startWidth + delta),
      );
      setPanelWidth(next);
    };

    const onUp = () => {
      setResizing(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      // Persist final value on release.
      setPanelWidth((w) => {
        try {
          window.localStorage.setItem(PEEK_WIDTH_STORAGE_KEY, String(w));
        } catch {
          // ignore quota errors
        }
        return w;
      });
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // While resizing, suppress text-selection across the page so the
  // cursor doesn't accidentally highlight content under the mouse.
  useEffect(() => {
    if (!resizing) return;
    const prevUserSelect = document.body.style.userSelect;
    const prevCursor = document.body.style.cursor;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      document.body.style.userSelect = prevUserSelect;
      document.body.style.cursor = prevCursor;
    };
  }, [resizing]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSummary(null);

    // Prefer the P5 file endpoint — it's available from Type 7 (fast email)
    // onward, before Type 3 / call_intelligence has run. Fall back to
    // call_intelligence.summary for legacy meetings whose summary.json
    // predates the endpoint.
    //
    // Both data sources nest the actual P5 fields one level deep — the
    // file stores {meeting:..., summary:{tldr,...}} (the SUMMARY_USER
    // schema) and call_intelligence.summary copies that shape verbatim.
    // The /summary endpoint flattens it; we flatten the fallback here.
    (async () => {
      try {
        const data = await api.getMeetingSummary(meeting.id);
        if (cancelled) return;
        setSummary((data ?? {}) as P5Summary);
      } catch {
        try {
          const intel = await api.getCallIntelligence(meeting.id);
          if (cancelled) return;
          const raw = (intel?.summary ?? {}) as Record<string, unknown>;
          const inner =
            raw && typeof raw.summary === "object" && raw.summary !== null
              ? (raw.summary as P5Summary)
              : (raw as P5Summary);
          setSummary(inner);
        } catch {
          if (!cancelled) setSummary(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [meeting.id]);

  // Reset audio + share + email state when the user clicks a different meeting
  useEffect(() => {
    setAudioSrc(null);
    setAudioPlaying(false);
    setAudioError(null);
    setShareOpen(false);
    setEmailDialogOpen(false);
    setEmailSent(false);
    setEmailPreviewHtml("");
    setEmailPreviewSubject("");
    setEmailLog([]);
    setTranscript(null);
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
    }
  }, [meeting.id]);

  // Esc to close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  // Close share popover on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (shareRef.current && !shareRef.current.contains(e.target as Node)) {
        setShareOpen(false);
      }
    };
    if (shareOpen) {
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }
  }, [shareOpen]);

  const platform = platformFromSourceOrUrl(meeting.source, meeting.meeting_url);
  const tldr = summary?.tldr || meeting.summary_excerpt || "";
  const decisions = (summary?.decisions ?? []).filter((d) => d?.title || d?.detail);
  const openQuestions = (summary?.open_questions ?? []).filter((q) => q?.text);
  const actions = (summary?.action_items ?? []).filter((a) => a?.text);
  const threads = (summary?.discussion_threads ?? []).filter((t) => t?.title || t?.summary);
  const hasAnyP5 =
    Boolean(summary?.tldr) ||
    decisions.length > 0 ||
    openQuestions.length > 0 ||
    actions.length > 0 ||
    threads.length > 0;
  const hasRecording = meeting.has_recording;

  const goDetail = () => router.push(`/meetings/${meeting.id}`);

  // Toggle audio. Lazy-fetch presigned URL on first play; reuse thereafter
  // for the same meeting (URL is presigned with a TTL — refetch if expired).
  const togglePlay = async () => {
    if (!hasRecording) return;
    if (audioPlaying) {
      audioRef.current?.pause();
      return;
    }
    if (!audioSrc) {
      setAudioLoading(true);
      setAudioError(null);
      try {
        const urls = await api.getAudioUrls(meeting.id);
        // audio-urls returns a map keyed by version (merged, enhanced, etc.);
        // pick the first available track.
        const first = Object.values(urls)[0];
        if (!first) {
          setAudioError("No audio track available");
          setAudioLoading(false);
          return;
        }
        setAudioSrc(first);
        // Wait one tick for the <audio> src to bind before play()
        setTimeout(() => audioRef.current?.play().catch((e) => setAudioError(e.message)), 0);
      } catch {
        setAudioError("Could not load audio");
      } finally {
        setAudioLoading(false);
      }
      return;
    }
    audioRef.current?.play().catch((e) => setAudioError(e.message));
  };

  const handleCopyLink = async () => {
    setGeneratingLink(true);
    try {
      const result = await api.createShareLink(meeting.id);
      const publicUrl = `${window.location.origin}/shared/${result.share_token}`;
      await navigator.clipboard.writeText(publicUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      try {
        await navigator.clipboard.writeText(`${window.location.origin}/meetings/${meeting.id}`);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
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
      // Backend signs a URL with ResponseContentDisposition=attachment so
      // the browser saves the file instead of streaming inline.
      const { url, filename } = await api.getRecordingDownloadUrl(meeting.id);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch {
      // Fall back to the inline presigned URL only if the download
      // endpoint fails entirely (no recording on disk, 404).
      if (audioSrc) window.open(audioSrc, "_blank");
    } finally {
      setAudioDownloading(false);
    }
  };

  // Fetch and cache the transcript on demand — peek doesn't render it,
  // but PDF + CSV exports need it.
  const ensureTranscript = async (): Promise<TranscriptResponse | null> => {
    if (transcript) return transcript;
    try {
      const t = await api.getTranscript(meeting.id);
      setTranscript(t);
      return t;
    } catch {
      return null;
    }
  };

  const handleExportCsv = async () => {
    setShareOpen(false);
    setCsvLoading(true);
    try {
      const t = await ensureTranscript();
      if (!t || t.segments.length === 0) return;
      const rows: string[][] = [["Timestamp", "Speaker", "Text"]];
      for (const seg of t.segments) {
        const sec = seg.original_start ?? seg.start ?? 0;
        const mm = Math.floor(sec / 60);
        const ss = Math.floor(sec % 60).toString().padStart(2, "0");
        const time = `${mm}:${ss}`;
        const rawSpeaker = seg.speaker || "";
        const name = t.speaker_map?.[rawSpeaker]?.name || rawSpeaker || "Speaker";
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
    } finally {
      setCsvLoading(false);
    }
  };

  const handleDownloadPdf = async () => {
    setShareOpen(false);
    setPdfDownloading(true);
    try {
      const t = await ensureTranscript();
      const html2pdf = (await import("html2pdf.js")).default;
      const fmtClock = (sec: number) => {
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60).toString().padStart(2, "0");
        return `${m}:${s}`;
      };
      const dateStr =
        meeting.started_at || meeting.created_at
          ? new Date((meeting.started_at || meeting.created_at)!).toLocaleString()
          : "";
      const durStr = meeting.duration_seconds != null ? durationLabel(meeting.duration_seconds) : "";

      let html = `<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:710px;margin:0 auto;color:#27272a;font-size:13px;line-height:1.6">`;
      html += `<h1 style="font-size:20px;margin-bottom:4px">${meeting.title || "Untitled Meeting"}</h1>`;
      html += `<div style="color:#71717a;font-size:12px;margin-bottom:24px">${dateStr}${durStr ? ` · ${durStr}` : ""}</div>`;
      if (tldr) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Summary</h2><p>${tldr}</p>`;
      }
      if (actions.length > 0) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Action Items</h2>`;
        for (const a of actions) {
          html += `<div style="margin-bottom:4px;padding-left:16px">&bull; ${a.text || ""}</div>`;
        }
      }
      if (decisions.length > 0) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Decisions</h2>`;
        for (const d of decisions) {
          html += `<div style="margin-bottom:4px">&bull; <strong>${d.title || ""}</strong>${d.detail ? ` — ${d.detail}` : ""}</div>`;
        }
      }
      if (t && t.segments.length > 0) {
        html += `<h2 style="font-size:15px;margin-top:24px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Transcript</h2>`;
        html += `<div style="color:#71717a;margin-bottom:10px;font-size:12px">${t.word_count.toLocaleString()} words · ${t.speaker_count} speakers</div>`;
        for (const seg of t.segments) {
          const time = fmtClock(seg.original_start ?? seg.start ?? 0);
          const rawSpeaker = seg.speaker || "Speaker";
          const sp = t.speaker_map?.[rawSpeaker]?.name || rawSpeaker;
          html += `<div style="margin-bottom:6px;display:flex;gap:10px"><span style="color:#a1a1aa;font-family:monospace;font-size:11px;min-width:40px;flex-shrink:0">${time}</span><div><span style="font-weight:600;color:#5C5CE0">[${sp}]</span> ${seg.text}</div></div>`;
        }
      }
      html += `<div style="margin-top:32px;padding-top:12px;border-top:1px solid #e4e4e7;color:#a1a1aa;font-size:11px;line-height:1.6">Generated by Memoir · ${new Date().toLocaleDateString()}</div></div>`;

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
    try {
      const [preview, log] = await Promise.all([
        api.getEmailPreview(meeting.id, "external"),
        api
          .getEmailLog(meeting.id)
          .catch(() => [] as Array<{ id: string; to_email: string; status: string; created_at: string }>),
      ]);
      setEmailPreviewHtml(preview.html_body);
      setEmailPreviewSubject(preview.subject);
      if (Array.isArray(log)) setEmailLog(log);
    } catch {
      // empty preview state
    }
  };

  const handleSelectEmailTemplate = async (tmpl: "recorder" | "internal" | "external") => {
    setEmailTemplate(tmpl);
    setEmailPreviewHtml("");
    setEmailPreviewSubject("");
    try {
      const preview = await api.getEmailPreview(meeting.id, tmpl);
      setEmailPreviewHtml(preview.html_body);
      setEmailPreviewSubject(preview.subject);
    } catch {
      // empty preview
    }
  };

  const handleSendEmail = async () => {
    if (!emailTo || !emailTo.includes("@")) return;
    setEmailSending(true);
    try {
      await api.sendMeetingEmail(meeting.id, emailTo, emailTemplate);
      setEmailSent(true);
      setTimeout(() => setEmailDialogOpen(false), 1200);
    } catch {
      window.alert("Failed to send email. Check the recipient address and try again.");
    } finally {
      setEmailSending(false);
    }
  };

  return (
    <aside
      style={{ width: panelWidth }}
      className="relative shrink-0 overflow-auto border-l border-surface-border bg-white font-sans text-ink"
    >
      {/* Left-edge drag handle to resize the panel width.
          Hidden grab strip widens to a visible accent on hover/resize. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize peek panel"
        onMouseDown={beginResize}
        className={`group absolute left-0 top-0 z-10 h-full w-1.5 cursor-col-resize select-none`}
        title="Drag to resize"
      >
        <div
          className={`mx-auto h-full w-px transition-colors ${
            resizing ? "bg-brand" : "bg-transparent group-hover:bg-brand/40"
          }`}
        />
      </div>
      <button
        onClick={goDetail}
        className="flex w-full items-center justify-between border-b border-surface-border px-5 py-3.5 text-left transition-colors hover:bg-brand-light"
        style={{ background: "linear-gradient(135deg, rgba(92,92,224,0.03), rgba(92,92,224,0.08))" }}
      >
        <span className="flex items-center gap-2 text-[13px] font-bold text-brand">
          <Maximize2 className="h-3.5 w-3.5" />
          View full meeting details
        </span>
        <ChevronRight className="h-3.5 w-3.5 text-brand" />
      </button>

      <div className="px-5 py-5">
        <div className="mb-4 flex items-center justify-between">
          <span className="text-[15px] font-extrabold text-ink">Quick Preview</span>
          <button onClick={onClose} className="rounded p-1 hover:bg-surface-soft" aria-label="Close panel">
            <X className="h-4 w-4 text-ink-secondary" />
          </button>
        </div>

        <button onClick={goDetail} className="mb-4 block w-full text-left">
          <h2
            className="mb-1.5 text-[16px] font-extrabold leading-snug text-brand underline decoration-brand/30 underline-offset-[3px]"
          >
            {meeting.title || "Untitled meeting"}
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <PlatformBadge platform={platform} />
            <span className="text-xs font-medium text-ink-secondary">
              {dateLabel(meeting.started_at)} · {timeLabel(meeting.started_at)} · {durationLabel(meeting.duration_seconds)}
            </span>
          </div>
          {meeting.primary_participant_name && (
            <div className="mt-1.5 text-xs font-medium text-ink-secondary">
              {meeting.primary_participant_name}
              {meeting.primary_participant_company && <span> · {meeting.primary_participant_company}</span>}
            </div>
          )}
        </button>

        <div className="mb-2 flex items-center gap-2">
          <button
            onClick={togglePlay}
            disabled={!hasRecording || audioLoading}
            title={!hasRecording ? "No recording available" : audioPlaying ? "Pause" : "Play"}
            aria-label={audioPlaying ? "Pause audio" : "Play audio"}
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-colors ${
              !hasRecording
                ? "cursor-not-allowed bg-surface-soft text-ink-light"
                : "bg-brand text-white hover:bg-brand-dark"
            } disabled:opacity-60`}
          >
            {audioLoading ? (
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" />
            ) : audioPlaying ? (
              <Pause className="h-4 w-4" />
            ) : (
              <Play className="h-4 w-4 translate-x-[1px]" />
            )}
          </button>

          {/* Share dropdown — mirrors detail-page ShareMenu options that make
              sense from a preview (copy public link + download audio). The
              transcript CSV / PDF download require detail-page state, so
              those route to the detail page. */}
          <div className="relative" ref={shareRef}>
            <button
              onClick={() => setShareOpen((v) => !v)}
              className="flex items-center gap-1.5 rounded-button bg-brand-light px-3.5 py-2 text-xs font-bold text-brand hover:bg-brand-light/80"
            >
              <Share2 className="h-3 w-3" /> Share
              <ChevronRight
                className={`h-3 w-3 transition-transform ${shareOpen ? "rotate-90" : ""}`}
              />
            </button>
            {shareOpen && (
              <div className="absolute left-0 top-full z-50 mt-1 w-60 rounded-card border border-surface-border bg-white py-1.5 shadow-elevated">
                <button
                  onClick={handleCopyLink}
                  disabled={generatingLink}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
                >
                  {copied ? (
                    <Check className="h-3.5 w-3.5 text-brand-green" />
                  ) : (
                    <Link2 className="h-3.5 w-3.5 text-ink-secondary" />
                  )}
                  {copied ? "Link copied" : generatingLink ? "Generating link..." : "Copy public share link"}
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
                  disabled={audioDownloading || !hasRecording}
                  title={!hasRecording ? "No recording available" : "Download recording"}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
                >
                  <Music className="h-3.5 w-3.5 text-ink-secondary" />
                  {audioDownloading ? "Downloading audio..." : "Download audio"}
                </button>
                <button
                  onClick={handleExportCsv}
                  disabled={csvLoading}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-surface-soft disabled:opacity-50"
                >
                  <FileSpreadsheet className="h-3.5 w-3.5 text-ink-secondary" />
                  {csvLoading ? "Loading transcript..." : "Export transcript CSV"}
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

          {audioError && (
            <span className="text-[11px] font-medium text-brand-red">{audioError}</span>
          )}
        </div>

        {/* Hidden audio element controlled by the Play button */}
        {audioSrc && (
          <audio
            ref={audioRef}
            src={audioSrc}
            preload="metadata"
            onPlay={() => setAudioPlaying(true)}
            onPause={() => setAudioPlaying(false)}
            onEnded={() => setAudioPlaying(false)}
            onError={() => setAudioError("Playback failed")}
            controls
            className="mb-3 mt-1 w-full"
          />
        )}

        {/* TL;DR */}
        <section className="mb-5">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">
            <Sparkles className="h-3.5 w-3.5 text-brand" /> AI Summary
          </div>
          {loading ? (
            <div className="space-y-1.5">
              <div className="h-3 w-full animate-pulse rounded bg-surface-soft" />
              <div className="h-3 w-4/5 animate-pulse rounded bg-surface-soft" />
              <div className="h-3 w-3/5 animate-pulse rounded bg-surface-soft" />
            </div>
          ) : tldr ? (
            <p className="text-[13px] font-normal leading-relaxed text-ink">{tldr}</p>
          ) : (
            <p className="text-[13px] italic text-ink-secondary">Summary not yet available.</p>
          )}
        </section>

        {/* Decisions */}
        {decisions.length > 0 && (
          <section className="mb-5">
            <div className="mb-2.5 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">
              <Gavel className="h-3.5 w-3.5 text-brand" /> Decisions
            </div>
            {decisions.map((d, i) => (
              <div
                key={i}
                className={`py-2 ${i < decisions.length - 1 ? "border-b border-surface-border" : ""}`}
              >
                {d.title && (
                  <div className="text-[13px] font-bold text-ink">{d.title}</div>
                )}
                {d.detail && (
                  <div className="mt-0.5 text-[12px] leading-relaxed text-ink-secondary">{d.detail}</div>
                )}
              </div>
            ))}
          </section>
        )}

        {/* Action Items */}
        {actions.length > 0 && (
          <section className="mb-5">
            <div className="mb-2.5 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">
              <CheckCircle2 className="h-3.5 w-3.5 text-brand" /> Action Items
            </div>
            {actions.map((a, i) => {
              const urgencyKey = (a.urgency || "").toLowerCase();
              const urgencyLabel = URGENCY_LABEL[urgencyKey];
              const urgencyTone = URGENCY_TONE[urgencyKey];
              return (
                <div
                  key={i}
                  className={`flex items-start gap-2.5 py-2 ${
                    i < actions.length - 1 ? "border-b border-surface-border" : ""
                  }`}
                >
                  <div className="mt-0.5 h-5 w-5 shrink-0 rounded-md border-2 border-surface-border bg-transparent" />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-ink">{a.text}</div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-ink-secondary">
                      {a.owner && <span>👤 {a.owner}</span>}
                      {urgencyLabel && (
                        <span className={`rounded px-1.5 py-[1px] font-bold ${urgencyTone || "bg-surface-soft text-ink-secondary"}`}>
                          {urgencyLabel}
                        </span>
                      )}
                      {a.blocking && <span className="text-brand-red">Blocking: {a.blocking}</span>}
                    </div>
                    {a.context && (
                      <div className="mt-1 text-[11px] italic text-ink-light">{a.context}</div>
                    )}
                  </div>
                </div>
              );
            })}
          </section>
        )}

        {/* Open Questions */}
        {openQuestions.length > 0 && (
          <section className="mb-5">
            <div className="mb-2.5 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">
              <HelpCircle className="h-3.5 w-3.5 text-brand" /> Open Questions
            </div>
            {openQuestions.map((q, i) => (
              <div
                key={i}
                className={`flex items-start gap-2 py-2 ${
                  i < openQuestions.length - 1 ? "border-b border-surface-border" : ""
                }`}
              >
                <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                <span className="text-[13px] text-ink">{q.text}</span>
              </div>
            ))}
          </section>
        )}

        {/* Discussion Threads */}
        {threads.length > 0 && (
          <section className="mb-2">
            <div className="mb-2.5 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wider text-ink-secondary">
              <MessageSquare className="h-3.5 w-3.5 text-brand" /> Discussion Threads
            </div>
            {threads.map((t, i) => {
              const priorityKey = (t.priority || "").toLowerCase();
              const priorityTone = PRIORITY_TONE[priorityKey];
              return (
                <div
                  key={i}
                  className={`py-2.5 ${i < threads.length - 1 ? "border-b border-surface-border" : ""}`}
                >
                  <div className="mb-1 flex items-center justify-between gap-2">
                    {t.title && (
                      <div className="text-[13px] font-bold text-ink">{t.title}</div>
                    )}
                    {priorityKey && (
                      <span className={`shrink-0 rounded px-1.5 py-[1px] text-[10px] font-bold uppercase ${priorityTone || "bg-surface-soft text-ink-secondary"}`}>
                        {priorityKey}
                      </span>
                    )}
                  </div>
                  {t.summary && (
                    <div className="text-[12px] leading-relaxed text-ink-secondary">{t.summary}</div>
                  )}
                </div>
              );
            })}
          </section>
        )}

        {!loading && !hasAnyP5 && (
          <div className="mt-2 rounded-lg border border-dashed border-surface-border bg-surface-soft px-4 py-5 text-center text-xs text-ink-secondary">
            Summary still processing. Comes back as soon as the pipeline finishes the P5 step.
          </div>
        )}
      </div>

      {/* Send-email modal — same flow as the detail page: To / Template
          / live HTML preview / previously sent log. Hits the same
          getEmailPreview + sendMeetingEmail endpoints so behavior is
          identical. Rendered inside the aside so it floats above the
          peek without leaking into the list pane. */}
      {emailDialogOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-ink/30 backdrop-blur-[2px]"
          onClick={() => setEmailDialogOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="flex max-h-[88vh] w-[min(720px,92vw)] flex-col overflow-hidden rounded-2xl border border-surface-border bg-white shadow-elevated"
          >
            <div className="flex items-center justify-between border-b border-surface-border px-5 py-3.5">
              <div className="flex items-center gap-2 text-[15px] font-extrabold text-ink">
                <Mail className="h-4 w-4 text-brand" />
                Send meeting summary
              </div>
              <button
                onClick={() => setEmailDialogOpen(false)}
                className="rounded p-1 hover:bg-surface-soft"
                aria-label="Close"
              >
                <X className="h-4 w-4 text-ink-secondary" />
              </button>
            </div>

            <div className="flex flex-1 flex-col gap-3 overflow-auto px-5 py-4">
              <label className="text-[11px] font-bold uppercase tracking-wider text-ink-secondary">To</label>
              <input
                type="email"
                value={emailTo}
                onChange={(e) => setEmailTo(e.target.value)}
                placeholder="recipient@example.com"
                className="rounded-md border border-surface-border bg-white px-3 py-2 text-sm outline-none focus:border-brand"
              />

              <label className="mt-1 text-[11px] font-bold uppercase tracking-wider text-ink-secondary">Template</label>
              <div className="flex gap-1.5">
                {(["external", "internal", "recorder"] as const).map((tmpl) => (
                  <button
                    key={tmpl}
                    onClick={() => handleSelectEmailTemplate(tmpl)}
                    className={`rounded-md border px-3 py-1 text-xs font-semibold capitalize ${
                      emailTemplate === tmpl
                        ? "border-brand bg-brand-light text-brand"
                        : "border-surface-border bg-white text-ink-secondary"
                    }`}
                  >
                    {tmpl}
                  </button>
                ))}
              </div>

              <div className="mt-2">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-ink-secondary">Preview</span>
                  {emailPreviewSubject && (
                    <span className="truncate text-[11px] font-medium text-ink-secondary">
                      Subject: {emailPreviewSubject}
                    </span>
                  )}
                </div>
                <div className="h-[280px] overflow-hidden rounded-lg border border-surface-border bg-surface-soft">
                  {emailPreviewHtml ? (
                    <iframe
                      title="Email preview"
                      srcDoc={emailPreviewHtml}
                      className="h-full w-full border-0 bg-white"
                      sandbox=""
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center text-xs text-ink-secondary">
                      Loading preview…
                    </div>
                  )}
                </div>
              </div>

              {emailLog.length > 0 && (
                <div className="mt-2">
                  <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wider text-ink-secondary">
                    Previously sent
                  </div>
                  <div className="rounded-lg border border-surface-border bg-white">
                    {emailLog.slice(0, 4).map((l) => (
                      <div
                        key={l.id}
                        className="flex items-center justify-between border-b border-surface-border px-3 py-1.5 text-xs last:border-b-0"
                      >
                        <span className="truncate text-ink">{l.to_email}</span>
                        <span className="ml-2 shrink-0 text-ink-secondary">
                          {l.status} · {new Date(l.created_at).toLocaleString()}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-surface-border bg-surface-soft px-5 py-3">
              <button
                onClick={() => setEmailDialogOpen(false)}
                className="rounded-md px-3 py-1.5 text-xs font-semibold text-ink-secondary hover:bg-white"
              >
                Cancel
              </button>
              <button
                onClick={handleSendEmail}
                disabled={emailSending || emailSent || !emailTo || !emailTo.includes("@")}
                className="flex items-center gap-1.5 rounded-md bg-brand px-3.5 py-1.5 text-xs font-bold text-white shadow-sm transition-colors hover:bg-brand-dark disabled:opacity-50"
              >
                {emailSending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {emailSent ? "Sent ✓" : emailSending ? "Sending…" : "Send"}
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
