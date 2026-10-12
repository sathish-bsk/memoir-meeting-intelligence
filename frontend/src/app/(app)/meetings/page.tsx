"use client";

import { useState, useEffect, useRef, useCallback, Suspense, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { api, ApiError } from "@/lib/api";
import { notifyMeetingsChanged } from "@/lib/utils";
import { Meeting, CalendarEvent, DashboardStats, EnhancedSearchResponse } from "@/lib/types";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { CreateCallModal } from "@/components/capture/create-call-modal";
import { UploadCallModal } from "@/components/capture/upload-call-modal";
// import { NudgeBanner } from "@/components/nudge-banner";
// import { useNudgeBanners } from "@/hooks/use-nudge-banners";

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
  };
  const c = config[status] || config.pending;
  return (
    <span className={`text-xs font-medium px-2.5 py-1 rounded-full border ${c.color}`}>
      {c.label}
    </span>
  );
};

const CrmStatusBadge = ({ status }: { status: string | null }) => {
  if (!status) return null;
  const config: Record<string, { label: string; color: string }> = {
    pushed: { label: "\u2713 Pushed to CRM", color: "bg-emerald-50 text-emerald-700 border-emerald-200" },
    pending: { label: "CRM pending", color: "bg-amber-50 text-amber-700 border-amber-200" },
    skipped: { label: "Skipped", color: "bg-memoir-bg text-memoir-text-muted border-memoir-border" },
  };
  const c = config[status] || config.pending;
  return (
    <span className={`text-xs font-medium px-2.5 py-1 rounded-full border ${c.color}`}>
      {c.label}
    </span>
  );
};

const ConfidenceMeter = ({ value }: { value: number | null }) => {
  if (value === null) return null;
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

const SourceBadge = ({
  source,
  meetingUrl,
  botJobId,
}: {
  source: string | null;
  meetingUrl?: string | null;
  botJobId?: string | null;
}) => {
  // Source wins over bot_job_id — tab recordings set bot_job_id to a
  // "tab-audio-<session_id>" string (job reference, not bot involvement).
  if (source === "extension_tab_audio") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-emerald-50 text-emerald-600 border border-emerald-200">
        <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
        </svg>
        Extension Tab Recording
      </span>
    );
  }
  // Non-bot bot_job_id prefixes: "tab-audio-", "upload-", "link-upload-".
  const isRealBot =
    !!botJobId &&
    !botJobId.startsWith("tab-audio-") &&
    !botJobId.startsWith("upload-") &&
    !botJobId.startsWith("link-upload-");
  if (isRealBot) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-indigo-50 text-indigo-600 border border-indigo-200">
        <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z" />
        </svg>
        Extension Bot Recording
      </span>
    );
  }
  if (source === "calendar") {
    const isTeams = meetingUrl?.includes("teams.microsoft.com") || meetingUrl?.includes("teams.live.com");
    const isZoom = meetingUrl?.includes("zoom.us");
    const label = isTeams ? "Outlook" : isZoom ? "Zoom Cal" : "Google Cal";
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-blue-50 text-blue-600 border border-blue-200">
        <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V10h14v10zm0-12H5V6h14v2z" />
        </svg>
        {label}
      </span>
    );
  }
  if (source === "upload") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-violet-50 text-violet-600 border border-violet-200">
        <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
        </svg>
        Upload
      </span>
    );
  }
  if (source === "zoom_cloud") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-blue-50 text-blue-600 border border-blue-200">
        <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M17.5 3A3.5 3.5 0 0 0 14 6.5v3H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-1V6.5a2.5 2.5 0 0 1 5 0V8h1V6.5A3.5 3.5 0 0 0 17.5 3zM16 11.5v7H4v-7h12zm4-1l2 1.5v5l-2 1.5v-8z" />
        </svg>
        Zoom Cloud
      </span>
    );
  }
  if (source === "web_recorder") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-purple-50 text-purple-600 border border-purple-200">
        <svg className="w-2.5 h-2.5" fill="currentColor" viewBox="0 0 24 24">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2h-2v2a9 9 0 0 0 8 8.94V23h-3v2h10v-2h-3v-2.06A9 9 0 0 0 21 12v-2h-2z" />
        </svg>
        Web Recorder
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-memoir-bg text-memoir-text-muted border border-memoir-border">
      Bot
    </span>
  );
};

// ─── Helpers ────────────────────────────────────────────────────

function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const mins = Math.round(seconds / 60);
  return `${mins} min`;
}

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function formatEventTime(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const startStr = s.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const endStr = e.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const dateStr = s.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  return `${dateStr}, ${startStr} \u2013 ${endStr}`;
}

function formatMeetingTime(meeting: Meeting): string {
  const ref = meeting.started_at || meeting.scheduled_start_at || meeting.created_at;
  const d = new Date(ref);
  const dateStr = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const startStr = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  if (meeting.started_at && meeting.ended_at) {
    const endStr = new Date(meeting.ended_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    return `${dateStr}, ${startStr} \u2013 ${endStr}`;
  }
  return `${dateStr}, ${startStr}`;
}

function isEventActionable(event: CalendarEvent): boolean {
  if (event.linked_meeting_id) return false;
  const now = new Date();
  const end = new Date(event.end_time);
  return now < end;
}

function isMissedEvent(event: CalendarEvent): boolean {
  if (event.linked_meeting_id) return false;
  const now = new Date();
  const end = new Date(event.end_time);
  return now >= end;
}

function confidenceBorderColor(score: number | null): string {
  if (score === null) return "";
  if (score >= 75) return "border-l-4 border-l-emerald-400";
  if (score >= 50) return "border-l-4 border-l-amber-400";
  return "border-l-4 border-l-red-400";
}

// ─── Date range helpers ────────────────────────────────────────

type DatePreset = "this_week" | "last_7" | "last_30" | "all";

function getDateRange(preset: DatePreset): { start: string | null; end: string | null } {
  const now = new Date();
  const toISO = (d: Date) => d.toISOString().split("T")[0];

  switch (preset) {
    case "this_week": {
      const day = now.getDay();
      const diff = day === 0 ? 6 : day - 1; // Monday start
      const start = new Date(now);
      start.setDate(now.getDate() - diff);
      return { start: toISO(start), end: toISO(now) };
    }
    case "last_7": {
      const start = new Date(now);
      start.setDate(now.getDate() - 7);
      return { start: toISO(start), end: toISO(now) };
    }
    case "last_30": {
      const start = new Date(now);
      start.setDate(now.getDate() - 30);
      return { start: toISO(start), end: toISO(now) };
    }
    case "all":
    default:
      return { start: null, end: null };
  }
}

const DATE_PRESETS: { key: DatePreset; label: string }[] = [
  { key: "this_week", label: "This week" },
  { key: "last_7", label: "Last 7 days" },
  { key: "last_30", label: "Last 30 days" },
  { key: "all", label: "All time" },
];

// ─── Calendar components ───────────────────────────────────────

function CalendarProviderBadge({ provider }: { provider?: string }) {
  if (!provider) return null;
  const isGoogle = provider === "google";
  const label = isGoogle ? "Google" : "Outlook";
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-white/70 text-memoir-text-muted border border-memoir-border">
      {isGoogle ? (
        <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none">
          <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
          <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
          <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
          <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
        </svg>
      ) : (
        <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none">
          <path d="M2 3h9v9H2V3z" fill="#F25022"/>
          <path d="M13 3h9v9h-9V3z" fill="#7FBA00"/>
          <path d="M2 13h9v9H2v-9z" fill="#00A4EF"/>
          <path d="M13 13h9v9h-9v-9z" fill="#FFB900"/>
        </svg>
      )}
      {label}
    </span>
  );
}

function ActionableEventsSection({
  events,
  onSchedule,
  schedulingId,
}: {
  events: CalendarEvent[];
  onSchedule: (event: CalendarEvent) => void;
  schedulingId: string | null;
}) {
  if (events.length === 0) return null;

  const now = new Date();
  const ongoing = events.filter((e) => {
    const start = new Date(e.start_time);
    const end = new Date(e.end_time);
    return start <= now && now < end;
  });
  const upcoming = events
    .filter((e) => new Date(e.start_time) > now)
    .sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime());

  return (
    <div className="mb-8">
      <h3 className="text-xs font-semibold text-blue-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V10h14v10zm0-12H5V6h14v2z" />
        </svg>
        Calendar Events
      </h3>
      <div className="space-y-2">
        {ongoing.length > 0 && (
          <>
            <p className="text-xs font-medium text-green-700 uppercase tracking-wider">Ongoing</p>
            {ongoing.map((event) => (
              <div key={event.id} className="p-4 border rounded-card bg-green-50 border-green-200">
                <div className="flex justify-between items-start">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-memoir-text text-sm">{event.title}</span>
                      <span className="flex items-center gap-1 text-xs font-medium text-green-700">
                        <span className="relative flex h-2 w-2">
                          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                          <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                        </span>
                        Live
                      </span>
                      <CalendarProviderBadge provider={event.provider} />
                    </div>
                    <div className="text-xs text-memoir-text-muted mt-1">
                      {formatEventTime(event.start_time, event.end_time)}
                    </div>
                  </div>
                  {event.meeting_url ? (
                    <button
                      onClick={() => onSchedule(event)}
                      disabled={schedulingId === event.id}
                      className="ml-3 px-3 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
                    >
                      {schedulingId === event.id ? "Scheduling..." : "Record"}
                    </button>
                  ) : (
                    <span className="ml-3 text-xs text-memoir-text-muted">No meeting link</span>
                  )}
                </div>
              </div>
            ))}
          </>
        )}
        {upcoming.length > 0 && (
          <>
            <p className={`text-xs font-medium text-blue-600 uppercase tracking-wider ${ongoing.length > 0 ? "mt-3" : ""}`}>Upcoming</p>
            {upcoming.map((event) => (
              <div key={event.id} className="p-4 border rounded-card bg-blue-50 border-blue-200">
                <div className="flex justify-between items-start">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-memoir-text text-sm">{event.title}</span>
                      <CalendarProviderBadge provider={event.provider} />
                    </div>
                    <div className="text-xs text-memoir-text-muted mt-1">
                      {formatEventTime(event.start_time, event.end_time)}
                    </div>
                  </div>
                  {event.meeting_url ? (
                    <button
                      onClick={() => onSchedule(event)}
                      disabled={schedulingId === event.id}
                      className="ml-3 px-3 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
                    >
                      {schedulingId === event.id ? "Scheduling..." : "Record"}
                    </button>
                  ) : (
                    <span className="ml-3 text-xs text-memoir-text-muted">No meeting link</span>
                  )}
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

// ─── Modals ────────────────────────────────────────────────────

function LinkUploadModal({
  open,
  onOpenChange,
  teamId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teamId: string;
  onCreated: () => void;
}) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const resetForm = () => {
    setUrl("");
    setTitle("");
    setError("");
    setSubmitting(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      await api.uploadFromLink({
        url: url.trim(),
        team_id: teamId,
        title: title.trim() || undefined,
      });
      resetForm();
      onOpenChange(false);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to upload from link");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) resetForm(); onOpenChange(v); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload from link</DialogTitle>
          <DialogDescription>
            Paste a direct link to an audio/video file or YouTube video for transcription.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-memoir-text-secondary mb-1">
              Media URL
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://youtube.com/watch?v=... or https://example.com/recording.mp4"
              className="w-full px-3 py-2 text-sm border border-memoir-border rounded-button focus:outline-none focus:ring-2 focus:ring-memoir-indigo focus:border-transparent"
              disabled={submitting}
            />
            <p className="mt-1 text-xs text-memoir-text-muted">
              Supported: YouTube links, .mp3, .mp4, .wav, .m4a, .webm, .ogg, .mov, .mkv, .aac
            </p>
          </div>
          <div>
            <label className="block text-sm font-medium text-memoir-text-secondary mb-1">
              Title <span className="text-memoir-text-muted">(optional)</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Sales Demo with Acme"
              className="w-full px-3 py-2 text-sm border border-memoir-border rounded-button focus:outline-none focus:ring-2 focus:ring-memoir-indigo focus:border-transparent"
              disabled={submitting}
            />
          </div>
          {error && (
            <p className="text-sm text-red-600">{error}</p>
          )}
          <DialogFooter>
            <button
              type="button"
              onClick={() => { resetForm(); onOpenChange(false); }}
              className="px-4 py-2 text-sm font-medium text-memoir-text-secondary bg-memoir-surface border border-memoir-border rounded-button hover:bg-memoir-bg transition-colors"
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !url.trim()}
              className="px-4 py-2 text-sm font-medium text-white bg-memoir-indigo rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
            >
              {submitting ? "Uploading..." : "Transcribe"}
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ─── Stats Strip ───────────────────────────────────────────────

function StatsStrip({ stats, totalCount }: { stats: DashboardStats; totalCount: number }) {
  const totalMinutes = Math.round(stats.total_minutes);

  return (
    <div className="grid grid-cols-3 gap-3 mb-4">
      <div className="bg-memoir-surface border border-memoir-border rounded-card p-3 text-center">
        <p className="text-lg font-bold text-memoir-text">{totalCount}</p>
        <p className="text-xs text-memoir-text-muted">Completed</p>
      </div>
      <div className="bg-memoir-surface border border-memoir-border rounded-card p-3 text-center">
        <p className="text-lg font-bold text-memoir-text">{totalMinutes}</p>
        <p className="text-xs text-memoir-text-muted">Minutes</p>
      </div>
      <div className="bg-memoir-surface border border-memoir-border rounded-card p-3 text-center">
        <p className="text-lg font-bold text-memoir-text">{stats.this_week_meetings}</p>
        <p className="text-xs text-memoir-text-muted">This week</p>
      </div>
    </div>
  );
}

// ─── Main Page ─────────────────────────────────────────────────

// ─── Filter state types ─────────────────────────────────────────

interface FilterState {
  status: string;            // "all" | "completed" | "recording" | "processing" | "failed" | "recorded" | "missed"
  source: string;            // "all" | "manual" | "calendar" | "upload" | "zoom_cloud" | "extension_tab_audio" | "web_recorder"
  crm_push_status: string;   // "all" | "pushed" | "pending" | "skipped"
  confidence: string;        // "all" | "high" | "medium" | "low"
}

const DEFAULT_FILTERS: FilterState = {
  status: "all",
  source: "all",
  crm_push_status: "all",
  confidence: "all",
};

function filtersToApiParams(filters: FilterState) {
  const params: Record<string, string | number | boolean | undefined> = {};

  // Status mapping
  if (filters.status === "recorded") {
    params.has_recording = true;
  } else if (filters.status === "processing") {
    params.status = "processing"; // backend handles processing + waiting_for_transcription
  } else if (filters.status !== "all" && filters.status !== "missed") {
    params.status = filters.status;
  }

  // Source
  if (filters.source !== "all") params.source = filters.source;

  // CRM push status
  if (filters.crm_push_status !== "all") params.crm_push_status = filters.crm_push_status;

  // Confidence ranges
  if (filters.confidence === "high") params.confidence_min = 75;
  else if (filters.confidence === "medium") {
    params.confidence_min = 50;
    params.confidence_max = 74;
  } else if (filters.confidence === "low") params.confidence_max = 49;

  return params;
}

function hasAdvancedFilters(filters: FilterState): boolean {
  return filters.source !== "all" || filters.crm_push_status !== "all" || filters.confidence !== "all";
}

import { MeetingsV2 } from "@/components/meetings-v2";
import { MeetingsV3 } from "@/components/meetings-v3";

export default function CallsPage() {
  // v3 takes precedence (plan/meetings-events-v3-list.md §4).
  if (process.env.NEXT_PUBLIC_FEATURE_MEETINGS_LIST_V3 === "true") {
    return <MeetingsV3 />;
  }
  // Revamp v2 — opt in via NEXT_PUBLIC_MEETINGS_V2=true.
  if (process.env.NEXT_PUBLIC_MEETINGS_V2 === "true") {
    return <MeetingsV2 />;
  }
  return <CallsPageV1 />;
}

function CallsPageV1() {
  return (
    <Suspense fallback={
      <div className="max-w-3xl mx-auto">
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-memoir-surface border border-memoir-border rounded-card p-4 animate-pulse">
              <div className="h-4 bg-memoir-bg rounded w-1/3 mb-3" />
              <div className="h-3 bg-memoir-bg rounded w-1/2" />
            </div>
          ))}
        </div>
      </div>
    }>
      <CallsPageInner />
    </Suspense>
  );
}

function CallsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const filterIdsParam = searchParams.get("filter_ids");
  const intelLabel = searchParams.get("intel_label");
  const timestampsParam = searchParams.get("timestamps");
  const searchParam = searchParams.get("search");
  const isMinimalMode = !!filterIdsParam;
  const isSearchMode = !!searchParam;

  const timestampMap: Record<string, number> = useMemo(() => {
    if (!timestampsParam) return {};
    try { return JSON.parse(timestampsParam); } catch { return {}; }
  }, [timestampsParam]);

  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const isFounder = user?.team_role === "owner" || user?.team_role === "admin";
  const [allMeetings, setAllMeetings] = useState<Meeting[]>([]);
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[]>([]);
  const [hasCalendar, setHasCalendar] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [hasZoom, setHasZoom] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [statusLoaded, setStatusLoaded] = useState(false);
  const [totalMeetings, setTotalMeetings] = useState<number | null>(null);
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [currentPage, setCurrentPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [datePreset, setDatePreset] = useState<DatePreset>("all");
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showUploadDropdown, setShowUploadDropdown] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [showLinkUploadModal, setShowLinkUploadModal] = useState(false);
  const uploadDropdownRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const loadMoreRef = useRef<() => void>(() => {});
  const [schedulingId, setSchedulingId] = useState<string | null>(null);

  // Search mode state
  const [searchQuery, setSearchQuery] = useState(searchParam || "");
  const [searchData, setSearchData] = useState<EnhancedSearchResponse | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchDebounceRef = useRef<NodeJS.Timeout | null>(null);

  const PER_PAGE = 20;

  // const { isVisible: isNudgeVisible, dismissBanner } = useNudgeBanners(["connect-calendar", "connect-zoom"]);


  const loadMeetings = useCallback((overrideFilters?: FilterState, overridePreset?: DatePreset, reset = true) => {
    if (!activeTeamId) {
      setLoading(false);
      return;
    }

    const activeFilters = overrideFilters ?? filters;
    const range = getDateRange(overridePreset ?? datePreset);
    const apiParams = filtersToApiParams(activeFilters);

    if (reset) setLoading(true);
    api
      .getMeetings({
        team_id: activeTeamId,
        page: 1,
        per_page: PER_PAGE,
        start_date: range.start || undefined,
        end_date: range.end || undefined,
        filter_ids: filterIdsParam || undefined,
        ...apiParams,
      })
      .then((data) => {
        setAllMeetings(data.meetings);
        setTotalMeetings(data.total);
        if (data.counts) setStatusCounts(data.counts);
        setCurrentPage(1);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId, filters, datePreset, filterIdsParam]);

  const loadMoreMeetings = useCallback(() => {
    if (!activeTeamId || loadingMore) return;
    if (totalMeetings !== null && allMeetings.length >= totalMeetings) return;

    const nextPage = currentPage + 1;
    const range = getDateRange(datePreset);
    const apiParams = filtersToApiParams(filters);

    setLoadingMore(true);
    api
      .getMeetings({
        team_id: activeTeamId,
        page: nextPage,
        per_page: PER_PAGE,
        start_date: range.start || undefined,
        end_date: range.end || undefined,
        filter_ids: filterIdsParam || undefined,
        ...apiParams,
      })
      .then((data) => {
        setAllMeetings((prev) => [...prev, ...data.meetings]);
        setTotalMeetings(data.total);
        if (data.counts) setStatusCounts(data.counts);
        setCurrentPage(nextPage);
      })
      .catch(() => {})
      .finally(() => setLoadingMore(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId, currentPage, loadingMore, totalMeetings, allMeetings.length, datePreset, filters, filterIdsParam]);

  // Keep ref in sync so IntersectionObserver always calls latest version
  loadMoreRef.current = loadMoreMeetings;

  // Derived: are there more pages to load? (computed before effects that need it)
  const hasMore = totalMeetings !== null && allMeetings.length < totalMeetings;

  const loadCalendarAndZoomStatus = async () => {
    if (!activeTeamId) return;
    // Calendar
    const checks = await Promise.allSettled([
      api.getCalendarStatus("google"),
      api.getCalendarStatus("outlook"),
    ]);
    const anyCalendar = checks.some((r) => r.status === "fulfilled");
    setHasCalendar(anyCalendar);
    if (anyCalendar) {
      try {
        const evts = await api.getCalendarEvents(activeTeamId);
        setCalendarEvents(evts);
      } catch {
        // failed to load events
      }
    }
    // Zoom
    try {
      await api.getZoomStatus();
      setHasZoom(true);
    } catch {
      setHasZoom(false);
    }
    setStatusLoaded(true);
  };

  const loadStats = async () => {
    if (!activeTeamId) return;
    try {
      const s = await api.getDashboardStats(activeTeamId);
      setStats(s);
    } catch {
      // stats endpoint may not exist yet
    }
  };

  useEffect(() => {
    if (isSearchMode) {
      setLoading(false);
      return;
    }
    loadMeetings();
    loadCalendarAndZoomStatus();
    loadStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId, isSearchMode]);

  // Close upload dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (uploadDropdownRef.current && !uploadDropdownRef.current.contains(e.target as Node)) {
        setShowUploadDropdown(false);
      }
    };
    if (showUploadDropdown) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [showUploadDropdown]);

  // Infinite scroll — observe sentinel element
  // Uses loadMoreRef to avoid recreating observer on every data load (which causes cascade)
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          loadMoreRef.current();
        }
      },
      { rootMargin: "200px" }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
    // Re-attach only when sentinel mounts/unmounts (hasMore or status tab changes)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasMore, filters.status]);

  // Search mode — debounced search effect
  useEffect(() => {
    if (!isSearchMode || !activeTeamId) return;
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    const trimmed = searchQuery.trim();
    if (!trimmed) { setSearchData(null); setSearchLoading(false); return; }
    setSearchLoading(true);
    searchDebounceRef.current = setTimeout(async () => {
      try {
        const res = await api.search(trimmed, activeTeamId, { perPage: 20 });
        setSearchData(res);
      } catch { setSearchData(null); }
      finally { setSearchLoading(false); }
    }, 300);
    return () => { if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, isSearchMode, activeTeamId]);

  const handleSchedule = async (event: CalendarEvent) => {
    if (!activeTeamId) return;
    setSchedulingId(event.id);
    try {
      await api.scheduleBot(event.id);
      const updated = await api.getCalendarEvents(activeTeamId);
      setCalendarEvents(updated);
      loadMeetings();
      notifyMeetingsChanged();
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Failed to schedule bot");
    } finally {
      setSchedulingId(null);
    }
  };

  const handleDatePresetChange = (preset: DatePreset) => {
    setDatePreset(preset);
    loadMeetings(undefined, preset, true);
  };

  const handleStatusFilterChange = (status: string) => {
    const newFilters = { ...filters, status };
    setFilters(newFilters);
    loadMeetings(newFilters);
  };

  const handleAdvancedFilterChange = (key: keyof FilterState, value: string) => {
    const newFilters = { ...filters, [key]: value };
    setFilters(newFilters);
    loadMeetings(newFilters);
  };

  const clearAdvancedFilters = () => {
    const newFilters = { ...filters, source: "all", crm_push_status: "all", confidence: "all" };
    setFilters(newFilters);
    loadMeetings(newFilters);
  };

  // Derived data
  const actionableEvents = hasCalendar ? calendarEvents.filter(isEventActionable) : [];
  const missedEvents = hasCalendar
    ? calendarEvents
        .filter(isMissedEvent)
        .sort((a, b) => new Date(b.start_time).getTime() - new Date(a.start_time).getTime())
    : [];

  // allMeetings IS the filtered list (server already filtered it)
  const meetings = allMeetings;

  // Group meetings by date
  const grouped: Record<string, Meeting[]> = {};
  meetings.forEach((m) => {
    const dateKey = formatDate(m.created_at);
    if (!grouped[dateKey]) grouped[dateKey] = [];
    grouped[dateKey].push(m);
  });

  const hasAnyCalls = (statusCounts.all > 0) || (totalMeetings !== null && totalMeetings > 0) || missedEvents.length > 0;
  const filteredEmpty = filters.status === "missed" ? missedEvents.length === 0 : meetings.length === 0;

  // Tab counts from server — accurate regardless of page size
  const filterTabs = [
    { key: "all", label: "All calls", count: statusCounts.all ?? totalMeetings ?? 0 },
    { key: "completed", label: "Completed", count: statusCounts.completed ?? 0 },
    { key: "recorded", label: "Recorded", count: statusCounts.recorded ?? 0 },
    { key: "recording", label: "Recording", count: statusCounts.recording ?? 0 },
    { key: "processing", label: "Processing", count: statusCounts.processing ?? 0 },
    { key: "failed", label: "Failed", count: statusCounts.failed ?? 0 },
    ...(missedEvents.length > 0 ? [{ key: "missed", label: "Missed", count: missedEvents.length }] : []),
  ];

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-bold text-memoir-text mb-6">
          {isMinimalMode ? (intelLabel || "Filtered Calls") : (isFounder ? "All Calls" : "My Calls")}
        </h1>
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-memoir-surface border border-memoir-border rounded-card p-4 animate-pulse">
              <div className="h-4 bg-memoir-bg rounded w-1/3 mb-3" />
              <div className="h-3 bg-memoir-bg rounded w-1/2 mb-2" />
              <div className="h-3 bg-memoir-bg rounded w-1/4" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  // ─── Search mode ────────────────────────────────────────────
  if (isSearchMode) {
    return (
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => router.push("/meetings")}
            className="text-memoir-text-muted hover:text-memoir-text transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
            </svg>
          </button>
          <h1 className="text-xl font-bold text-memoir-text">Search Results</h1>
        </div>

        {/* Search input */}
        <div className="mb-4 flex items-center gap-3 bg-memoir-surface border border-memoir-border rounded-card px-4 py-3">
          <svg className="w-[18px] h-[18px] text-memoir-text-muted flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
          </svg>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search calls and transcripts..."
            className="flex-1 bg-transparent text-sm text-memoir-text placeholder:text-memoir-text-muted outline-none"
            autoFocus
          />
          {searchLoading && (
            <div className="w-4 h-4 border-2 border-memoir-border border-t-memoir-indigo rounded-full animate-spin flex-shrink-0" />
          )}
          <button onClick={() => router.push("/meetings")} className="text-xs text-memoir-indigo hover:underline font-medium">
            Clear
          </button>
        </div>

        {/* Results count */}
        {searchData && !searchLoading && (
          <p className="text-xs text-memoir-text-muted mb-4">
            {searchData.call_total + searchData.transcript_total} result{searchData.call_total + searchData.transcript_total !== 1 ? "s" : ""} for &ldquo;{searchQuery}&rdquo;
          </p>
        )}

        {/* Results */}
        {searchData && (
          <div className="space-y-6">
            {/* Call matches */}
            <div>
              <h3 className="text-xs font-semibold text-memoir-text-muted uppercase tracking-wider mb-2">
                Calls ({searchData.call_total})
              </h3>
              {searchData.call_results.length > 0 ? (
                <div className="space-y-2">
                  {searchData.call_results.map((r) => (
                    <div
                      key={r.meeting_id}
                      onClick={() => router.push(`/meetings/${r.meeting_id}`)}
                      className="p-4 bg-memoir-surface border border-memoir-border rounded-card cursor-pointer hover:border-memoir-indigo/30 hover:shadow-card transition-all"
                    >
                      <div className="flex justify-between items-start mb-1">
                        <div className="flex-1 min-w-0">
                          <span className="font-semibold text-memoir-text text-sm">{r.title || "Untitled Call"}</span>
                          {r.prospect_name && (
                            <span className="text-xs text-memoir-text-muted ml-1.5">
                              with {r.prospect_name}{r.prospect_company ? ` (${r.prospect_company})` : ""}
                            </span>
                          )}
                        </div>
                      </div>
                      {r.snippet && <p className="text-xs text-memoir-text-muted line-clamp-2">{r.snippet}</p>}
                      {r.date && <p className="text-xs text-memoir-text-muted mt-1">{formatDate(r.date)}</p>}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-memoir-text-muted px-1">No matching calls</p>
              )}
            </div>

            {/* Transcript matches */}
            <div>
              <h3 className="text-xs font-semibold text-memoir-text-muted uppercase tracking-wider mb-2">
                Transcripts ({searchData.transcript_total})
              </h3>
              {searchData.transcript_results.length > 0 ? (
                <div className="space-y-2">
                  {searchData.transcript_results.map((r, i) => (
                    <div
                      key={`${r.meeting_id}-${i}`}
                      onClick={() => router.push(r.timestamp_seconds != null ? `/meetings/${r.meeting_id}?t=${r.timestamp_seconds}` : `/meetings/${r.meeting_id}`)}
                      className="p-4 bg-memoir-surface border border-memoir-border rounded-card cursor-pointer hover:border-memoir-indigo/30 hover:shadow-card transition-all"
                    >
                      <div className="flex justify-between items-start mb-1">
                        <div className="flex-1 min-w-0">
                          <span className="font-semibold text-memoir-text text-sm">{r.title || "Untitled Call"}</span>
                          {r.prospect_name && (
                            <span className="text-xs text-memoir-text-muted ml-1.5">
                              with {r.prospect_name}{r.prospect_company ? ` (${r.prospect_company})` : ""}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          {r.timestamp_seconds != null && (
                            <span className="text-[11px] font-mono text-memoir-indigo bg-memoir-indigo/10 rounded px-1.5 py-0.5">
                              {formatTimestamp(r.timestamp_seconds)}
                            </span>
                          )}
                        </div>
                      </div>
                      {r.snippet && (
                        <p
                          className="text-xs text-memoir-text-muted line-clamp-2 mt-0.5"
                          dangerouslySetInnerHTML={{ __html: r.snippet }}
                        />
                      )}
                      {r.date && <p className="text-xs text-memoir-text-muted mt-1">{formatDate(r.date)}</p>}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-memoir-text-muted px-1">No matching transcripts</p>
              )}
            </div>

            {/* No results at all */}
            {searchData.call_total === 0 && searchData.transcript_total === 0 && (
              <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
                <p className="text-sm text-memoir-text-muted">No results for &ldquo;{searchQuery}&rdquo;</p>
              </div>
            )}
          </div>
        )}

        {/* Initial empty state */}
        {!searchData && !searchLoading && !searchQuery.trim() && (
          <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
            <p className="text-sm text-memoir-text-muted">Type a query to search calls and transcripts</p>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center gap-3">
          {isMinimalMode && (
            <button
              onClick={() => router.push("/intelligence")}
              className="text-memoir-text-muted hover:text-memoir-text transition-colors"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
              </svg>
            </button>
          )}
          <h1 className="text-xl font-bold text-memoir-text">
            {isMinimalMode ? (intelLabel || "Filtered Calls") : (isFounder ? "All Calls" : "My Calls")}
          </h1>
        </div>
        {!isMinimalMode && (
          <div className="flex items-center gap-2">
            <div className="relative" ref={uploadDropdownRef}>
              <button
                onClick={() => setShowUploadDropdown(!showUploadDropdown)}
                className="px-3 py-1.5 text-sm font-medium bg-memoir-surface text-memoir-text-secondary border border-memoir-border rounded-button hover:bg-memoir-bg transition-colors flex items-center gap-1.5"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
                </svg>
                Upload
                <svg className="w-3 h-3 ml-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                </svg>
              </button>
              {showUploadDropdown && (
                <div className="absolute right-0 mt-1 w-48 bg-memoir-surface border border-memoir-border rounded-card shadow-elevated z-20 py-1">
                  <button
                    onClick={() => {
                      setShowUploadDropdown(false);
                      setShowUploadModal(true);
                    }}
                    className="w-full text-left px-3 py-2 text-sm text-memoir-text hover:bg-memoir-bg flex items-center gap-2"
                  >
                    <svg className="w-4 h-4 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m6.75 12-3-3m0 0-3 3m3-3v6m-1.5-15H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
                    </svg>
                    Upload file
                  </button>
                  <button
                    onClick={() => { setShowUploadDropdown(false); setShowLinkUploadModal(true); }}
                    className="w-full text-left px-3 py-2 text-sm text-memoir-text hover:bg-memoir-bg flex items-center gap-2"
                  >
                    <svg className="w-4 h-4 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" />
                    </svg>
                    <span>From link</span>
                  </button>
                </div>
              )}
            </div>
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-3 py-1.5 text-sm font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors flex items-center gap-1.5"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
              New Meeting
            </button>
          </div>
        )}
      </div>

      {/* Stats Strip — hidden in minimal mode */}
      {!isMinimalMode && stats && stats.total_meetings > 0 && (
        <StatsStrip stats={stats} totalCount={stats.total_meetings} />
      )}

      {/* Filters — hidden in minimal mode */}
      {!isMinimalMode && hasAnyCalls && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          {DATE_PRESETS.map((p) => (
            <button
              key={p.key}
              onClick={() => handleDatePresetChange(p.key)}
              className={`text-xs px-2.5 py-1 rounded-button border transition-colors ${
                datePreset === p.key
                  ? "bg-memoir-text text-white border-memoir-text"
                  : "bg-memoir-surface text-memoir-text-muted border-memoir-border hover:text-memoir-text-secondary"
              }`}
            >
              {p.label}
            </button>
          ))}
          <div className="w-px h-4 bg-memoir-border mx-1" />
          <select
            value={filters.source}
            onChange={(e) => handleAdvancedFilterChange("source", e.target.value)}
            className="text-xs px-2.5 py-1.5 rounded-button border border-memoir-border bg-memoir-surface text-memoir-text-secondary focus:outline-none focus:ring-1 focus:ring-memoir-indigo"
          >
            <option value="all">All Sources</option>
            <option value="manual">Bot</option>
            <option value="calendar">Calendar</option>
            <option value="upload">Upload</option>
            <option value="zoom_cloud">Zoom Cloud</option>
            <option value="extension">Extension Bot Recording</option>
            <option value="extension_tab_audio">Extension Tab Recording</option>
            <option value="web_recorder">Web Recorder</option>
          </select>
          <select
            value={filters.crm_push_status}
            onChange={(e) => handleAdvancedFilterChange("crm_push_status", e.target.value)}
            className="text-xs px-2.5 py-1.5 rounded-button border border-memoir-border bg-memoir-surface text-memoir-text-secondary focus:outline-none focus:ring-1 focus:ring-memoir-indigo"
          >
            <option value="all">All CRM Status</option>
            <option value="pushed">Pushed</option>
            <option value="pending">Pending</option>
            <option value="skipped">Skipped</option>
          </select>
          <select
            value={filters.confidence}
            onChange={(e) => handleAdvancedFilterChange("confidence", e.target.value)}
            className="text-xs px-2.5 py-1.5 rounded-button border border-memoir-border bg-memoir-surface text-memoir-text-secondary focus:outline-none focus:ring-1 focus:ring-memoir-indigo"
          >
            <option value="all">All Confidence</option>
            <option value="high">High (75+)</option>
            <option value="medium">Medium (50-74)</option>
            <option value="low">Low (&lt;50)</option>
          </select>
          {(datePreset !== "all" || hasAdvancedFilters(filters)) && (
            <button
              onClick={() => {
                if (datePreset !== "all") handleDatePresetChange("all");
                if (hasAdvancedFilters(filters)) clearAdvancedFilters();
              }}
              className="text-xs text-memoir-indigo hover:underline ml-1"
            >
              Clear all
            </button>
          )}
        </div>
      )}

      {/* Status filter tabs — hidden in minimal mode */}
      {!isMinimalMode && hasAnyCalls && (
        <div className="flex gap-2 mb-4 flex-wrap">
          {filterTabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => handleStatusFilterChange(tab.key)}
              className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                filters.status === tab.key
                  ? "bg-memoir-indigo text-white border-memoir-indigo"
                  : "bg-memoir-surface text-memoir-text-secondary border-memoir-border hover:border-memoir-indigo/30"
              }`}
            >
              {tab.label}
              <span className={`ml-1 ${filters.status === tab.key ? "text-white/70" : "text-memoir-text-muted"}`}>
                {tab.count}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Intelligence filter banner */}
      {filterIdsParam && (
        <div className="mb-4 flex items-center gap-3 px-4 py-2.5 bg-memoir-indigo/5 border border-memoir-indigo/20 rounded-card">
          <svg className="w-4 h-4 text-memoir-indigo shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z" />
          </svg>
          <div className="flex-1">
            <p className="text-sm font-medium text-memoir-text">
              {intelLabel || "Intelligence Filter"}
            </p>
            <p className="text-xs text-memoir-text-muted">
              Showing {totalMeetings ?? "..."} call{totalMeetings !== 1 ? "s" : ""}
            </p>
          </div>
          <button
            onClick={() => router.push("/meetings")}
            className="text-xs text-memoir-indigo hover:underline font-medium"
          >
            Clear filter
          </button>
        </div>
      )}

      {/* Nudge Banners — commented out for now, can re-enable later
      {statusLoaded && (
        <div className="space-y-2 mb-4">
          {!hasCalendar && isNudgeVisible("connect-calendar") && (
            <NudgeBanner
              id="connect-calendar"
              message="Connect your calendar for automatic meeting recording"
              ctaText="Connect now"
              ctaHref="/integrations"
              onDismiss={dismissBanner}
            />
          )}
          {!hasZoom && isNudgeVisible("connect-zoom") && (
            <NudgeBanner
              id="connect-zoom"
              message="Connect Zoom to import cloud recordings"
              ctaText="Connect now"
              ctaHref="/integrations"
              onDismiss={dismissBanner}
            />
          )}
        </div>
      )}
      */}

      {/* Actionable calendar events — hidden in minimal mode */}
      {!isMinimalMode && (
        <ActionableEventsSection
          events={actionableEvents}
          onSchedule={handleSchedule}
          schedulingId={schedulingId}
        />
      )}

      {/* Missed events tab */}
      {filters.status === "missed" ? (
        missedEvents.length === 0 ? (
          <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
            <p className="text-sm text-memoir-text-muted">No missed events.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {missedEvents.map((event) => (
              <div
                key={event.id}
                className="p-4 bg-amber-50 border border-amber-200 rounded-card"
              >
                <div className="flex justify-between items-start">
                  <div className="flex-1 min-w-0">
                    <span className="font-semibold text-memoir-text text-sm">{event.title}</span>
                    <div className="flex items-center gap-2 text-xs text-memoir-text-muted mt-1">
                      <span>{formatEventTime(event.start_time, event.end_time)}</span>
                    </div>
                  </div>
                  <span className="ml-3 text-xs font-medium px-2.5 py-1 rounded-full bg-amber-100 text-amber-700 border border-amber-300">
                    Not recorded
                  </span>
                </div>
              </div>
            ))}
          </div>
        )
      ) : filteredEmpty && !hasAnyCalls ? (
        /* No calls at all — empty state */
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-12 text-center">
          <div className="w-12 h-12 bg-memoir-indigo-light rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-memoir-indigo" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 6.75c0 8.284 6.716 15 15 15h2.25a2.25 2.25 0 0 0 2.25-2.25v-1.372c0-.516-.351-.966-.852-1.091l-4.423-1.106c-.44-.11-.902.055-1.173.417l-.97 1.293c-.282.376-.769.542-1.21.38a12.035 12.035 0 0 1-7.143-7.143c-.162-.441.004-.928.38-1.21l1.293-.97c.363-.271.527-.734.417-1.173L6.963 3.102a1.125 1.125 0 0 0-1.091-.852H4.5A2.25 2.25 0 0 0 2.25 4.5v2.25Z" />
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-memoir-text mb-1">No calls yet</h3>
          <p className="text-sm text-memoir-text-muted max-w-xs mx-auto mb-4">
            Record your first call to get started. Paste a meeting link or upload a recording.
          </p>
          <div className="flex items-center justify-center gap-3">
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-4 py-2 text-sm font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors"
            >
              New Meeting
            </button>
            <button
              onClick={() => setShowUploadModal(true)}
              className="px-4 py-2 text-sm font-medium text-memoir-text-secondary bg-memoir-surface border border-memoir-border rounded-button hover:bg-memoir-bg transition-colors"
            >
              Or upload a recording
            </button>
          </div>
        </div>
      ) : filteredEmpty ? (
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
          <p className="text-sm text-memoir-text-muted">
            No calls match the current filters.
          </p>
        </div>
      ) : (
        /* Meeting cards */
        Object.entries(grouped).map(([date, dateMeetings]) => (
          <div key={date} className="mb-6">
            <h3 className="text-xs font-semibold text-memoir-text-muted uppercase tracking-wider mb-3">
              {date}
            </h3>
            <div className="space-y-2">
              {dateMeetings.map((meeting) => {
                const t = timestampMap[meeting.id];
                return (
                <div
                  key={meeting.id}
                  onClick={() => router.push(`/meetings/${meeting.id}${t ? `?t=${t}` : ""}`)}
                  className={`p-4 bg-memoir-surface border border-memoir-border rounded-card cursor-pointer hover:border-memoir-indigo/30 hover:shadow-card transition-all ${confidenceBorderColor(meeting.confidence_score)}`}
                >
                  {/* Row 1: Title + Participant + Confidence */}
                  <div className="flex justify-between items-start mb-1.5">
                    <div className="flex-1 min-w-0">
                      <span className="font-semibold text-memoir-text text-sm">
                        {meeting.title || "Untitled Call"}
                      </span>
                      {meeting.primary_participant_name && (
                        <span className="text-xs text-memoir-text-muted ml-1.5">
                          with {meeting.primary_participant_name}
                          {meeting.primary_participant_company && (
                            <span> ({meeting.primary_participant_company})</span>
                          )}
                        </span>
                      )}
                    </div>
                    <ConfidenceMeter value={meeting.confidence_score} />
                  </div>
                  {/* Summary excerpt */}
                  {meeting.summary_excerpt && (
                    <p className="text-xs text-memoir-text-muted line-clamp-2 mb-1.5">
                      {meeting.summary_excerpt}
                    </p>
                  )}
                  {/* Row 2: Status + Duration + Time + Creator + Source */}
                  <div className="flex items-center gap-2 text-xs text-memoir-text-muted flex-wrap">
                    <StatusBadge status={meeting.status === "completed" && meeting.intelligence_status && meeting.intelligence_status !== "completed" ? "analyzing" : meeting.status} />
                    {meeting.duration_seconds && (
                      <>
                        <span>&middot;</span>
                        <span>{formatDuration(meeting.duration_seconds)}</span>
                      </>
                    )}
                    <span>&middot;</span>
                    <span>{formatMeetingTime(meeting)}</span>
                    {meeting.created_by_name && (
                      <>
                        <span>&middot;</span>
                        <span className="text-memoir-text-secondary">{meeting.created_by_name}</span>
                      </>
                    )}
                    <span>&middot;</span>
                    <SourceBadge source={meeting.source} meetingUrl={meeting.meeting_url} botJobId={meeting.bot_job_id} />
                  </div>
                  {/* Row 3: CRM status */}
                  {meeting.crm_push_status && (
                    <div className="mt-2">
                      <CrmStatusBadge status={meeting.crm_push_status} />
                    </div>
                  )}
                </div>
                );
              })}
            </div>
          </div>
        ))
      )}

      {/* Infinite scroll sentinel + loading indicator */}
      {filters.status !== "missed" && hasMore && (
        <>
          <div ref={sentinelRef} className="h-1" />
          {loadingMore && (
            <div className="flex justify-center py-4">
              <div className="h-5 w-5 border-2 border-memoir-indigo border-t-transparent rounded-full animate-spin" />
            </div>
          )}
        </>
      )}

      {/* Create Call Modal */}
      {activeTeamId && (
        <CreateCallModal
          open={showCreateModal}
          onOpenChange={setShowCreateModal}
          teamId={activeTeamId}
          onCreated={() => { loadMeetings(); loadCalendarAndZoomStatus(); loadStats(); }}
        />
      )}

      {/* Upload Call Modal */}
      {activeTeamId && (
        <UploadCallModal
          open={showUploadModal}
          onOpenChange={setShowUploadModal}
          teamId={activeTeamId}
          onCreated={() => { loadMeetings(); loadStats(); }}
        />
      )}

      {/* Link Upload Modal */}
      {activeTeamId && (
        <LinkUploadModal
          open={showLinkUploadModal}
          onOpenChange={setShowLinkUploadModal}
          teamId={activeTeamId}
          onCreated={() => { loadMeetings(); loadStats(); }}
        />
      )}
    </div>
  );
}
