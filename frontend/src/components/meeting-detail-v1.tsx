"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import type { MeetingDetail, MemoirResponse, TranscriptResponse, TranscriptionVersion, CoachingResponse, SpeakerProfile, MeetingParticipantInfo, CrmPushResult, CrmConnection, CallIntelligenceResponse, DealTimelineResponse } from "@/lib/types";
import { resolveMemoir, getMeetingSourceLabel } from "@/lib/utils";
import { EditableTranscript } from "@/components/editable-transcript";
import { EditableActionItems } from "@/components/editable-action-items";
import { EditableSignals } from "@/components/editable-signals";
import { ErrorBoundary } from "@/components/error-boundary";
import { ShareMenu } from "@/components/share-menu";

// TipTap uses browser APIs (document, window) — must skip SSR
const SummaryEditor = dynamic(
  () => import("@/components/summary-editor").then((m) => m.SummaryEditor),
  { ssr: false },
);

// ─── Helper components ──────────────────────────────────────────

const StatusBadge = ({ status }: { status: string }) => {
  const config: Record<string, { label: string; color: string }> = {
    pending: { label: "Pending", color: "bg-memoir-bg text-memoir-text-secondary border-memoir-border" },
    scheduled: { label: "Scheduled", color: "bg-blue-50 text-blue-700 border-blue-200" },
    joining: { label: "Joining", color: "bg-blue-50 text-blue-700 border-blue-200" },
    waiting_in_lobby: { label: "Waiting in lobby", color: "bg-blue-50 text-blue-700 border-blue-200" },
    recording: { label: "Recording", color: "bg-red-50 text-red-700 border-red-200" },
    processing: { label: "Processing", color: "bg-amber-50 text-amber-700 border-amber-200" },
    waiting_for_transcription: { label: "Transcribing", color: "bg-amber-50 text-amber-700 border-amber-200" },
    analyzing: { label: "Analyzing", color: "bg-indigo-50 text-indigo-700 border-indigo-200" },
    completed: { label: "Completed", color: "bg-emerald-50 text-emerald-700 border-emerald-200" },
    failed: { label: "Failed", color: "bg-red-50 text-red-600 border-red-200" },
    // fast-email-only: T6 diarization retry failed → fast email was sent but
    // T5/T3/T2 were skipped. Polished version unavailable; user can retry.
    fast_email_only: { label: "Fast email only", color: "bg-amber-50 text-amber-700 border-amber-200" },
  };
  const c = config[status] || config.pending;
  return (
    <span className={`text-xs font-medium px-2.5 py-1 rounded-full border ${c.color}`}>
      {c.label}
    </span>
  );
};

const ConfidenceMeter = ({ value }: { value: number }) => {
  const color = value >= 75 ? "bg-emerald-500" : value >= 50 ? "bg-amber-400" : "bg-red-400";
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-memoir-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs text-memoir-text-muted font-medium">{value}%</span>
    </div>
  );
};

// ─── Helpers ────────────────────────────────────────────────────

function formatSeconds(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatDuration(seconds: number): string {
  const m = Math.round(seconds / 60);
  return `${m} min`;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

const SPEAKER_COLORS = [
  "#4f46e5", // indigo
  "#059669", // emerald
  "#7c3aed", // violet
  "#d97706", // amber
  "#e11d48", // rose
  "#0891b2", // cyan
  "#ea580c", // orange
  "#2563eb", // blue
];

function getSpeakerColor(speaker: string, speakerMap: Map<string, number>): string {
  if (!speakerMap.has(speaker)) {
    speakerMap.set(speaker, speakerMap.size);
  }
  return SPEAKER_COLORS[speakerMap.get(speaker)! % SPEAKER_COLORS.length];
}

// ─── PDF Download ───────────────────────────────────────────────

async function downloadPdf(
  meeting: MeetingDetail,
  rawMemoir: MemoirResponse | null,
  coaching: CoachingResponse | null,
  transcript: TranscriptResponse | null,
) {
  const memoir = resolveMemoir(rawMemoir, transcript?.speaker_map) ?? rawMemoir;
  const title = meeting.title || "Untitled Call";
  const date = formatDate(meeting.created_at);
  const duration = meeting.duration_seconds != null ? formatDuration(meeting.duration_seconds) : "";
  const cm = coaching?.coaching_metrics as Record<string, unknown> | undefined;
  const rawTalkRatio = cm?.talk_ratio as { rep_percentage: number; customer_percentage: number } | undefined;
  const talkRatio = (() => {
    if (!rawTalkRatio || !transcript?.segments?.length || !transcript?.speaker_map) return rawTalkRatio;
    const names = new Set(transcript.segments.map(s => transcript.speaker_map?.[s.speaker || ""]?.name || s.speaker));
    if (names.size <= 1) return { rep_percentage: 100, customer_percentage: 0 };
    return rawTalkRatio;
  })();
  const questionCount = cm?.question_count as { rep: number; customer: number } | undefined;
  const fillerCount = cm?.filler_word_count as { rep: number; customer: number } | undefined;
  const speakerColorMap = new Map<string, number>();

  let html = `<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:710px;margin:0 auto;color:#27272a;font-size:13px;line-height:1.6">`;

  html += `<h1 style="font-size:20px;margin-bottom:4px">${title}</h1><div style="color:#71717a;font-size:12px;margin-bottom:24px">${date}`;
  if (duration) html += ` &middot; ${duration}`;
  if (meeting.source) html += ` &middot; ${getMeetingSourceLabel({ source: meeting.source, bot_job_id: meeting.bot_job_id })}`;
  if (memoir?.call_metadata?.call_type && memoir.call_metadata.call_type !== "Other" && memoir.call_metadata.call_type !== "null") html += ` &middot; ${memoir.call_metadata.call_type}`;
  if (memoir?.overall_confidence_score != null) html += ` &middot; Confidence: ${memoir.overall_confidence_score}%`;
  html += `</div>`;

  if (memoir?.narrative_summary) {
    html += `<h2 style="font-size:15px;margin-top:28px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Summary</h2>`;
    memoir.narrative_summary.split("\n\n").forEach(p => { html += `<p>${p}</p>`; });
  }
  if (memoir?.call_metadata?.participants?.length) {
    html += `<h2 style="font-size:15px;margin-top:28px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Participants</h2>`;
    const seenPdf = new Map<string, typeof memoir.call_metadata.participants[0]>();
    for (const p of memoir.call_metadata.participants) {
      const ex = seenPdf.get(p.name);
      if (!ex) { seenPdf.set(p.name, { ...p }); }
      else { if (!ex.role || ex.role === "other") ex.role = p.role; if (!ex.title && p.title) ex.title = p.title; if (!ex.company && p.company) ex.company = p.company; }
    }
    Array.from(seenPdf.values()).forEach(p => {
      const det = [p.title, p.company].filter(Boolean).join(" at ");
      html += `<div style="margin-bottom:4px"><strong>${p.name}</strong> <span style="color:#71717a">(${p.role})${det ? ` — ${det}` : ""}</span></div>`;
    });
  }
  const ns = memoir?.next_steps;
  if (ns && (ns.next_meeting || ns.next_milestone || ns.suggested_deal_stage)) {
    html += `<h2 style="font-size:15px;margin-top:28px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Next Steps</h2>`;
    if (ns.suggested_deal_stage) html += `<div><strong>Suggested stage:</strong> ${ns.suggested_deal_stage}</div>`;
    if (ns.next_milestone) html += `<div><strong>Milestone:</strong> ${ns.next_milestone}</div>`;
    if (ns.next_meeting) html += `<div><strong>Next meeting:</strong> ${ns.next_meeting}</div>`;
  }
  const repActs = memoir?.action_items?.filter(a => a.type === "rep") || [];
  const prospActs = memoir?.action_items?.filter(a => a.type === "prospect") || [];
  if (repActs.length > 0 || prospActs.length > 0) {
    html += `<h2 style="font-size:15px;margin-top:28px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Action Items</h2>`;
    if (repActs.length > 0) {
      html += `<div style="font-size:11px;color:#71717a;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Your To-dos</div>`;
      repActs.forEach(a => { html += `<div style="margin-bottom:4px;padding-left:16px;position:relative"><span style="position:absolute;left:0">&#9744;</span>${a.text}</div>`; });
    }
    if (prospActs.length > 0) {
      html += `<div style="font-size:11px;color:#71717a;text-transform:uppercase;letter-spacing:0.5px;margin:10px 0 6px">Their Commitments</div>`;
      prospActs.forEach(a => { html += `<div style="margin-bottom:4px;padding-left:16px">&bull; ${a.text}</div>`; });
    }
  }
  const buyingSigs = memoir?.signals?.buying || [];
  const riskSigs = memoir?.signals?.risk || [];
  if (buyingSigs.length > 0 || riskSigs.length > 0) {
    html += `<h2 style="font-size:15px;margin-top:28px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Signals</h2><div style="display:flex;gap:24px">`;
    if (buyingSigs.length > 0) {
      html += `<div style="flex:1"><div style="font-size:11px;color:#059669;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Buying Signals</div>`;
      buyingSigs.forEach((s: string) => { html += `<div style="color:#059669;margin-bottom:4px">&#9650; ${s}</div>`; });
      html += `</div>`;
    }
    if (riskSigs.length > 0) {
      html += `<div style="flex:1"><div style="font-size:11px;color:#e11d48;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Risk Signals</div>`;
      riskSigs.forEach((s: string) => { html += `<div style="color:#e11d48;margin-bottom:4px">&#9660; ${s}</div>`; });
      html += `</div>`;
    }
    html += `</div>`;
  }
  const fields = memoir?.discovery_fields || [];
  if (fields.length > 0) {
    html += `<h2 style="font-size:15px;margin-top:28px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Discovery Fields</h2>`;
    fields.forEach(f => {
      html += `<div style="margin-bottom:8px;padding:8px 12px;background:#fafafa;border-radius:6px"><div style="font-weight:600;color:#52525b;font-size:11px;text-transform:uppercase;letter-spacing:0.5px">${f.name}</div><div>${f.value}</div>`;
      if (f.quote) html += `<div style="font-style:italic;color:#71717a;font-size:12px;margin-top:4px">"${f.quote}"</div>`;
      html += `</div>`;
    });
  }
  if (cm) {
    html += `<h2 style="font-size:15px;margin-top:28px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Coaching Metrics</h2>`;
    if (talkRatio) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Talk Ratio</span><span style="font-weight:500">You ${Math.round(talkRatio.rep_percentage)}% / Them ${Math.round(talkRatio.customer_percentage)}%</span></div>`;
    if (cm.longest_monologue_seconds != null) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Longest Monologue</span><span style="font-weight:500">${formatSeconds(cm.longest_monologue_seconds as number)}</span></div>`;
    if (questionCount) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Questions Asked</span><span style="font-weight:500">You: ${questionCount.rep} / Them: ${questionCount.customer}</span></div>`;
    if (fillerCount && (fillerCount.rep > 0 || fillerCount.customer > 0)) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Filler Words</span><span style="font-weight:500">You: ${fillerCount.rep} / Them: ${fillerCount.customer}</span></div>`;
    if (cm.interruption_count != null && (cm.interruption_count as number) > 0) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Interruptions</span><span style="font-weight:500">${cm.interruption_count}</span></div>`;
    if (cm.average_response_time_seconds != null) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Avg Response Time</span><span style="font-weight:500">${(cm.average_response_time_seconds as number).toFixed(1)}s</span></div>`;
    if (cm.silence_percentage != null) html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f4f4f5"><span style="color:#71717a">Silence</span><span style="font-weight:500">${(cm.silence_percentage as number).toFixed(1)}%</span></div>`;
  }
  if (transcript && transcript.segments.length > 0) {
    html += `<h2 style="font-size:15px;margin-top:28px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e4e4e7">Transcript</h2>`;
    html += `<div style="color:#71717a;margin-bottom:10px;font-size:12px">${transcript.word_count.toLocaleString()} words &middot; ${transcript.speaker_count} speakers</div>`;
    transcript.segments.forEach(seg => {
      const time = seg.original_start != null ? seg.original_start : seg.start;
      const rawSpeaker = seg.speaker || "Speaker";
      const speaker = transcript.speaker_map?.[rawSpeaker]?.name || rawSpeaker;
      const clr = getSpeakerColor(speaker, speakerColorMap);
      html += `<div style="margin-bottom:6px;display:flex;gap:10px"><span style="color:#a1a1aa;font-family:monospace;font-size:11px;min-width:40px;flex-shrink:0">${formatSeconds(time)}</span><div><span style="font-weight:600;color:${clr}">[${speaker}]</span> ${seg.text}</div></div>`;
    });
  }
  if (memoir?.disclaimer) {
    html += `<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:10px 14px;font-size:12px;color:#92400e;margin-top:20px"><strong>Disclaimer:</strong> ${memoir.disclaimer}</div>`;
  }
  html += `<div style="margin-top:32px;padding-top:12px;padding-bottom:8px;border-top:1px solid #e4e4e7;color:#a1a1aa;font-size:11px;line-height:1.6;page-break-inside:avoid">Generated by Memoir &middot; ${new Date().toLocaleDateString()}</div>`;
  html += `</div>`;

  // Dynamic import — html2pdf.js (~180KB) only loads when user clicks Download PDF
  const html2pdf = (await import("html2pdf.js")).default;

  // Two-layer structure:
  //  - Outer wrapper hides the DOM node from the user (opacity:0). This wrapper
  //    is NOT passed to html2pdf, so its styles don't leak into the capture.
  //  - Inner container is what html2pdf clones and hands to html2canvas. It has
  //    only layout styles (width) — no opacity/visibility that would cause the
  //    clone to render transparent in the captured canvas (which was the
  //    root cause of the "empty PDF" bug).
  const wrapper = document.createElement("div");
  wrapper.style.position = "fixed";
  wrapper.style.left = "0";
  wrapper.style.top = "0";
  wrapper.style.opacity = "0";
  wrapper.style.pointerEvents = "none";
  wrapper.style.zIndex = "-1";

  const container = document.createElement("div");
  container.innerHTML = html;
  // A4 at 96dpi = 794px, minus 10mm margins each side (~76px) = ~718px usable
  container.style.width = "710px";

  wrapper.appendChild(container);
  document.body.appendChild(wrapper);

  try {
    await html2pdf()
      .set({
        margin: [10, 10, 15, 10],
        filename: `${title.replace(/[^a-zA-Z0-9 ]/g, "").trim() || "call"}.pdf`,
        image: { type: "jpeg", quality: 0.95 },
        html2canvas: { scale: 2, useCORS: true, logging: false },
        jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
        pagebreak: { mode: ["avoid-all", "css", "legacy"] },
      })
      .from(container)
      .save();
  } finally {
    document.body.removeChild(wrapper);
  }
}

// ─── Tab types ──────────────────────────────────────────────────

type TabKey = "summary" | "transcript" | "crm";

const TABS: { key: TabKey; label: string; icon: React.ReactNode }[] = [
  {
    key: "summary",
    label: "AI Summary",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09Z" />
      </svg>
    ),
  },
  {
    key: "transcript",
    label: "Transcript",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
      </svg>
    ),
  },
  {
    key: "crm",
    label: "CRM Update",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125" />
      </svg>
    ),
  },
];

// ─── Page ───────────────────────────────────────────────────────

export function MeetingDetailV1() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const id = params.id as string;

  const [meeting, setMeeting] = useState<MeetingDetail | null>(null);
  const [memoir, setMemoir] = useState<MemoirResponse | null>(null);
  const [coaching, setCoaching] = useState<CoachingResponse | null>(null);
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);
  const [versions, setVersions] = useState<TranscriptionVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [crmAction, setCrmAction] = useState<"pushing" | "skipping" | null>(null);
  const [crmPushResults, setCrmPushResults] = useState<CrmPushResult[]>([]);
  const [crmConnections, setCrmConnections] = useState<CrmConnection[]>([]);
  const [activeCrmProvider, setActiveCrmProvider] = useState<string>("hubspot");
  const [editingCrmField, setEditingCrmField] = useState<string | null>(null);
  const [editingCrmValue, setEditingCrmValue] = useState("");
  const [retranscribing, setRetranscribing] = useState(false);
  const [regeneratingCrm, setRegeneratingCrm] = useState(false);
  const [crmExtractError, setCrmExtractError] = useState<string | null>(null);
  const [crmExtraction, setCrmExtraction] = useState<Record<string, unknown> | null>(null);
  const [callIntelligence, setCallIntelligence] = useState<CallIntelligenceResponse | null>(null);
  const [includingIntel, setIncludingIntel] = useState(false);
  const [intelIncluded, setIntelIncluded] = useState(false);
  // const [expandedField, setExpandedField] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("summary");
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [speakerInput, setSpeakerInput] = useState("");
  const [teamSpeakers, setTeamSpeakers] = useState<SpeakerProfile[]>([]);
  const [savingSpeaker, setSavingSpeaker] = useState(false);
  const speakerInputRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [audioTime, setAudioTime] = useState(0);
  const [audioDuration, setAudioDuration] = useState(0);
  const [audioSrc, setAudioSrc] = useState<string | null>(null);
  const [audioUrls, setAudioUrls] = useState<Record<string, string>>({});
  const [audioVersion, setAudioVersion] = useState<string>("raw");
  const [quickNotes, setQuickNotes] = useState<string>("");
  const [shareDismissed, setShareDismissed] = useState(false);
  const [sharingExternal, setSharingExternal] = useState(false);
  const [meetingSpeakers, setMeetingSpeakers] = useState<MeetingParticipantInfo[]>([]);
  const [dealTimeline, setDealTimeline] = useState<DealTimelineResponse | null>(null);
  const [rerunningIntel, setRerunningIntel] = useState(false);
  const [showEmailDialog, setShowEmailDialog] = useState(false);
  const [emailTo, setEmailTo] = useState("");
  const [emailPreviewHtml, setEmailPreviewHtml] = useState("");
  const [emailPreviewSubject, setEmailPreviewSubject] = useState("");
  const [emailSending, setEmailSending] = useState(false);
  const [emailSent, setEmailSent] = useState(false);
  const [emailTemplate, setEmailTemplate] = useState("external");
  const [emailLog, setEmailLog] = useState<Array<{ id: string; to_email: string; status: string; created_at: string }>>([]);
  const [processingStatus, setProcessingStatus] = useState<{
    transcription: string;
    summary: string;
    intelligence: string;
    crm_extraction: string;
    active_stage: string;
    stages: Record<string, { percent: number; label: string }>;
  } | null>(null);
  const crmPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const processingPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup CRM + processing polling on unmount
  useEffect(() => {
    return () => {
      if (crmPollRef.current) clearInterval(crmPollRef.current);
      if (processingPollRef.current) clearInterval(processingPollRef.current);
    };
  }, []);

  const startCrmPolling = () => {
    if (crmPollRef.current) clearInterval(crmPollRef.current);
    crmPollRef.current = setInterval(async () => {
      try {
        const status = await api.getCrmRegenerateStatus(id);
        if (status.status === "completed" && status.crm_extraction) {
          setCrmExtraction(status.crm_extraction as Record<string, unknown>);
          setCrmExtractError(null);
          setRegeneratingCrm(false);
          if (crmPollRef.current) clearInterval(crmPollRef.current);
          crmPollRef.current = null;
        } else if (status.status === "failed") {
          setCrmExtractError(status.error || "CRM extraction failed");
          setRegeneratingCrm(false);
          if (crmPollRef.current) clearInterval(crmPollRef.current);
          crmPollRef.current = null;
        }
      } catch {
        setRegeneratingCrm(false);
        if (crmPollRef.current) clearInterval(crmPollRef.current);
        crmPollRef.current = null;
      }
    }, 3000);
  };

  // Fetch quick notes
  useEffect(() => {
    if (!id) return;
    api.getMeetingNotes(id).then((n) => {
      if (n.content) setQuickNotes(n.content);
    }).catch(() => {});
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const m = await api.getMeeting(id);
        if (cancelled) return;
        setMeeting(m);
        const promises: Promise<void>[] = [];
        promises.push(
          api.getVersions(id).then((v) => {
            if (!cancelled && v.versions.length > 0) {
              setVersions(v.versions);
              const isIncomplete = m.status === "failed" || ["pending", "scheduled", "joining", "recording", "processing", "waiting_for_transcription"].includes(m.status);
              let defaultVer: number;
              if (isIncomplete && v.versions.length > 1) {
                defaultVer = v.versions[v.versions.length - 2].version;
              } else {
                defaultVer = v.versions[v.versions.length - 1].version;
              }
              setSelectedVersion(defaultVer);
              api.getTranscript(id, defaultVer).then((t) => { if (!cancelled) setTranscript(t); }).catch(() => {});
              api.getMemoir(id, defaultVer).then((mem) => { if (!cancelled) setMemoir(mem); }).catch(() => {});
              api.getCoaching(id, defaultVer).then((c) => { if (!cancelled) setCoaching(c); }).catch(() => {});
            }
          }).catch(() => {
            if (m.status === "completed" && m.has_transcript) {
              api.getTranscript(id).then((t) => { if (!cancelled) setTranscript(t); }).catch(() => {});
            }
            if (m.status === "completed" && m.has_memoir) {
              api.getMemoir(id).then((mem) => { if (!cancelled) setMemoir(mem); }).catch(() => {});
            }
          })
        );
        await Promise.all(promises);

        // Fetch presigned audio URLs for all versions
        if (m.has_recording) {
          api.getAudioUrls(id).then((urls) => {
            if (!cancelled) {
              setAudioUrls(urls);
              const defaultVersion = urls.preprocessed ? "preprocessed" : urls.raw ? "raw" : Object.keys(urls)[0];
              if (defaultVersion) {
                setAudioVersion(defaultVersion);
                setAudioSrc(urls[defaultVersion]);
              }
            }
          }).catch(() => {
            api.getRecordingPresignedUrl(id).then((url) => { if (!cancelled) setAudioSrc(url); }).catch(() => {});
          });
        }

        // Load CRM data + speakers (non-blocking)
        api.getMeetingSpeakers(id).then((resp) => { if (!cancelled) setMeetingSpeakers(resp.participants); }).catch(() => {});
        api.getCrmPushResults(id).then((results) => { if (!cancelled) setCrmPushResults(results); }).catch(() => {});
        api.getCallIntelligence(id).then((ci) => {
          if (!cancelled && ci) {
            setCallIntelligence(ci);
            if (ci.crm_extraction) setCrmExtraction(ci.crm_extraction as Record<string, unknown>);
          }
        }).catch(() => {});
        // Fetch deal timeline if meeting is linked to a deal
        if (m.memoir_deal_id) {
          api.getDealTimeline(m.memoir_deal_id).then((tl) => {
            if (!cancelled && tl && tl.entries.length > 1) setDealTimeline(tl);
          }).catch(() => {});
        }
        // Check for in-progress CRM extraction job (resume polling after page reload)
        api.getCrmRegenerateStatus(id).then((crmStatus) => {
          if (cancelled) return;
          if (crmStatus.status === "waiting" || crmStatus.status === "in_progress") {
            setRegeneratingCrm(true);
            startCrmPolling();
          } else if (crmStatus.status === "completed" && crmStatus.crm_extraction) {
            setCrmExtraction(crmStatus.crm_extraction as Record<string, unknown>);
            setCrmExtractError(null);
          } else if (crmStatus.status === "failed") {
            setCrmExtractError(crmStatus.error || "CRM extraction failed");
          }
        }).catch(() => {});
        api.getCrmConnections().then((conns) => {
          if (!cancelled) {
            setCrmConnections(conns);
            // Set default active provider tab
            if (conns.length > 0) setActiveCrmProvider(conns[0].provider);
          }
        }).catch(() => {});
      } catch {
        if (!cancelled) setError("Failed to load meeting");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [id]);

  // Poll processing status when intelligence or meeting is in progress
  useEffect(() => {
    if (!meeting) return;
    const shouldPoll =
      meeting.intelligence_status === "processing" ||
      meeting.intelligence_status === "pending" ||
      ["processing", "waiting_for_transcription"].includes(meeting.status) ||
      (processingStatus && (processingStatus.crm_extraction === "waiting" || processingStatus.crm_extraction === "in_progress"));
    if (!shouldPoll) {
      if (processingPollRef.current) {
        clearInterval(processingPollRef.current);
        processingPollRef.current = null;
      }
      return;
    }
    // Fetch immediately, then every 8s
    const poll = async () => {
      try {
        const status = await api.getProcessingStatus(id);
        setProcessingStatus(status);
        // Keep meeting.intelligence_status in sync — but NOT "completed" (handled separately to trigger data refetch)
        if (status.intelligence !== "completed") {
          setMeeting((prev) => prev ? { ...prev, intelligence_status: status.intelligence } : prev);
        }
        // When transcription completes, reload the full meeting + transcript data
        if (status.transcription === "completed" && meeting.status !== "completed") {
          const updatedMeeting = await api.getMeeting(id);
          setMeeting(updatedMeeting);
          if (selectedVersion != null || updatedMeeting.status === "completed") {
            const v = selectedVersion ?? 1;
            api.getTranscript(id, v).then((t) => { if (t) setTranscript(t); }).catch(() => {});
            api.getCoaching(id, v).then((c) => { if (c) setCoaching(c); }).catch(() => {});
            api.getMemoir(id, v).then((m) => { if (m) setMemoir(m); }).catch(() => {});
          }
          api.getVersions(id).then((v) => { if (v.versions.length > 0) setVersions(v.versions); }).catch(() => {});
        }
        // When intelligence transitions to completed, refetch all data
        if (status.intelligence === "completed" && meeting.intelligence_status !== "completed") {
          const v = selectedVersion ?? undefined;
          Promise.all([
            api.getCallIntelligence(id).catch(() => null),
            api.getMemoir(id, v).catch(() => null),
            api.getMeeting(id).catch(() => null),
          ]).then(([ci, mem, mtg]) => {
            if (ci) {
              setCallIntelligence(ci);
              if (ci.crm_extraction) setCrmExtraction(ci.crm_extraction as Record<string, unknown>);
            }
            if (mem) setMemoir(mem);
            if (mtg) setMeeting(mtg);
            else setMeeting((prev) => prev ? { ...prev, intelligence_status: "completed" } : prev);
          });
        }
        // Stop polling when everything is done
        const allDone = status.intelligence === "completed" && status.crm_extraction === "completed";
        if (allDone) {
          setMeeting((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              intelligence_status: status.intelligence === "completed" ? "completed" : prev.intelligence_status,
              status: status.transcription === "completed" && status.summary === "completed" ? "completed" : prev.status,
            };
          });
          if (processingPollRef.current) {
            clearInterval(processingPollRef.current);
            processingPollRef.current = null;
          }
        }
      } catch { /* silent */ }
    };
    poll();
    processingPollRef.current = setInterval(poll, 3000);
    return () => {
      if (processingPollRef.current) {
        clearInterval(processingPollRef.current);
        processingPollRef.current = null;
      }
    };
  }, [id, meeting?.intelligence_status, meeting?.status]);

  // ─── Fast-summary preview fetch ─────────────────────────────────
  // When Type 7 completes (operation_type=7 → fast_summary stage hits 100%
  // in Redis), fetch the memoir endpoint AND the transcript endpoint.
  // Backend's fast-P5 fallbacks serve summary_fast.json + transcript_segments_
  // fast.json when the polished outputs aren't ready yet, letting the UI
  // render a read-only preview while Type 6/5/3/2 finish.
  useEffect(() => {
    if (!meeting) return;
    const fastReady = (processingStatus?.stages?.fast_summary?.percent ?? 0) >= 100;
    const polishedDone = meeting.status === "completed";
    if (!fastReady || polishedDone) return;
    let cancelled = false;
    if (!memoir) {
      api.getMemoir(id).then((m) => {
        if (!cancelled && m) setMemoir(m);
      }).catch(() => { /* silent — fast-P5 fallback may not be ready yet */ });
    }
    if (!transcript) {
      api.getTranscript(id).then((t) => {
        if (!cancelled && t) setTranscript(t);
      }).catch(() => { /* silent — fast transcript may not be ready yet */ });
    }
    return () => { cancelled = true; };
  }, [id, processingStatus?.stages?.fast_summary?.percent, meeting?.status, memoir, transcript]);

  const handleVersionChange = async (ver: number) => {
    setSelectedVersion(ver);
    try {
      const [t, mem, c] = await Promise.all([
        api.getTranscript(id, ver).catch(() => null),
        api.getMemoir(id, ver).catch(() => null),
        api.getCoaching(id, ver).catch(() => null),
      ]);
      if (t) setTranscript(t);
      if (mem) setMemoir(mem);
      if (c) setCoaching(c);
    } catch { /* silent */ }
  };

  // ─── CRM actions ──────────────────────────────────────────────

  const handlePushCrm = async (provider?: string) => {
    const p = provider || activeCrmProvider;
    if (!p) return;
    setCrmAction("pushing");
    try {
      const result = await api.pushToCrm(id, p);
      setCrmPushResults((prev) => {
        const others = prev.filter((r) => r.provider !== p);
        return [...others, result];
      });
      setMeeting((prev) => prev ? { ...prev, crm_push_status: "pushed" } : prev);
    } catch { /* silent */ } finally { setCrmAction(null); }
  };

  const handleSkipCrm = async (provider?: string) => {
    const p = provider || activeCrmProvider;
    if (!p) return;
    setCrmAction("skipping");
    try {
      await api.skipCrmPush(id, p);
      setCrmPushResults((prev) => {
        const others = prev.filter((r) => r.provider !== p);
        return [...others, { provider: p, status: "skipped" } as CrmPushResult];
      });
      setMeeting((prev) => prev ? { ...prev, crm_push_status: "skipped" } : prev);
    } catch { /* silent */ } finally { setCrmAction(null); }
  };

  const handleEditCrmField = async (provider: string, fieldKey: string, value: string) => {
    try {
      const existingResult = crmPushResults.find((r) => r.provider === provider);
      const currentData = JSON.parse(JSON.stringify(existingResult?.edited_data || existingResult?.pushed_data || {}));
      // fieldKey can be dotted like "contact.email" — deep merge
      const parts = fieldKey.split(".");
      if (parts.length === 2) {
        const [parentKey, childKey] = parts;
        if (typeof currentData[parentKey] === "object" && currentData[parentKey] !== null) {
          currentData[parentKey] = { ...currentData[parentKey], [childKey]: value };
        } else {
          currentData[parentKey] = { [childKey]: value };
        }
      } else {
        currentData[fieldKey] = value;
      }
      await api.editCrmPushData(id, provider, currentData);
      setCrmPushResults((prev) =>
        prev.map((r) => r.provider === provider ? { ...r, edited_data: currentData as Record<string, unknown> } : r)
      );
      setEditingCrmField(null);
    } catch { /* silent */ }
  };

  const handleRegenerateCrmExtraction = async () => {
    setRegeneratingCrm(true);
    setCrmExtractError(null);
    try {
      await api.regenerateCrmExtraction(id);
      startCrmPolling();
    } catch { setRegeneratingCrm(false); }
  };

  const handleRetranscribe = async () => {
    setRetranscribing(true);
    try {
      await api.retranscribe(id);
      setMeeting((prev) => prev ? { ...prev, status: "waiting_for_transcription" } : prev);
      setTranscript(null);
      setMemoir(null);
    } catch { /* silent */ } finally { setRetranscribing(false); }
  };

  const handleRerunIntelligence = async () => {
    setRerunningIntel(true);
    try {
      await api.rerunIntelligence(id);
      setMeeting((prev) => prev ? { ...prev, intelligence_status: "processing" } : prev);
    } catch { /* silent */ } finally { setRerunningIntel(false); }
  };

  const handleIncludeInIntelligence = async () => {
    setIncludingIntel(true);
    try {
      await api.includeInIntelligence(id);
      setIntelIncluded(true);
    } catch { /* silent */ } finally { setIncludingIntel(false); }
  };

  // ─── Audio controls ───────────────────────────────────────────

  const togglePlay = () => {
    if (!audioRef.current) return;
    if (audioPlaying) { audioRef.current.pause(); } else { audioRef.current.play(); }
  };

  const seekTo = (seconds: number) => {
    if (!audioRef.current) return;
    audioRef.current.currentTime = seconds;
    setAudioTime(seconds);
  };

  const handleScrub = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!audioRef.current || !audioDuration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seekTo(pct * audioDuration);
  };

  // ─── Auto-seek from ?t= query param ─────────────────────────
  const seekedRef = useRef(false);

  // Primary seek: triggered when audio metadata loads
  const handleLoadedMetadata = () => {
    setAudioDuration(audioRef.current?.duration || 0);
    const t = searchParams.get("t");
    if (t && audioRef.current && !seekedRef.current) {
      const seconds = parseInt(t, 10);
      if (!isNaN(seconds) && seconds >= 0) {
        seekedRef.current = true;
        audioRef.current.currentTime = seconds;
      }
    }
  };

  // Fallback seek: re-runs when loading completes (audio element now in DOM)
  useEffect(() => {
    const t = searchParams.get("t");
    if (t && audioRef.current && !seekedRef.current) {
      const seconds = parseInt(t, 10);
      if (!isNaN(seconds) && seconds >= 0) {
        seekedRef.current = true;
        const trySeek = () => {
          if (audioRef.current && audioRef.current.readyState >= 1) {
            audioRef.current.currentTime = seconds;
          } else {
            setTimeout(trySeek, 200);
          }
        };
        trySeek();
      }
    }
  }, [searchParams, loading]);

  // ─── Speaker editing ─────────────────────────────────────────

  const speakerToLabel = new Map<string, string>();
  if (transcript?.speaker_map) {
    for (const [label, entry] of Object.entries(transcript.speaker_map)) {
      speakerToLabel.set(entry.name, label);
    }
  }

  const handleSpeakerClick = async (speaker: string) => {
    setEditingSpeaker(speaker);
    setSpeakerInput("");
    const user = useAuthStore.getState().user;
    if (user?.team_id) {
      try {
        const profiles = await api.getTeamSpeakers(user.team_id);
        setTeamSpeakers(profiles);
      } catch { setTeamSpeakers([]); }
    }
    setTimeout(() => speakerInputRef.current?.focus(), 50);
  };

  const handleSpeakerSave = async (originalSpeaker: string, newName: string, profileId?: string) => {
    if (!newName.trim() || savingSpeaker) return;
    setSavingSpeaker(true);
    try {
      const diarizationLabel = speakerToLabel.get(originalSpeaker) || originalSpeaker;
      await api.assignSpeaker(id, { diarization_label: diarizationLabel, speaker_name: newName.trim(), speaker_profile_id: profileId });
      const ver = selectedVersion ?? undefined;
      const t = await api.getTranscript(id, ver);
      setTranscript(t);
    } catch { /* silent */ } finally { setSavingSpeaker(false); setEditingSpeaker(null); }
  };


  // ─── Back button ──────────────────────────────────────────────

  const handleBack = () => {
    const searchBack = sessionStorage.getItem("memoir_search_back");
    if (searchBack) {
      sessionStorage.removeItem("memoir_search_back");
      router.push(`/meetings?search=${encodeURIComponent(searchBack)}`);
    } else {
      router.back();
    }
  };

  const backButton = (
    <button
      onClick={handleBack}
      className="text-sm text-memoir-text-secondary hover:text-memoir-text mb-4 flex items-center gap-1"
    >
      &larr; Back
    </button>
  );

  // ─── Loading ──────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto">
        {backButton}
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-12 text-center">
          <div className="animate-spin w-6 h-6 border-2 border-memoir-border border-t-memoir-indigo rounded-full mx-auto mb-3" />
          <p className="text-sm text-memoir-text-muted">Loading call details&hellip;</p>
        </div>
      </div>
    );
  }

  // ─── Error / Not found ────────────────────────────────────────

  if (error || !meeting) {
    return (
      <div className="max-w-5xl mx-auto">
        {backButton}
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-12 text-center">
          <div className="w-12 h-12 bg-memoir-bg rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m5.231 13.481L15 17.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v16.5c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Zm3.75 11.625a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-memoir-text mb-1">Call not found</h3>
          <p className="text-sm text-memoir-text-muted">{error || "This call doesn\u2019t exist or you don\u2019t have access to it."}</p>
        </div>
      </div>
    );
  }

  // ─── Failed (no previous versions) ──────────────────────────

  if (meeting.status === "failed" && versions.length === 0) {
    return (
      <div className="max-w-5xl mx-auto">
        {backButton}
        <div className="mb-6">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-xl font-bold text-memoir-text">{meeting.title || "Untitled Call"}</h1>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <span className="inline-flex items-center gap-1 text-xs text-memoir-text-muted">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
                  </svg>
                  {formatDate(meeting.created_at)}
                </span>
                {meeting.source && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-memoir-bg text-memoir-text-secondary border border-memoir-border">
                    {getMeetingSourceLabel({ source: meeting.source, bot_job_id: meeting.bot_job_id })}
                  </span>
                )}
                <StatusBadge status={meeting.status} />
              </div>
            </div>
            <button onClick={handleRetranscribe} disabled={retranscribing}
              className="px-3 py-1.5 text-xs font-medium text-memoir-text-secondary bg-memoir-surface border border-memoir-border rounded-button hover:border-memoir-indigo/30 transition-colors disabled:opacity-50">
              {retranscribing ? "Queuing\u2026" : "Retranscribe"}
            </button>
          </div>
        </div>
        <div className="bg-red-50 border border-red-200 rounded-card p-5">
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 bg-red-100 rounded-full flex items-center justify-center flex-shrink-0">
              <svg className="w-4 h-4 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <div>
              <h3 className="text-sm font-semibold text-red-800">Recording failed</h3>
              <p className="text-sm text-red-600 mt-1">The bot was unable to complete the recording.</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ─── In-progress ──────────────────────────────────────────────

  const inProgressStatuses = ["pending", "scheduled", "joining", "recording", "processing", "waiting_for_transcription"];
  const hasPreviousVersions = versions.length > 0;

  // Read-only preview state: when Type 7 (operation_type=7) has completed —
  // signaled by `fast_summary` stage hitting 100% in Redis — we have the
  // fast summary in R2 and the backend memoir endpoint serves it via the
  // fast-P5 fallback. Render the existing memoir UI but with readOnly=true
  // until the polished pipeline (Type 5) flips meeting.status to "completed".
  const fastSummaryReady = (processingStatus?.stages?.fast_summary?.percent ?? 0) >= 100;
  const polishedComplete = meeting.status === "completed";
  const showReadOnlyPreview = inProgressStatuses.includes(meeting.status)
    && fastSummaryReady
    && !!memoir
    && !polishedComplete
    && !hasPreviousVersions;
  // When the preview is showing OR the polished pipeline isn't done, edit
  // affordances stay locked. Toggles to false the moment status flips to
  // "completed" so users get full editing without a reload.
  const memoirReadOnly = !polishedComplete;

  if (inProgressStatuses.includes(meeting.status) && !showReadOnlyPreview && !hasPreviousVersions) {
    const statusMessages: Record<string, { title: string; description: string; pulse?: boolean }> = {
      pending: { title: "Bot is being prepared\u2026", description: "The recording bot will join the meeting shortly." },
      scheduled: { title: "Bot is scheduled", description: "The bot will join when the meeting starts." },
      joining: { title: "Waiting in meeting lobby\u2026", description: "The bot is waiting to be admitted to the meeting." },
      recording: { title: "Recording in progress\u2026", description: "The bot is currently recording this meeting.", pulse: true },
      processing: { title: "Processing transcription\u2026", description: "The recording is being processed and transcribed." },
      waiting_for_transcription: { title: "Processing transcription\u2026", description: "The recording is being transcribed. This usually takes a few minutes." },
    };
    const msg = statusMessages[meeting.status] || statusMessages.pending;
    return (
      <div className="max-w-5xl mx-auto">
        {backButton}
        <div className="mb-6 flex items-center gap-3">
          <div>
            <h1 className="text-xl font-bold text-memoir-text">{meeting.title || "Untitled Call"}</h1>
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <span className="inline-flex items-center gap-1 text-xs text-memoir-text-muted">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
                </svg>
                {formatDate(meeting.created_at)}
              </span>
              {meeting.source && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-memoir-bg text-memoir-text-secondary border border-memoir-border">
                  {meeting.source === "zoom_cloud" ? "Zoom Cloud" : meeting.source === "calendar" ? "Calendar" : meeting.source === "upload" ? "Upload" : meeting.source === "extension_tab_audio" ? "Extension Recording" : meeting.source === "web_recorder" ? "Web Recorder" : meeting.source === "extension" ? "Extension" : meeting.source === "manual" ? "Bot" : meeting.source}
                </span>
              )}
              <StatusBadge status={meeting.status === "completed" && (meeting.intelligence_status && meeting.intelligence_status !== "completed" || (processingStatus && (processingStatus.crm_extraction === "waiting" || processingStatus.crm_extraction === "in_progress"))) ? "analyzing" : meeting.status} />
            </div>
          </div>
        </div>
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-6">
          {(meeting.status === "processing" || meeting.status === "waiting_for_transcription") && processingStatus ? (
            <>
              <div className="mb-3">
                <span className="text-sm font-semibold text-memoir-text">{msg.title}</span>
              </div>
              <div className="space-y-3">
                {[
                  { key: "preprocess", label: "Preprocessing" },
                  { key: "enhance", label: "Enhancing audio" },
                  { key: "diarization", label: "Identifying speakers" },
                  { key: "fast_summary", label: "Preview summary" },
                  { key: "transcribe", label: "Transcribing" },
                  { key: "ai_analysis", label: "AI analysis" },
                  { key: "crm_sync", label: "CRM sync" },
                ].map((stage) => {
                  const data = processingStatus.stages?.[stage.key];
                  const pct = data?.percent ?? 0;
                  const stageLabel = data?.label || "";
                  const isActive = processingStatus.active_stage === stage.key;
                  const isComplete = pct >= 100;
                  const isPending = pct === 0 && !isActive;
                  return (
                    <div key={stage.key} className={isPending ? "opacity-40" : ""}>
                      <div className="flex items-center justify-between mb-1">
                        <div className="flex items-center gap-1.5 text-xs">
                          {isComplete ? (
                            <svg className="w-3.5 h-3.5 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                            </svg>
                          ) : isActive ? (
                            <div className="w-3 h-3 border-2 border-memoir-indigo border-t-transparent rounded-full animate-spin" />
                          ) : (
                            <div className="w-2.5 h-2.5 rounded-full bg-memoir-border" />
                          )}
                          <span className={isActive ? "text-memoir-indigo font-medium" : isComplete ? "text-memoir-text" : "text-memoir-text-muted"}>{stage.label}</span>
                        </div>
                        {(isActive || isComplete) && <span className="text-xs font-semibold text-memoir-indigo">{pct}%</span>}
                      </div>
                      <div className="w-full h-1.5 bg-memoir-border/30 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${isComplete ? "bg-emerald-500" : "bg-memoir-indigo"}`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      {isActive && stageLabel && (
                        <p className="text-[10px] text-memoir-text-muted mt-0.5">{stageLabel}</p>
                      )}
                      {isComplete && stageLabel && stageLabel.toLowerCase().includes("skipped") && (
                        <p className="text-[10px] text-amber-600 mt-0.5">{stageLabel}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="flex items-center gap-3">
              {msg.pulse ? (
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500" />
                </span>
              ) : (
                <div className="animate-spin w-5 h-5 border-2 border-memoir-border border-t-memoir-indigo rounded-full" />
              )}
              <div>
                <h3 className="text-sm font-semibold text-memoir-text">{msg.title}</h3>
                <p className="text-sm text-memoir-text-muted mt-0.5">{msg.description}</p>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ─── Processing / Failed banners ──────────────────────────────

  const processingBanner = inProgressStatuses.includes(meeting.status) && hasPreviousVersions ? (
    <div className="mb-4 bg-amber-50 border border-amber-200 rounded-card px-4 py-3">
      <div className="flex items-center gap-3 mb-2">
        <div className="animate-spin w-4 h-4 border-2 border-amber-300 border-t-amber-600 rounded-full flex-shrink-0" />
        <p className="text-sm text-amber-800">
          <span className="font-medium">New version processing&hellip;</span>{" "}Showing previous version below.
        </p>
      </div>
      {(meeting.status === "processing" || meeting.status === "waiting_for_transcription") && processingStatus && (
        <div className="space-y-2 mt-3">
          {[
            { key: "preprocess", label: "Preprocessing" },
            { key: "enhance", label: "Enhancing audio" },
            { key: "diarization", label: "Identifying speakers" },
            { key: "fast_summary", label: "Preview summary" },
            { key: "transcribe", label: "Transcribing" },
            { key: "ai_analysis", label: "AI analysis" },
            { key: "crm_sync", label: "CRM sync" },
          ].map((stage) => {
            const data = processingStatus.stages?.[stage.key];
            const pct = data?.percent ?? 0;
            const stageLabel = data?.label || "";
            const isActive = processingStatus.active_stage === stage.key;
            const isComplete = pct >= 100;
            const isPending = pct === 0 && !isActive;
            return (
              <div key={stage.key} className={isPending ? "opacity-40" : ""}>
                <div className="flex items-center justify-between mb-0.5">
                  <div className="flex items-center gap-1.5 text-xs">
                    {isComplete ? (
                      <svg className="w-3 h-3 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                      </svg>
                    ) : isActive ? (
                      <div className="w-2.5 h-2.5 border-2 border-amber-600 border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <div className="w-2 h-2 rounded-full bg-amber-200" />
                    )}
                    <span className={isActive ? "text-amber-800 font-medium" : isComplete ? "text-amber-900" : "text-amber-600"}>{stage.label}</span>
                  </div>
                  {(isActive || isComplete) && <span className="text-xs font-semibold text-amber-700">{pct}%</span>}
                </div>
                <div className="w-full h-1 bg-amber-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${isComplete ? "bg-emerald-500" : "bg-amber-500"}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                {isActive && stageLabel && (
                  <p className="text-[10px] text-amber-700 mt-0.5">{stageLabel}</p>
                )}
                {isComplete && stageLabel && stageLabel.toLowerCase().includes("skipped") && (
                  <p className="text-[10px] text-amber-600 mt-0.5">{stageLabel}</p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  ) : null;

  const failedBanner = meeting.status === "failed" && hasPreviousVersions ? (
    <div className="mb-4 bg-red-50 border border-red-200 rounded-card px-4 py-3">
      <div className="flex items-center gap-3">
        <svg className="w-4 h-4 text-red-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
        <p className="text-sm text-red-800">
          <span className="font-medium">Latest transcription failed.</span>{" "}Showing previous version below.
        </p>
      </div>
    </div>
  ) : null;

  // fast_email_only banner — T6 retry failed, so the polished pipeline never
  // ran. Fast email was sent best-effort. User can retranscribe to retry.
  const fastEmailOnlyBanner = meeting.status === "fast_email_only" ? (
    <div className="mb-4 bg-amber-50 border border-amber-200 rounded-card px-4 py-3">
      <div className="flex items-start gap-3">
        <svg className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
        </svg>
        <div>
          <p className="text-sm font-medium text-amber-800">Polished version unavailable</p>
          <p className="text-xs text-amber-700 mt-1">
            Diarization failed for this meeting, so the polished transcript, intelligence, and CRM extraction are not available. The fast email summary was still sent. You can retry by clicking <span className="font-medium">Retranscribe</span> below.
          </p>
        </div>
      </div>
    </div>
  ) : null;

  // Preview banner — visible while the read-only fast summary is showing
  // (Type 7 done, polished pipeline still running). The active stage label
  // surfaces what the pipeline is currently doing so the user knows the
  // page will auto-unlock for editing when polished completes.
  const previewBanner = showReadOnlyPreview ? (() => {
    const activeStageKey = processingStatus?.active_stage || "";
    const stageLabelMap: Record<string, string> = {
      diarization: "Identifying speakers",
      transcribe: "Transcribing",
      ai_analysis: "AI analysis",
      crm_sync: "CRM sync",
    };
    const activeLabel = stageLabelMap[activeStageKey] || "Finalizing";
    const activePct = processingStatus?.stages?.[activeStageKey]?.percent ?? 0;
    return (
      <div className="mb-4 bg-blue-50 border border-blue-200 rounded-card px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="animate-spin w-4 h-4 border-2 border-blue-300 border-t-blue-600 rounded-full flex-shrink-0" />
          <div className="flex-1">
            <p className="text-sm text-blue-900">
              <span className="font-medium">Preview summary</span>
              {" — "}full version processing
              {activeStageKey && (
                <>
                  {" ("}
                  <span className="text-blue-700">{activeLabel}{activePct > 0 ? ` ${activePct}%` : "…"}</span>
                  {")"}
                </>
              )}
              . Editing will be enabled when complete.
            </p>
          </div>
        </div>
      </div>
    );
  })() : null;

  // ─── Completed — data prep ────────────────────────────────────

  const resolved = resolveMemoir(memoir, transcript?.speaker_map) ?? memoir;
  const actionItemsRep = resolved?.action_items?.filter((a) => a.type === "rep") || [];
  const actionItemsProspect = resolved?.action_items?.filter((a) => a.type === "prospect") || [];
  const hasActionItems = actionItemsRep.length > 0 || actionItemsProspect.length > 0;
  const buyingSignals = resolved?.signals?.buying || [];
  const riskSignals = resolved?.signals?.risk || [];
  const hasSignals = buyingSignals.length > 0 || riskSignals.length > 0;
  const crmFields = resolved?.discovery_fields || [];
  // const crmStatus = meeting.crm_push_status;
  const nextSteps = resolved?.next_steps;
  const callMeta = resolved?.call_metadata;
  const cm = coaching?.coaching_metrics as Record<string, unknown> | undefined;

  const dedupedParticipants = (() => {
    // Build name set from speaker_map (ground truth — manually corrected names)
    const speakerMapNames = new Set<string>();
    if (transcript?.speaker_map) {
      for (const entry of Object.values(transcript.speaker_map)) {
        if (entry.name) speakerMapNames.add(entry.name);
      }
    }

    const participants = callMeta?.participants;
    if (!participants?.length) {
      // No LLM participants — build from speaker_map if available
      if (speakerMapNames.size > 0) {
        return Array.from(speakerMapNames).map((name) => ({
          name,
          title: null as string | null,
          company: null as string | null,
          role: "participant",
        }));
      }
      return participants;
    }

    // Merge: start with LLM participants, override names from speaker_map.
    // Skip any LLM participant whose name is null/empty — memoir extraction
    // can occasionally return participants with missing names, and the
    // downstream render (.toLowerCase on p.name in Speakers list) crashes
    // the whole detail page if we let them through.
    const seen = new Map<string, typeof participants[0]>();
    for (const p of participants) {
      if (!p?.name || typeof p.name !== "string") continue;
      const existing = seen.get(p.name);
      if (!existing) { seen.set(p.name, { ...p }); }
      else { if (!existing.role || existing.role === "other") existing.role = p.role; if (!existing.title && p.title) existing.title = p.title; if (!existing.company && p.company) existing.company = p.company; }
    }

    // If speaker_map has names not in participants, add them; also replace
    // LLM-inferred names with speaker_map names when they likely refer to same person
    if (speakerMapNames.size > 0) {
      const result = new Map<string, typeof participants[0]>();
      // If there's only 1 LLM participant and 1 speaker_map name, they're the same person
      const llmNames = Array.from(seen.keys());
      const mapNames = Array.from(speakerMapNames);
      if (llmNames.length === 1 && mapNames.length === 1 && llmNames[0] !== mapNames[0]) {
        const entry = seen.get(llmNames[0])!;
        result.set(mapNames[0], { ...entry, name: mapNames[0] });
      } else {
        // Copy existing, add missing
        Array.from(seen.entries()).forEach(([name, p]) => result.set(name, p));
        Array.from(speakerMapNames).forEach((name) => {
          if (!result.has(name)) {
            result.set(name, { name, title: null, company: null, role: "participant" });
          }
        });
      }
      return Array.from(result.values());
    }

    return Array.from(seen.values());
  })();

  const correctedTalkRatio = (() => {
    const rawRatio = cm?.talk_ratio as { rep_percentage: number; customer_percentage: number; assessment: string } | undefined;
    if (!rawRatio || !transcript?.segments?.length || !transcript?.speaker_map) return rawRatio;
    const speakerMap = transcript.speaker_map;
    const resolvedNames = new Set<string>();
    for (const seg of transcript.segments) {
      const rawLabel = seg.speaker || "";
      const name = speakerMap[rawLabel]?.name || rawLabel;
      resolvedNames.add(name);
    }
    if (resolvedNames.size <= 1) return { rep_percentage: 100, customer_percentage: 0, assessment: "solo" };
    return rawRatio;
  })();

  const isProcessing = inProgressStatuses.includes(meeting.status);
  const talkRatio = correctedTalkRatio;
  const longestMonologue = cm?.longest_monologue_seconds as number | undefined;
  const questionCount = cm?.question_count as { rep: number; customer: number } | undefined;
  const fillerCount = cm?.filler_word_count as { rep: number; customer: number; rep_details?: Record<string, number> } | undefined;
  const interruptionCount = cm?.interruption_count as number | undefined;
  const avgResponseTime = cm?.average_response_time_seconds as number | undefined;
  const silencePercent = cm?.silence_percentage as number | undefined;

  // ─── Key moments extraction ───────────────────────────────────

  const keyMoments: { label: string; time: number; type: string }[] = [];
  if (transcript?.segments && resolved) {
    // Extract moments from signals with approximate timestamps
    const segmentTexts = transcript.segments;
    const findTimestamp = (text: string): number | null => {
      const lower = text.toLowerCase();
      for (const seg of segmentTexts) {
        if (seg.text.toLowerCase().includes(lower.slice(0, 30))) {
          return seg.original_start ?? seg.start;
        }
      }
      return null;
    };

    buyingSignals.slice(0, 3).forEach((s: string) => {
      const t = findTimestamp(s);
      if (t != null) keyMoments.push({ label: s, time: t, type: "buying" });
    });
    riskSignals.slice(0, 3).forEach((s: string) => {
      const t = findTimestamp(s);
      if (t != null) keyMoments.push({ label: s, time: t, type: "risk" });
    });
    keyMoments.sort((a, b) => a.time - b.time);
  }

  // ─── Completed view ───────────────────────────────────────────

  return (
    <div className="max-w-5xl mx-auto">
      {backButton}
      {processingBanner}
      {failedBanner}
      {fastEmailOnlyBanner}
      {previewBanner}

      {/* Intelligence opt-in banner */}
      {meeting.status === "completed" && meeting.intelligence_status === "completed" && !resolved?.narrative_summary && !intelIncluded && (
        <div className="mb-4 bg-memoir-bg border border-memoir-border rounded-card px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <svg className="w-4 h-4 text-memoir-text-muted flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09Z" />
              </svg>
              <p className="text-sm text-memoir-text-secondary">
                No intelligence data for this call. It may have been skipped as internal or too short.
              </p>
            </div>
            <button
              onClick={handleIncludeInIntelligence}
              disabled={includingIntel}
              className="text-xs font-medium text-memoir-indigo hover:text-memoir-indigo-hover whitespace-nowrap disabled:opacity-50"
            >
              {includingIntel ? "Analysing\u2026" : "Run Intelligence analysis \u2192"}
            </button>
          </div>
        </div>
      )}
      {intelIncluded && (
        <div className="mb-4 bg-emerald-50 border border-emerald-200 rounded-card px-4 py-3">
          <div className="flex items-center gap-3">
            <svg className="w-4 h-4 text-emerald-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
            </svg>
            <p className="text-sm text-emerald-700">Intelligence analysis queued. Results will appear after processing.</p>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="mb-4">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-xl font-bold font-display text-memoir-text">{meeting.title || "Untitled Call"}</h1>
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <span className="inline-flex items-center gap-1 text-xs text-memoir-text-muted">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
                </svg>
                {formatDate(meeting.created_at)}
              </span>
              {meeting.duration_seconds != null && (
                <span className="inline-flex items-center gap-1 text-xs text-memoir-text-muted">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                  </svg>
                  {formatDuration(meeting.duration_seconds)}
                </span>
              )}
              {meeting.source && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-memoir-bg text-memoir-text-secondary border border-memoir-border">
                  {meeting.source === "zoom_cloud" ? "Zoom Cloud" : meeting.source === "calendar" ? "Calendar" : meeting.source === "upload" ? "Upload" : meeting.source === "extension_tab_audio" ? "Extension Recording" : meeting.source === "web_recorder" ? "Web Recorder" : meeting.source === "extension" ? "Extension" : meeting.source === "manual" ? "Bot" : meeting.source}
                </span>
              )}
              {callMeta?.call_type && callMeta.call_type !== "Other" && callMeta.call_type !== "null" && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-memoir-indigo/10 text-memoir-indigo border border-memoir-indigo/20">
                  {callMeta.call_type}
                </span>
              )}
              <StatusBadge status={meeting.status === "completed" && (meeting.intelligence_status && meeting.intelligence_status !== "completed" || (processingStatus && (processingStatus.crm_extraction === "waiting" || processingStatus.crm_extraction === "in_progress"))) ? "analyzing" : meeting.status} />
            </div>
          </div>
          <ShareMenu
            onDownloadPdf={() => downloadPdf(meeting, memoir, coaching, transcript)}
            transcript={transcript}
            meeting={meeting}
            audioSrc={audioSrc}
            audioDuration={audioDuration}
          />
        </div>
      </div>

      {/* Deal Timeline + Version controls card */}
      <div className="mb-5 bg-memoir-surface border border-memoir-border rounded-card px-4 py-3">
        {/* Top row: deal info (left) + version/retranscribe/confidence (right) */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          {/* Left: deal info */}
          {dealTimeline && dealTimeline.entries.length > 1 ? (
            <div className="flex items-center gap-2">
              <svg className="w-3.5 h-3.5 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" />
              </svg>
              <span className="text-xs font-medium text-memoir-text">
                {dealTimeline.prospect_company || "Deal"} &middot; {dealTimeline.total_calls} calls
              </span>
              {callIntelligence?.crm_stage_label && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-memoir-indigo/10 text-memoir-indigo border border-memoir-indigo/20">
                  {callIntelligence.crm_stage_label}
                </span>
              )}
            </div>
          ) : <div />}
          {/* Right: version + retranscribe + confidence */}
          <div className="flex items-center gap-2 flex-wrap">
            {versions.length > 0 && (
              <select value={selectedVersion ?? ""} onChange={(e) => handleVersionChange(Number(e.target.value))}
                className="px-2 py-1.5 text-xs font-medium text-memoir-text-secondary bg-memoir-bg border border-memoir-border rounded-button hover:border-memoir-indigo/30 transition-colors">
                {versions.map((v, i) => {
                  const isFailed = meeting.status === "failed" && i === versions.length - 1;
                  const isInProg = inProgressStatuses.includes(meeting.status) && i === versions.length - 1 && versions.length > 1;
                  const src = v.recording_source === "zoom_cloud" ? "Zoom Cloud" : v.recording_source === "upload" ? "Upload" : v.recording_source === "web_recorder" ? "Web Recorder" : v.recording_source === "extension_tab_audio" ? "Extension Tab Recording" : v.recording_source === "extension" ? "Extension Bot Recording" : "Bot";
                  return <option key={v.version} value={v.version}>v{v.version} ({src}){isFailed ? " - failed" : isInProg ? " - processing" : ""}</option>;
                })}
              </select>
            )}
            <button onClick={handleRetranscribe} disabled={retranscribing || isProcessing}
              className="px-3 py-1.5 text-xs font-medium text-memoir-text-secondary bg-memoir-bg border border-memoir-border rounded-button hover:border-memoir-indigo/30 transition-colors disabled:opacity-50">
              {retranscribing ? "Queuing\u2026" : "Retranscribe"}
            </button>
            <button onClick={handleRerunIntelligence}
              disabled={rerunningIntel || isProcessing || meeting.intelligence_status === "processing"}
              className="px-3 py-1.5 text-xs font-medium text-memoir-text-secondary bg-memoir-bg border border-memoir-border rounded-button hover:border-memoir-indigo/30 transition-colors disabled:opacity-50">
              {meeting.intelligence_status === "processing" ? "Analyzing\u2026" : rerunningIntel ? "Queuing\u2026" : "Rerun Intelligence"}
            </button>
            {meeting.status === "completed" && (
              <button onClick={async () => {
                setShowEmailDialog(true);
                setEmailSent(false);
                setEmailTo("");
                setEmailTemplate("external");
                try {
                  const [preview, log] = await Promise.all([
                    api.getEmailPreview(id, "external"),
                    api.getEmailLog(id),
                  ]);
                  setEmailPreviewHtml(preview.html_body);
                  setEmailPreviewSubject(preview.subject);
                  setEmailLog(log);
                } catch { setEmailPreviewHtml(""); }
              }}
                className="px-3 py-1.5 text-xs font-medium text-white bg-memoir-indigo border border-memoir-indigo rounded-button hover:bg-memoir-indigo/90 transition-colors">
                Send Email
              </button>
            )}
            {resolved?.overall_confidence_score != null && (
              <ConfidenceMeter value={resolved.overall_confidence_score} />
            )}
          </div>
        </div>
        {/* Timeline dots row */}
        {dealTimeline && dealTimeline.entries.length > 1 && (
          <div className="flex items-center gap-0 mt-3 pt-3 border-t border-memoir-border">
            {dealTimeline.entries.map((entry, idx) => {
              const isCurrent = entry.meeting_id === id;
              const isLast = idx === dealTimeline.entries.length - 1;
              return (
                <div key={entry.meeting_id} className="flex items-center">
                  <button
                    onClick={() => { if (!isCurrent) router.push(`/meetings/${entry.meeting_id}`); }}
                    disabled={isCurrent}
                    className="group relative flex flex-col items-center"
                    title={entry.one_line_summary || entry.title || `Call #${entry.call_number}`}
                  >
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-semibold transition-all ${
                      isCurrent
                        ? "bg-memoir-indigo text-white ring-2 ring-memoir-indigo/30"
                        : "bg-memoir-bg text-memoir-text-secondary border border-memoir-border group-hover:border-memoir-indigo/40 group-hover:text-memoir-indigo"
                    }`}>
                      {entry.call_number || idx + 1}
                    </div>
                    <span className={`text-[10px] mt-1 max-w-[80px] truncate ${
                      isCurrent ? "text-memoir-indigo font-medium" : "text-memoir-text-muted group-hover:text-memoir-text-secondary"
                    }`}>
                      {entry.date ? new Date(entry.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : ""}
                    </span>
                    {entry.call_health && (
                      <span className={`w-1.5 h-1.5 rounded-full mt-0.5 ${
                        entry.call_health === "on_track" ? "bg-emerald-400" :
                        entry.call_health === "needs_attention" ? "bg-amber-400" : "bg-red-400"
                      }`} />
                    )}
                  </button>
                  {!isLast && (
                    <div className="w-8 h-px bg-memoir-border mx-1" />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Processing progress bar — hidden during retranscribe (the
          processingBanner above already renders the same stage list and
          showing both produces a confusing double-loader). On a first-pass
          recording (no previous versions yet) processingBanner is null and
          this panel becomes the only progress UI. */}
      {processingStatus && (
        processingStatus.intelligence !== "completed" ||
        (processingStatus.crm_extraction === "waiting" || processingStatus.crm_extraction === "in_progress")
      ) && !processingBanner && (
        <div className="mb-5 bg-memoir-surface border border-memoir-border rounded-card px-5 py-4">
          <div className="mb-3">
            <span className="text-sm font-medium text-memoir-text">Processing</span>
          </div>
          <div className="space-y-3">
            {[
              { key: "preprocess", label: "Preprocessing" },
              { key: "enhance", label: "Enhancing audio" },
              { key: "diarization", label: "Identifying speakers" },
              { key: "fast_summary", label: "Preview summary" },
              { key: "transcribe", label: "Transcribing" },
              { key: "ai_analysis", label: "AI analysis" },
              { key: "crm_sync", label: "CRM sync" },
            ].map((stage) => {
              const data = processingStatus?.stages?.[stage.key];
              const pct = data?.percent ?? 0;
              const stageLabel = data?.label || "";
              const isActive = processingStatus?.active_stage === stage.key;
              const isComplete = pct >= 100;
              const isPending = pct === 0 && !isActive;
              return (
                <div key={stage.key} className={isPending ? "opacity-40" : ""}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-1.5 text-xs">
                      {isComplete ? (
                        <svg className="w-3.5 h-3.5 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                        </svg>
                      ) : isActive ? (
                        <div className="w-3 h-3 border-2 border-memoir-indigo border-t-transparent rounded-full animate-spin" />
                      ) : (
                        <div className="w-2.5 h-2.5 rounded-full bg-memoir-border" />
                      )}
                      <span className={isActive ? "text-memoir-indigo font-medium" : isComplete ? "text-memoir-text" : "text-memoir-text-muted"}>{stage.label}</span>
                    </div>
                    {(isActive || isComplete) && <span className="text-xs font-semibold text-memoir-indigo">{pct}%</span>}
                  </div>
                  <div className="w-full h-1.5 bg-memoir-border/30 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${isComplete ? "bg-emerald-500" : "bg-memoir-indigo"}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  {isActive && stageLabel && (
                    <p className="text-[10px] text-memoir-text-muted mt-0.5">{stageLabel}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* External share approval banner */}
      {meeting.status === "completed" &&
        !shareDismissed &&
        meeting.external_share_approved !== true &&
        (meeting.external_participants_count ?? 0) > 0 && (
        <div className="mb-5 bg-memoir-indigo/5 border border-memoir-indigo/20 rounded-card px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-memoir-text">
                Share summary with external participants?
              </p>
              <p className="text-xs text-memoir-text-muted mt-1">
                {meeting.external_participants_count} external participant{(meeting.external_participants_count ?? 0) > 1 ? "s" : ""} can receive a summary of this meeting.
              </p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button
                onClick={async () => {
                  setSharingExternal(true);
                  try {
                    await api.approveExternalShare(id);
                    setMeeting((prev) => prev ? { ...prev, external_share_approved: true } : prev);
                  } catch {
                    /* silent */
                  } finally {
                    setSharingExternal(false);
                  }
                }}
                disabled={sharingExternal}
                className="px-3 py-1.5 text-xs font-medium text-white bg-memoir-indigo rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
              >
                {sharingExternal ? "Sharing\u2026" : "Share Summary"}
              </button>
              <button
                onClick={() => setShareDismissed(true)}
                className="px-3 py-1.5 text-xs font-medium text-memoir-text-secondary bg-memoir-surface border border-memoir-border rounded-button hover:border-memoir-indigo/30 transition-colors"
              >
                Skip
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Tab bar */}
      <div className="flex items-center gap-1 mb-6 border-b border-memoir-border">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
              activeTab === tab.key
                ? "border-memoir-indigo text-memoir-indigo"
                : "border-transparent text-memoir-text-secondary hover:text-memoir-text hover:border-memoir-border"
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Two-column layout: tab content + right panel */}
      <div className="flex flex-col lg:flex-row gap-3 items-start">
        {/* ──── Tab content (left) ──── */}
        <div className="flex-1 min-w-0 space-y-4">

          {/* === AI Summary Tab === */}
          {activeTab === "summary" && (
            <ErrorBoundary>
              {/* Summary — wait for memoir to load so TipTap initializes with content */}
              {memoir && resolved?.narrative_summary ? (
                <SummaryEditor
                  key={`${id}-${resolved?.narrative_summary?.slice(0, 20) || "loading"}`}
                  meetingId={id}
                  initialContent={resolved.narrative_summary}
                  isEdited={memoir?.is_edited || false}
                  onReset={async () => {
                    await api.resetMemoir(id);
                    // Refetch memoir + coaching to get fresh content
                    const ver = selectedVersion ?? undefined;
                    const [freshMemoir, freshCoaching] = await Promise.all([
                      api.getMemoir(id, ver).catch(() => null),
                      api.getCoaching(id, ver).catch(() => null),
                    ]);
                    if (freshMemoir) setMemoir(freshMemoir);
                    if (freshCoaching) setCoaching(freshCoaching);
                  }}
                />
              ) : (
                <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                  <h2 className="text-sm font-semibold text-memoir-text mb-3">Summary</h2>
                  <p className="text-sm text-memoir-text-muted italic">
                    {memoir && !resolved?.narrative_summary
                      ? "No summary available. The transcript may be too short for AI analysis."
                      : meeting.status === "completed" && meeting.intelligence_status === "completed"
                        ? "No summary available."
                        : meeting.status === "completed"
                          ? "AI analysis in progress..."
                          : "Loading summary\u2026"}
                  </p>
                </div>
              )}

              {/* P5 preview sections (shown while intelligence is processing) */}
              {meeting.intelligence_status !== "completed" && resolved && (() => {
                const p5NextSteps = resolved.next_steps;
                const p5Actions = resolved.action_items || [];
                const p5Signals = resolved.signals;
                const p5Decisions = p5NextSteps?.decisions || [];
                const p5OpenQuestions = p5NextSteps?.open_questions || [];
                const p5Threads = p5Signals?.discussion_threads || [];
                return (
                <>
                  {/* Decisions */}
                  {(p5Decisions.length > 0) && (
                    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                      <h2 className="text-sm font-semibold text-memoir-text mb-3">Decisions</h2>
                      <div className="space-y-2">
                        {p5Decisions.map((d, idx) => (
                          <div key={idx} className="flex items-start gap-2">
                            <svg className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                            </svg>
                            <div>
                              <p className="text-sm font-medium text-memoir-text">{d.title}</p>
                              {d.detail && <p className="text-xs text-memoir-text-muted mt-0.5">{d.detail}</p>}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Action Items (P5 format) */}
                  {(p5Actions.length > 0) && (
                    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                      <h2 className="text-sm font-semibold text-memoir-text mb-3">Action Items</h2>
                      <div className="space-y-2">
                        {p5Actions.map((a, idx) => (
                          <div key={idx} className="flex items-start gap-2">
                            <div className="w-4 h-4 mt-0.5 flex-shrink-0 rounded border border-memoir-border" />
                            <div>
                              <p className="text-sm text-memoir-text">{a.text}</p>
                              <div className="flex items-center gap-2 mt-1">
                                {a.owner && <span className="text-xs font-medium text-memoir-indigo">{a.owner}</span>}
                                {a.urgency && <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${a.urgency === "today" ? "bg-red-50 text-red-700" : a.urgency === "this_week" ? "bg-amber-50 text-amber-700" : "bg-gray-50 text-gray-600"}`}>{a.urgency}</span>}
                              </div>
                              {a.context && <p className="text-xs text-memoir-text-muted mt-0.5">{a.context}</p>}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Open Questions */}
                  {(p5OpenQuestions.length > 0) && (
                    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                      <h2 className="text-sm font-semibold text-memoir-text mb-3">Open Questions</h2>
                      <div className="space-y-2">
                        {p5OpenQuestions.map((q, idx) => (
                          <div key={idx} className="flex items-start gap-2">
                            <span className="text-amber-500 mt-0.5 flex-shrink-0 text-sm">?</span>
                            <p className="text-sm text-memoir-text">{q.text}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Discussion Threads */}
                  {(p5Threads.length > 0) && (
                    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                      <h2 className="text-sm font-semibold text-memoir-text mb-3">Discussion Threads</h2>
                      <div className="space-y-3">
                        {p5Threads.map((t, idx) => (
                          <div key={idx} className={`border-l-2 pl-3 ${t.priority === "high" ? "border-memoir-indigo" : t.priority === "medium" ? "border-amber-400" : "border-memoir-border"}`}>
                            <p className="text-sm font-medium text-memoir-text">{t.title}</p>
                            {t.summary && <p className="text-xs text-memoir-text-muted mt-1">{t.summary}</p>}
                            {t.attributions && t.attributions.length > 0 && (
                              <div className="flex flex-wrap gap-1 mt-1.5">
                                {t.attributions.map((attr, ai) => (
                                  <span key={ai} className="text-[10px] px-1.5 py-0.5 rounded bg-memoir-bg text-memoir-text-secondary border border-memoir-border">
                                    <span className="font-medium text-memoir-indigo">{attr.speaker}</span> {attr.action}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
                );
              })()}

              {/* Phase 10 sections (shown when intelligence is complete) */}
              {meeting.intelligence_status === "completed" && (
                <>
                  {/* Next Steps */}
                  {nextSteps && Object.keys(nextSteps).length > 0 && (nextSteps.next_meeting || nextSteps.next_milestone || nextSteps.suggested_deal_stage || nextSteps.decisions || nextSteps.open_questions) && (
                    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                      <h2 className="text-sm font-semibold text-memoir-text mb-3">Next Steps</h2>
                      <div className="space-y-3">
                        {nextSteps.suggested_deal_stage && (
                          <div className="flex items-center gap-2">
                            <span className="px-2 py-0.5 bg-blue-50 text-blue-700 text-xs font-medium rounded-full border border-blue-200">{nextSteps.suggested_deal_stage}</span>
                            <span className="text-xs text-memoir-text-muted">Suggested stage</span>
                          </div>
                        )}
                        {nextSteps.next_milestone && (
                          <div className="flex items-start gap-2">
                            <svg className="w-4 h-4 text-memoir-text-muted mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3 3v1.5M3 21v-6m0 0 2.77-.693a9 9 0 0 1 6.208.682l.108.054a9 9 0 0 0 6.086.71l3.114-.732a48.524 48.524 0 0 1-.005-10.499l-3.11.732a9 9 0 0 1-6.085-.711l-.108-.054a9 9 0 0 0-6.208-.682L3 4.5M3 15V4.5" />
                            </svg>
                            <div>
                              <p className="text-xs text-memoir-text-muted mb-0.5">Milestone</p>
                              <p className="text-sm text-memoir-text">{nextSteps.next_milestone}</p>
                            </div>
                          </div>
                        )}
                        {nextSteps.next_meeting && (
                          <div className="flex items-start gap-2">
                            <svg className="w-4 h-4 text-memoir-text-muted mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
                            </svg>
                            <div>
                              <p className="text-xs text-memoir-text-muted mb-0.5">Next meeting</p>
                              <p className="text-sm text-memoir-text">{nextSteps.next_meeting}</p>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Action Items */}
                  {hasActionItems && (
                    <EditableActionItems
                      meetingId={id}
                      initialItems={resolved?.action_items || []}
                      readOnly={memoirReadOnly}
                    />
                  )}

                  {/* Signals */}
                  {hasSignals && (
                    <EditableSignals
                      meetingId={id}
                      initialSignals={resolved?.signals || { buying: [], risk: [] }}
                      readOnly={memoirReadOnly}
                    />
                  )}
                </>
              )}
            </ErrorBoundary>
          )}

          {/* === Transcript Tab === */}
          {activeTab === "transcript" && (
            <ErrorBoundary>
            {/* Raw ASR link is always visible once Type 7 has produced raw_asr_segments.json,
                regardless of whether the polished transcript is loaded — gives the user a
                way to inspect the transcript even during the read-only preview window. */}
            {(transcript || (processingStatus?.stages?.fast_summary?.percent ?? 0) >= 100) && (
              <div className="flex justify-end mb-2">
                <button
                  onClick={() => {
                    const q = selectedVersion != null ? `?version=${selectedVersion}` : '';
                    window.open(`/meetings/${id}/raw-asr${q}`, '_blank');
                  }}
                  className="text-xs text-memoir-text-muted hover:text-memoir-indigo transition-colors"
                >
                  View Raw ASR ↗
                </button>
              </div>
            )}
            {transcript ? (
              <>
              <EditableTranscript
                meetingId={id}
                transcript={transcript}
                onSeek={seekTo}
                onTranscriptUpdate={setTranscript}
                editingSpeaker={editingSpeaker}
                speakerInput={speakerInput}
                teamSpeakers={teamSpeakers}
                savingSpeaker={savingSpeaker}
                speakerInputRef={speakerInputRef}
                onSpeakerClick={handleSpeakerClick}
                onSpeakerInputChange={setSpeakerInput}
                onSpeakerSave={handleSpeakerSave}
                onSpeakerEditCancel={() => setEditingSpeaker(null)}
                readOnly={memoirReadOnly}
              />
              </>
            ) : (processingStatus?.stages?.fast_summary?.percent ?? 0) >= 100 ? (
              <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                <div className="flex items-center gap-3">
                  <div className="animate-spin w-4 h-4 border-2 border-memoir-border border-t-memoir-indigo rounded-full flex-shrink-0" />
                  <p className="text-sm text-memoir-text-muted italic">Loading preview transcript…</p>
                </div>
                <p className="text-xs text-memoir-text-muted mt-2">
                  The polished transcript is still processing. The raw transcript is available now —{" "}
                  <button
                    onClick={() => {
                    const q = selectedVersion != null ? `?version=${selectedVersion}` : '';
                    window.open(`/meetings/${id}/raw-asr${q}`, '_blank');
                  }}
                    className="text-memoir-indigo hover:underline"
                  >
                    open raw ASR
                  </button>
                  .
                </p>
              </div>
            ) : (
              <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                <p className="text-sm text-memoir-text-muted italic">Transcript not available.</p>
              </div>
            )}
            </ErrorBoundary>
          )}

          {/* === CRM Update Tab === */}
          {activeTab === "crm" && (() => {
            // Show processing state when intelligence or CRM is still running
            if (meeting.intelligence_status !== "completed" && meeting.intelligence_status !== "failed") {
              return (
                <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
                  <div className="w-8 h-8 border-2 border-memoir-indigo border-t-transparent rounded-full animate-spin mx-auto mb-4" />
                  <h3 className="text-sm font-semibold text-memoir-text mb-2">AI Analysis in Progress</h3>
                  <p className="text-sm text-memoir-text-muted">
                    CRM data will be available once the intelligence analysis is complete.
                  </p>
                </div>
              );
            }
            if (processingStatus && (processingStatus.crm_extraction === "waiting" || processingStatus.crm_extraction === "in_progress")) {
              return (
                <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
                  <div className="w-8 h-8 border-2 border-memoir-indigo border-t-transparent rounded-full animate-spin mx-auto mb-4" />
                  <h3 className="text-sm font-semibold text-memoir-text mb-2">Syncing CRM Data</h3>
                  <p className="text-sm text-memoir-text-muted">
                    {processingStatus.stages?.crm_sync?.label || "Extracting CRM fields from call data..."}
                  </p>
                </div>
              );
            }

            const connectedProviders = crmConnections.filter((c) => c.is_active).map((c) => c.provider);
            const noCrmConnected = connectedProviders.length === 0;
            const crmUser = useAuthStore.getState().user;
            const canAccessIntegrations = crmUser?.team_role === "owner" || crmUser?.team_role === "admin";

            // No CRM connected — show prompt to integrate
            if (noCrmConnected) {
              return (
                <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
                  <svg className="w-12 h-12 text-memoir-text-muted/40 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m9.86-9.86a4.5 4.5 0 00-6.364 0l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
                  </svg>
                  <h3 className="text-sm font-semibold text-memoir-text mb-2">No CRM Connected</h3>
                  {canAccessIntegrations ? (
                    <>
                      <p className="text-sm text-memoir-text-muted mb-4">
                        Connect HubSpot or Zoho CRM to push call insights automatically.
                      </p>
                      <a href="/integrations" className="inline-block px-5 py-2.5 text-sm font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors">
                        Go to Integrations
                      </a>
                    </>
                  ) : (
                    <p className="text-sm text-memoir-text-muted">
                      Ask your team admin to connect a CRM from the Integrations page.
                    </p>
                  )}
                </div>
              );
            }

            return (
              <>
                {/* Provider sub-tabs — only show if more than one connected */}
                {connectedProviders.length > 1 && (
                  <div className="flex gap-1 mb-4">
                    {connectedProviders.map((prov) => {
                      const label = prov === "hubspot" ? "HubSpot" : "Zoho CRM";
                      return (
                        <button
                          key={prov}
                          onClick={() => setActiveCrmProvider(prov)}
                          className={`px-3 py-1.5 text-xs font-medium rounded-button transition-colors ${
                            (activeCrmProvider || connectedProviders[0]) === prov
                              ? "bg-memoir-indigo text-white"
                              : "bg-memoir-bg text-memoir-text-muted hover:text-memoir-text"
                          }`}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Per-provider CRM content */}
                {(() => {
                  const provider = connectedProviders.includes(activeCrmProvider) ? activeCrmProvider : connectedProviders[0];
                  const providerLabel = provider === "hubspot" ? "HubSpot" : "Zoho CRM";
                  const pushResult = crmPushResults.find((r) => r.provider === provider);
                  const isProcessing = pushResult?.status === "processing";

                  // Build data fields to display
                  const dataSource = pushResult?.edited_data || pushResult?.pushed_data;
                  const hasPushedData = dataSource && Object.keys(dataSource).length > 0;
                  const displayFields: { key: string; label: string; value: string; section?: string }[] = [];

                  // Helper to extract display fields from a nested CRM data object
                  const extractFieldsFromNested = (source: Record<string, unknown>) => {
                    for (const [key, value] of Object.entries(source)) {
                      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
                        for (const [subKey, subVal] of Object.entries(value as Record<string, unknown>)) {
                          if (subVal && subVal !== "null" && subVal !== "Unknown") {
                            displayFields.push({ key: `${key}.${subKey}`, label: subKey.replace(/_/g, " "), value: String(subVal), section: key });
                          }
                        }
                      } else if (key === "call_summary" && value) {
                        displayFields.push({ key: "call_summary", label: "Call Summary", value: String(value) });
                      }
                    }
                  };

                  if (hasPushedData) {
                    extractFieldsFromNested(dataSource as Record<string, unknown>);
                  } else if (crmExtraction && Object.keys(crmExtraction).length > 0) {
                    extractFieldsFromNested(crmExtraction);
                  } else if (crmFields.length > 0) {
                    for (const field of crmFields) {
                      displayFields.push({ key: `discovery.${field.name}`, label: field.name, value: field.value });
                    }
                  }

                  const hasFields = displayFields.length > 0;

                  // Determine push button state
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
                    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                      {/* Status banners */}
                      {isProcessing && (
                        <div className="flex items-center gap-2 mb-4 p-3 bg-memoir-indigo/5 border border-memoir-indigo/20 rounded-button">
                          <div className="w-4 h-4 border-2 border-memoir-indigo border-t-transparent rounded-full animate-spin flex-shrink-0" />
                          <span className="text-sm font-medium text-memoir-indigo">Pushing to {providerLabel}...</span>
                        </div>
                      )}

                      {pushResult?.status === "completed" && (
                        <div className="flex items-center justify-between mb-4 p-3 bg-emerald-50 border border-emerald-200 rounded-button">
                          <div className="flex items-center gap-2">
                            <svg className="w-4 h-4 text-emerald-500" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" /></svg>
                            <span className="text-sm font-medium text-emerald-700">Pushed to {providerLabel}</span>
                            {pushResult.pushed_at && (
                              <span className="text-xs text-emerald-600/70" title={new Date(pushResult.pushed_at).toLocaleString()}>
                                {timeAgo(pushResult.pushed_at)}
                              </span>
                            )}
                          </div>
                          {pushResult.crm_record_url && (
                            <a href={pushResult.crm_record_url} target="_blank" rel="noopener noreferrer" className="text-xs font-medium text-memoir-indigo hover:underline">
                              View in {providerLabel} &rarr;
                            </a>
                          )}
                        </div>
                      )}

                      {pushResult?.status === "failed" && (
                        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-button">
                          <div className="flex items-center gap-2 mb-1">
                            <svg className="w-4 h-4 text-red-500" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" /></svg>
                            <span className="text-sm font-medium text-red-700">Push failed</span>
                          </div>
                          {pushResult.error_message && (
                            <p className="text-xs text-red-600 ml-6">{pushResult.error_message}</p>
                          )}
                        </div>
                      )}

                      {pushResult?.status === "skipped" && (
                        <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-button">
                          <span className="text-sm text-amber-700">CRM push was skipped for this call</span>
                        </div>
                      )}

                      {/* Contact + Deal IDs (if pushed) */}
                      {pushResult?.status === "completed" && (pushResult.crm_contact_id || pushResult.crm_deal_id) && (
                        <div className="flex gap-4 mb-4 text-xs text-memoir-text-muted">
                          {pushResult.crm_contact_id && <span>Contact: <span className="font-mono text-memoir-text">{pushResult.crm_contact_id}</span></span>}
                          {pushResult.crm_deal_id && <span>Deal: <span className="font-mono text-memoir-text">{pushResult.crm_deal_id}</span></span>}
                        </div>
                      )}

                      {/* Extraction error banner */}
                      {crmExtractError && !regeneratingCrm && (
                        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-button">
                          <div className="flex items-center gap-2 mb-1">
                            <svg className="w-4 h-4 text-red-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" /></svg>
                            <span className="text-sm font-medium text-red-700">CRM extraction failed</span>
                          </div>
                          <p className="text-xs text-red-600 ml-6">{crmExtractError}</p>
                        </div>
                      )}

                      {/* Intelligence processing / failed state */}
                      {(meeting.intelligence_status as string) === "processing" && (
                        <div className="mb-4 p-3 bg-memoir-bg border border-memoir-border rounded-button">
                          <div className="flex items-center gap-3">
                            <div className="w-4 h-4 border-2 border-memoir-indigo border-t-transparent rounded-full animate-spin flex-shrink-0" />
                            <div>
                              <p className="text-sm font-medium text-memoir-text">Intelligence analysis in progress</p>
                              {processingStatus?.stages?.ai_analysis?.label && (
                                <p className="text-xs text-memoir-text-muted mt-0.5">{processingStatus.stages.ai_analysis.label}</p>
                              )}
                            </div>
                          </div>
                        </div>
                      )}
                      {meeting.intelligence_status === "failed" && (
                        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-button">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <svg className="w-4 h-4 text-red-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                              </svg>
                              <span className="text-sm font-medium text-red-700">Intelligence analysis failed</span>
                            </div>
                            <button onClick={handleRerunIntelligence} disabled={rerunningIntel}
                              className="px-3 py-1.5 text-xs font-medium text-white bg-red-500 rounded-button hover:bg-red-600 transition-colors disabled:opacity-50">
                              {rerunningIntel ? "Queuing\u2026" : "Rerun Intelligence"}
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Deal Intelligence section */}
                      {callIntelligence && (callIntelligence.validated_stage || callIntelligence.crm_stage_label || callIntelligence.deal_momentum || callIntelligence.has_committed_next_step != null) && (
                        <div className="mb-4 p-3 bg-memoir-bg border border-memoir-border rounded-button">
                          <h3 className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-2.5">Deal Intelligence</h3>

                          {/* Stage comparison */}
                          {(callIntelligence.validated_stage || callIntelligence.crm_stage_label) && (
                            <div className="flex items-center gap-3 mb-2.5">
                              {callIntelligence.validated_stage && (
                                <div className="flex-1 min-w-0">
                                  <p className="text-[10px] text-memoir-text-muted mb-0.5">AI Validated Stage</p>
                                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-memoir-indigo/10 text-memoir-indigo border border-memoir-indigo/20">
                                    {callIntelligence.validated_stage}
                                  </span>
                                </div>
                              )}
                              {callIntelligence.validated_stage && callIntelligence.crm_stage_label && (
                                <svg className="w-4 h-4 text-memoir-text-muted flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 21 3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" />
                                </svg>
                              )}
                              {callIntelligence.crm_stage_label && (
                                <div className="flex-1 min-w-0">
                                  <p className="text-[10px] text-memoir-text-muted mb-0.5">CRM Stage</p>
                                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200">
                                    {callIntelligence.crm_stage_label}
                                  </span>
                                </div>
                              )}
                            </div>
                          )}

                          {/* Deal momentum + buy signal row */}
                          <div className="flex flex-wrap items-center gap-2">
                            {callIntelligence.deal_momentum && (
                              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
                                callIntelligence.deal_momentum === "accelerating" ? "bg-emerald-50 text-emerald-700 border border-emerald-200" :
                                callIntelligence.deal_momentum === "steady" ? "bg-blue-50 text-blue-700 border border-blue-200" :
                                callIntelligence.deal_momentum === "decelerating" ? "bg-amber-50 text-amber-700 border border-amber-200" :
                                "bg-red-50 text-red-700 border border-red-200"
                              }`}>
                                {callIntelligence.deal_momentum === "accelerating" ? "\u2191" : callIntelligence.deal_momentum === "steady" ? "\u2192" : callIntelligence.deal_momentum === "decelerating" ? "\u2193" : "\u25CF"}
                                {" "}{callIntelligence.deal_momentum.charAt(0).toUpperCase() + callIntelligence.deal_momentum.slice(1)}
                              </span>
                            )}
                            {callIntelligence.has_committed_next_step != null && (
                              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
                                callIntelligence.has_committed_next_step
                                  ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                                  : "bg-memoir-bg text-memoir-text-muted border border-memoir-border"
                              }`}>
                                <span className={`w-1.5 h-1.5 rounded-full ${callIntelligence.has_committed_next_step ? "bg-emerald-500" : "bg-memoir-text-muted/40"}`} />
                                {callIntelligence.has_committed_next_step
                                  ? `Next step${callIntelligence.next_step_type ? `: ${callIntelligence.next_step_type}` : ""}`
                                  : "No next step"}
                              </span>
                            )}
                          </div>
                        </div>
                      )}

                      {/* Data fields */}
                      <div className="flex items-center justify-between mb-3">
                        <h2 className="text-sm font-semibold text-memoir-text">
                          {hasPushedData ? "CRM Data" : "Extracted Fields"}
                        </h2>
                        {hasFields && (
                          <button
                            onClick={handleRegenerateCrmExtraction}
                            disabled={regeneratingCrm}
                            className="text-xs text-memoir-indigo hover:underline disabled:opacity-50"
                          >
                            {regeneratingCrm ? "Generating..." : "Regenerate"}
                          </button>
                        )}
                      </div>

                      {hasFields ? (
                        <div className="space-y-2 mb-4">
                          {displayFields.map((field) => (
                            <div key={field.key} className="border border-memoir-border/60 rounded-button p-2.5">
                              <div className="flex items-start justify-between gap-2">
                                <div className="flex-1 min-w-0">
                                  <span className="text-xs text-memoir-text-muted capitalize">{field.label}</span>
                                  {editingCrmField === field.key ? (
                                    <div className="flex items-center gap-1 mt-1">
                                      {field.key === "call_summary" ? (
                                        <textarea
                                          value={editingCrmValue}
                                          onChange={(e) => setEditingCrmValue(e.target.value)}
                                          className="w-full px-2 py-1 text-xs border border-memoir-border rounded resize-y"
                                          rows={6}
                                          autoFocus
                                        />
                                      ) : (
                                        <input
                                          value={editingCrmValue}
                                          onChange={(e) => setEditingCrmValue(e.target.value)}
                                          className="flex-1 px-2 py-1 text-xs border border-memoir-border rounded"
                                          autoFocus
                                        />
                                      )}
                                      <button onClick={() => handleEditCrmField(provider, field.key, editingCrmValue)} className="text-emerald-600 text-xs font-medium whitespace-nowrap">Save</button>
                                      <button onClick={() => setEditingCrmField(null)} className="text-memoir-text-muted text-xs whitespace-nowrap">Cancel</button>
                                    </div>
                                  ) : (
                                    <p className={`text-sm text-memoir-text mt-0.5 ${field.key === "call_summary" ? "whitespace-pre-line text-xs leading-relaxed" : ""}`}>
                                      {field.value}
                                    </p>
                                  )}
                                </div>
                                {editingCrmField !== field.key && (
                                  <button
                                    onClick={() => { setEditingCrmField(field.key); setEditingCrmValue(field.value); }}
                                    className="text-xs text-memoir-indigo hover:underline flex-shrink-0 mt-0.5"
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
                          <p className="text-sm text-memoir-text-muted italic mb-3">No CRM fields extracted for this call.</p>
                          <button
                            onClick={handleRegenerateCrmExtraction}
                            disabled={regeneratingCrm}
                            className="px-4 py-2 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
                          >
                            {regeneratingCrm ? "Generating..." : "Extract CRM Data"}
                          </button>
                        </div>
                      )}

                      {/* Push / Re-push / Retry button — only show if there are fields */}
                      {hasFields && (
                        <div className="pt-3 border-t border-memoir-border space-y-2">
                          {pushDisabled ? (
                            <div className="space-y-2">
                              <button disabled className="w-full py-2.5 bg-memoir-indigo/40 text-white text-sm font-medium rounded-button cursor-not-allowed">
                                {isProcessing ? "Pushing..." : pushResult?.status === "completed" && pushResult?.edited_data
                                  ? `Re-push to ${providerLabel}`
                                  : `Push to ${providerLabel}`}
                              </button>
                              <p className="text-xs text-memoir-text-muted text-center">{pushDisabledReason}</p>
                            </div>
                          ) : (
                            <button
                              onClick={() => handlePushCrm(provider)}
                              disabled={crmAction !== null || meeting.intelligence_status !== "completed"}
                              className={`w-full py-2.5 text-white text-sm font-medium rounded-button transition-colors disabled:opacity-50 ${
                                pushResult?.edited_data
                                  ? "bg-amber-500 hover:bg-amber-600"
                                  : pushResult?.status === "failed"
                                    ? "bg-red-500 hover:bg-red-600"
                                    : "bg-memoir-indigo hover:bg-memoir-indigo-hover"
                              }`}
                            >
                              {crmAction === "pushing" ? "Pushing..." :
                                pushResult?.edited_data ? `Re-push to ${providerLabel}` :
                                pushResult?.status === "failed" ? "Retry Push" :
                                pushResult?.status === "skipped" ? "Push Now" :
                                `Push to ${providerLabel}`}
                            </button>
                          )}

                          {/* Skip button — only when pending or no result yet */}
                          {(!pushResult || pushResult.status === "pending") && (
                            <button onClick={() => handleSkipCrm(provider)} disabled={crmAction !== null || meeting.intelligence_status !== "completed"}
                              className="w-full py-2 text-memoir-text-muted text-xs hover:text-memoir-text transition-colors disabled:opacity-50">
                              {crmAction === "skipping" ? "Skipping..." : "Skip CRM push"}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })()}

                {/* Disclaimer */}
                {resolved?.disclaimer && (
                  <div className="bg-amber-50 border border-amber-200 rounded-card p-4">
                    <p className="text-xs font-medium text-amber-700 mb-1">Disclaimer</p>
                    <p className="text-xs text-amber-600 leading-relaxed">{resolved.disclaimer}</p>
                  </div>
                )}
              </>
            );
          })()}
        </div>

        {/* ──── Right panel (persistent) ──── */}
        <div className="w-full lg:w-[340px] flex-shrink-0 space-y-4">

          {/* Unified card: Recording + Speakers + Coaching */}
          <div className="bg-memoir-surface border border-memoir-border rounded-card p-4">

            {/* Audio Player */}
            {meeting.has_recording && audioSrc && (
              <div>
                <h3 className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-3">Recording</h3>
                <audio
                  ref={audioRef}
                  preload="metadata"
                  src={audioSrc}
                  onPlay={() => setAudioPlaying(true)}
                  onPause={() => setAudioPlaying(false)}
                  onTimeUpdate={() => setAudioTime(audioRef.current?.currentTime || 0)}
                  onLoadedMetadata={handleLoadedMetadata}
                  className="hidden"
                />
                <div className="flex items-center gap-3">
                  <button onClick={togglePlay}
                    className="w-9 h-9 rounded-full bg-memoir-indigo text-white flex items-center justify-center hover:bg-memoir-indigo-hover transition-colors flex-shrink-0">
                    {audioPlaying ? (
                      <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z"/></svg>
                    ) : (
                      <svg className="w-4 h-4 ml-0.5" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                    )}
                  </button>
                  <div className="flex-1">
                    <div className="h-1.5 bg-memoir-border rounded-full cursor-pointer" onClick={handleScrub}>
                      <div className="h-full bg-memoir-indigo rounded-full transition-all"
                        style={{ width: audioDuration ? `${(audioTime / audioDuration) * 100}%` : "0%" }} />
                    </div>
                    <div className="flex justify-between mt-1">
                      <span className="text-[10px] font-mono text-memoir-text-muted">{formatSeconds(audioTime)}</span>
                      <span className="text-[10px] font-mono text-memoir-text-muted">{formatSeconds(audioDuration)}</span>
                    </div>
                  </div>
                </div>
                {Object.keys(audioUrls).length > 1 && (
                  <div className="mt-2 flex items-center gap-2">
                    <span className="text-[10px] text-memoir-text-muted">Audio:</span>
                    <select
                      value={audioVersion}
                      onChange={(e) => {
                        const v = e.target.value;
                        setAudioVersion(v);
                        setAudioSrc(audioUrls[v]);
                        if (audioRef.current) {
                          const wasPlaying = !audioRef.current.paused;
                          const currentTime = audioRef.current.currentTime;
                          audioRef.current.src = audioUrls[v];
                          audioRef.current.currentTime = currentTime;
                          if (wasPlaying) audioRef.current.play();
                        }
                      }}
                      className="text-[11px] bg-memoir-surface border border-memoir-border rounded px-1.5 py-0.5 text-memoir-text"
                    >
                      {Object.keys(audioUrls).map((key) => (
                        <option key={key} value={key}>
                          {key.charAt(0).toUpperCase() + key.slice(1)}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            )}

            {/* Participants */}
            {dedupedParticipants && dedupedParticipants.length > 0 && (
              <div className={meeting.has_recording ? "mt-4 pt-4 border-t border-memoir-border" : ""}>
                <h3 className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-3">Speakers</h3>
                <div className="space-y-2.5">
                  {dedupedParticipants.map((p, i) => {
                    // Defensive — even after the dedupe-time filter, treat
                    // p.name as a string consistently to avoid future
                    // regressions if the upstream schema drifts.
                    const safeName = (typeof p.name === "string" && p.name) ? p.name : "Unknown";
                    const isUnidentified = /^SPEAKER_\d+$/i.test(safeName);
                    const speakerCrmLinks = meetingSpeakers.find(
                      (s) => s.display_name?.toLowerCase() === safeName.toLowerCase()
                    )?.crm_links;
                    return (
                      <div key={i} className="flex items-center gap-2">
                        <div className={`w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 ${isUnidentified ? "bg-memoir-bg" : "bg-memoir-indigo/10"}`}>
                          <span className={`text-xs font-medium ${isUnidentified ? "text-memoir-text-muted" : "text-memoir-indigo"}`}>
                            {safeName.charAt(0).toUpperCase()}
                          </span>
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <p className={`text-sm break-all ${isUnidentified ? "text-memoir-text-muted italic" : "text-memoir-text"}`}>{safeName}</p>
                            {speakerCrmLinks?.hubspot && (
                              <span className="text-[10px] px-1.5 py-0.5 bg-orange-50 text-orange-600 rounded flex-shrink-0">HubSpot</span>
                            )}
                            {speakerCrmLinks?.zoho && (
                              <span className="text-[10px] px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded flex-shrink-0">Zoho</span>
                            )}
                          </div>
                          {(p.title || p.company) && (
                            <p className="text-xs text-memoir-text-muted break-words">{[p.title, p.company].filter(Boolean).join(" at ")}</p>
                          )}
                        </div>
                        {p.role && p.role !== "null" && p.role !== "other" && (
                          <span className="ml-auto text-xs text-memoir-text-muted capitalize flex-shrink-0">{p.role}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Coaching Metrics */}
            {cm && (
              <div className={(meeting.has_recording || (dedupedParticipants && dedupedParticipants.length > 0)) ? "mt-4 pt-4 border-t border-memoir-border" : ""}>
                <h3 className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-3">Coaching Metrics</h3>

                {/* Talk Ratio Bar */}
                {talkRatio && (
                  <div className="mb-4">
                    <div className="flex justify-between text-xs text-memoir-text-muted mb-1">
                      <span>You {Math.round(talkRatio.rep_percentage)}%</span>
                      <span>Them {Math.round(talkRatio.customer_percentage)}%</span>
                    </div>
                    <div className="flex h-2 rounded-full overflow-hidden bg-memoir-border">
                      <div className="bg-memoir-indigo rounded-l-full" style={{ width: `${talkRatio.rep_percentage}%` }} />
                      <div className="bg-emerald-400 rounded-r-full" style={{ width: `${talkRatio.customer_percentage}%` }} />
                    </div>
                  </div>
                )}

                <div className="space-y-2.5">
                  {longestMonologue != null && (
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-memoir-text-muted">Longest monologue</span>
                      <span className="text-xs font-medium text-memoir-text">{formatSeconds(longestMonologue)}</span>
                    </div>
                  )}
                  {questionCount && (
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-memoir-text-muted">Questions asked</span>
                      <span className="text-xs font-medium text-memoir-text">You: {questionCount.rep} &middot; Them: {questionCount.customer}</span>
                    </div>
                  )}
                  {fillerCount && (fillerCount.rep > 0 || fillerCount.customer > 0) && (
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-memoir-text-muted">Filler words</span>
                      <span className="text-xs font-medium text-memoir-text">You: {fillerCount.rep} &middot; Them: {fillerCount.customer}</span>
                    </div>
                  )}
                  {interruptionCount != null && interruptionCount > 0 && (
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-memoir-text-muted">Interruptions</span>
                      <span className="text-xs font-medium text-memoir-text">{interruptionCount}</span>
                    </div>
                  )}
                  {avgResponseTime != null && avgResponseTime > 0 && (
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-memoir-text-muted">Avg response time</span>
                      <span className="text-xs font-medium text-memoir-text">{avgResponseTime.toFixed(1)}s</span>
                    </div>
                  )}
                  {silencePercent != null && (
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-memoir-text-muted">Silence</span>
                      <span className="text-xs font-medium text-memoir-text">{silencePercent.toFixed(1)}%</span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Key Moments */}
          {keyMoments.length > 0 && (
            <div className="bg-memoir-surface border border-memoir-border rounded-card p-4">
              <h3 className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-3">Key Moments</h3>
              <div className="space-y-2">
                {keyMoments.map((moment, i) => (
                  <button key={i} onClick={() => { seekTo(moment.time); setActiveTab("transcript"); }}
                    className="flex items-start gap-2 w-full text-left hover:bg-memoir-bg rounded-button p-1.5 -mx-1.5 transition-colors">
                    <span className="text-[10px] font-mono text-memoir-text-muted w-10 flex-shrink-0 pt-0.5">{formatSeconds(moment.time)}</span>
                    <div className="flex-1 min-w-0">
                      <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${moment.type === "buying" ? "bg-emerald-500" : "bg-red-500"}`} />
                      <span className="text-xs text-memoir-text-secondary line-clamp-2">{moment.label}</span>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Intelligence processing / failed state (right panel) */}
          {meeting.intelligence_status === "processing" && (
            <div className="bg-memoir-surface border border-memoir-border rounded-card p-4">
              <div className="flex items-center gap-3">
                <div className="w-4 h-4 border-2 border-memoir-indigo border-t-transparent rounded-full animate-spin flex-shrink-0" />
                <div>
                  <p className="text-xs font-medium text-memoir-text">Analyzing call intelligence...</p>
                  {processingStatus?.stages?.ai_analysis?.label && (
                    <p className="text-[11px] text-memoir-text-muted mt-0.5">{processingStatus.stages.ai_analysis.label}</p>
                  )}
                </div>
              </div>
            </div>
          )}
          {meeting.intelligence_status === "failed" && !callIntelligence && (
            <div className="bg-memoir-surface border border-red-200 rounded-card p-4">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-red-600">Intelligence failed</span>
                <button onClick={handleRerunIntelligence} disabled={rerunningIntel}
                  className="text-xs font-medium text-memoir-indigo hover:text-memoir-indigo-hover disabled:opacity-50">
                  {rerunningIntel ? "Queuing\u2026" : "Retry"}
                </button>
              </div>
            </div>
          )}

          {/* PMF / Deal Intelligence Card */}
          {callIntelligence && (callIntelligence.pull_score != null || callIntelligence.deal_momentum || callIntelligence.overall_pmf_signal) && (
            <div className="bg-memoir-surface border border-memoir-border rounded-card p-4">
              <h3 className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-3">PMF Signals</h3>

              {/* Score bars */}
              <div className="space-y-2.5 mb-3">
                {callIntelligence.pull_score != null && (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-memoir-text-muted">Pull Score</span>
                      <span className="text-xs font-medium text-memoir-text">{callIntelligence.pull_score}/10</span>
                    </div>
                    <div className="h-1.5 bg-memoir-border rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all ${callIntelligence.pull_score >= 7 ? "bg-emerald-500" : callIntelligence.pull_score >= 4 ? "bg-amber-400" : "bg-red-400"}`} style={{ width: `${callIntelligence.pull_score * 10}%` }} />
                    </div>
                  </div>
                )}
                {callIntelligence.pain_fit_score != null && (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-memoir-text-muted">Pain Fit</span>
                      <span className="text-xs font-medium text-memoir-text">{callIntelligence.pain_fit_score}/10</span>
                    </div>
                    <div className="h-1.5 bg-memoir-border rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all ${callIntelligence.pain_fit_score >= 7 ? "bg-emerald-500" : callIntelligence.pain_fit_score >= 4 ? "bg-amber-400" : "bg-red-400"}`} style={{ width: `${callIntelligence.pain_fit_score * 10}%` }} />
                    </div>
                  </div>
                )}
                {callIntelligence.icp_fit_score != null && (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-memoir-text-muted">ICP Fit</span>
                      <span className="text-xs font-medium text-memoir-text">{callIntelligence.icp_fit_score}/10</span>
                    </div>
                    <div className="h-1.5 bg-memoir-border rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all ${callIntelligence.icp_fit_score >= 7 ? "bg-emerald-500" : callIntelligence.icp_fit_score >= 4 ? "bg-amber-400" : "bg-red-400"}`} style={{ width: `${callIntelligence.icp_fit_score * 10}%` }} />
                    </div>
                  </div>
                )}
              </div>

              {/* Badges row */}
              <div className="flex flex-wrap gap-1.5 mb-3">
                {callIntelligence.deal_momentum && (
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
                    callIntelligence.deal_momentum === "accelerating" ? "bg-emerald-50 text-emerald-700 border border-emerald-200" :
                    callIntelligence.deal_momentum === "steady" ? "bg-blue-50 text-blue-700 border border-blue-200" :
                    callIntelligence.deal_momentum === "decelerating" ? "bg-amber-50 text-amber-700 border border-amber-200" :
                    "bg-red-50 text-red-700 border border-red-200"
                  }`}>
                    {callIntelligence.deal_momentum === "accelerating" ? "\u2191" : callIntelligence.deal_momentum === "steady" ? "\u2192" : callIntelligence.deal_momentum === "decelerating" ? "\u2193" : "\u25CF"}
                    {" "}{callIntelligence.deal_momentum.charAt(0).toUpperCase() + callIntelligence.deal_momentum.slice(1)}
                  </span>
                )}
                {callIntelligence.overall_pmf_signal && (
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${
                    callIntelligence.overall_pmf_signal === "strong" ? "bg-emerald-50 text-emerald-700 border border-emerald-200" :
                    callIntelligence.overall_pmf_signal === "moderate" ? "bg-blue-50 text-blue-700 border border-blue-200" :
                    callIntelligence.overall_pmf_signal === "weak" ? "bg-amber-50 text-amber-700 border border-amber-200" :
                    "bg-memoir-bg text-memoir-text-muted border border-memoir-border"
                  }`}>
                    PMF: {callIntelligence.overall_pmf_signal.charAt(0).toUpperCase() + callIntelligence.overall_pmf_signal.slice(1)}
                  </span>
                )}
              </div>

              {/* Buy Signal indicator */}
              {callIntelligence.has_committed_next_step != null && (
                <div className="flex items-center gap-2 mb-3 p-2 rounded-button bg-memoir-bg">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${callIntelligence.has_committed_next_step ? "bg-emerald-500" : "bg-memoir-text-muted/40"}`} />
                  <span className="text-xs text-memoir-text-secondary">
                    {callIntelligence.has_committed_next_step
                      ? `Committed next step${callIntelligence.next_step_type ? `: ${callIntelligence.next_step_type}` : ""}`
                      : "No committed next step"}
                  </span>
                </div>
              )}

              {/* Coaching Insight */}
              {(() => {
                const pmf = callIntelligence.pmf_signals as Record<string, unknown> | undefined;
                const coachingInsight = pmf?.coaching_insight as Record<string, string> | undefined;
                const forRep = coachingInsight?.for_rep;
                if (!forRep) return null;
                return (
                  <div className="mb-3">
                    <p className="text-xs text-memoir-text-muted mb-1">Coaching Insight</p>
                    <p className="text-xs text-memoir-text-secondary leading-relaxed">{forRep}</p>
                  </div>
                );
              })()}

              {/* Hesitation Signals */}
              {(() => {
                const pmf = callIntelligence.pmf_signals as Record<string, unknown> | undefined;
                const hesitation = pmf?.hesitation_signals as Array<string | Record<string, unknown>> | undefined;
                if (!hesitation || hesitation.length === 0) return null;
                return (
                  <div>
                    <p className="text-xs text-memoir-text-muted mb-1.5">Hesitation Signals</p>
                    <div className="space-y-1">
                      {hesitation.map((h, i) => {
                        const text = typeof h === "string" ? h : (h as Record<string, unknown>)?.signal as string || JSON.stringify(h);
                        return (
                          <div key={i} className="flex items-start gap-1.5">
                            <span className="text-amber-500 text-xs mt-px flex-shrink-0">&bull;</span>
                            <span className="text-xs text-memoir-text-secondary leading-relaxed">{text}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}
            </div>
          )}

          {/* Quick Notes */}
          {quickNotes && (
            <div className="bg-memoir-surface border border-memoir-border rounded-card p-4">
              <h3 className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-3 flex items-center gap-1.5">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
                Quick Notes
              </h3>
              <p className="text-xs text-memoir-text-secondary whitespace-pre-wrap leading-relaxed">{quickNotes}</p>
            </div>
          )}
        </div>
      </div>

      {/* Send Email Dialog */}
      {showEmailDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowEmailDialog(false)}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-4 border-b border-memoir-border">
              <h2 className="text-sm font-semibold text-memoir-text">Send Meeting Summary</h2>
              <button onClick={() => setShowEmailDialog(false)} className="text-memoir-text-muted hover:text-memoir-text">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="px-5 py-4 space-y-4 overflow-y-auto flex-1">
              <div>
                <label className="block text-xs font-medium text-memoir-text mb-1">To</label>
                <input
                  type="email"
                  value={emailTo}
                  onChange={(e) => { setEmailTo(e.target.value); setEmailSent(false); }}
                  placeholder="recipient@company.com"
                  className="w-full px-3 py-2 text-sm border border-memoir-border rounded-lg focus:outline-none focus:ring-2 focus:ring-memoir-indigo/20 focus:border-memoir-indigo"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-memoir-text mb-1">Template</label>
                <select
                  value={emailTemplate}
                  onChange={async (e) => {
                    const tmpl = e.target.value;
                    setEmailTemplate(tmpl);
                    setEmailPreviewHtml("");
                    try {
                      const preview = await api.getEmailPreview(id, tmpl);
                      setEmailPreviewHtml(preview.html_body);
                      setEmailPreviewSubject(preview.subject);
                    } catch { setEmailPreviewHtml(""); }
                  }}
                  className="w-full px-3 py-2 text-sm border border-memoir-border rounded-lg focus:outline-none focus:ring-2 focus:ring-memoir-indigo/20 focus:border-memoir-indigo"
                >
                  <option value="recorder">Full Summary (Recorder)</option>
                  <option value="internal">Team Member Summary</option>
                  <option value="external">External Participant Summary</option>
                </select>
              </div>
              {emailPreviewSubject && (
                <div>
                  <label className="block text-xs font-medium text-memoir-text mb-1">Subject</label>
                  <p className="text-sm text-memoir-text-secondary">{emailPreviewSubject}</p>
                </div>
              )}
              {emailPreviewHtml ? (
                <div>
                  <label className="block text-xs font-medium text-memoir-text mb-1">Preview</label>
                  <div className="border border-memoir-border rounded-lg overflow-hidden bg-white" style={{ maxHeight: 400 }}>
                    <iframe
                      srcDoc={emailPreviewHtml}
                      title="Email Preview"
                      className="w-full border-0"
                      style={{ height: 380 }}
                      sandbox="allow-same-origin"
                    />
                  </div>
                </div>
              ) : (
                <p className="text-sm text-memoir-text-muted italic">Loading preview...</p>
              )}
              {emailLog.length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-memoir-text mb-1">Previously Sent</label>
                  <div className="space-y-1">
                    {emailLog.map((log) => (
                      <div key={log.id} className="flex items-center justify-between text-xs text-memoir-text-muted">
                        <span>{log.to_email}</span>
                        <span>{log.status === "sent" ? "\u2713" : "\u2717"} {new Date(log.created_at).toLocaleDateString()} {new Date(log.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-memoir-border">
              {emailSent && <span className="text-xs text-emerald-600 font-medium">Email sent successfully!</span>}
              <button onClick={() => setShowEmailDialog(false)} className="px-4 py-2 text-xs font-medium text-memoir-text-secondary border border-memoir-border rounded-lg hover:bg-memoir-bg transition-colors">
                Cancel
              </button>
              <button
                disabled={!emailTo || !emailTo.includes("@") || emailSending || !emailPreviewHtml}
                onClick={async () => {
                  setEmailSending(true);
                  try {
                    await api.sendMeetingEmail(id, emailTo, emailTemplate);
                    setShowEmailDialog(false);
                  } catch {
                    alert("Failed to send email");
                  } finally {
                    setEmailSending(false);
                  }
                }}
                className="px-4 py-2 text-xs font-medium text-white bg-memoir-indigo rounded-lg hover:bg-memoir-indigo/90 transition-colors disabled:opacity-50"
              >
                {emailSending ? "Sending..." : "Send Email"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
