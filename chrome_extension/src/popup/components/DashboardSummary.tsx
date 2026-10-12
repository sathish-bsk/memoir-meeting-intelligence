import React, { useEffect, useState } from "react";
import type { ExtensionState } from "../../shared/types";
import { sendMessage } from "../../shared/messaging";
import { WEB_BASE } from "../../shared/constants";
import { T, FONT_STACK } from "../theme";
import { PopupHeader } from "./Header";

interface NextMeeting {
  id: string;
  title: string;
  start_time: string;
  meeting_url: string | null;
  bot_scheduled: boolean;
  minutes_until: number;
}

interface Props {
  state: ExtensionState | null;
  onSettings: () => void;
  onAvatarClick: () => void;
  /** Called after the ad-hoc Start CTA succeeds so the popup can re-fetch
   *  state and flip to RecordingStatus without waiting for the 3s poll. */
  onAdHocStarted?: () => void;
}

function platformFromUrl(url: string | null): { label: string; color: string; bg: string } {
  if (!url) return { label: "Meeting", color: T.inkSecondary, bg: T.surfaceSoft };
  if (url.includes("zoom")) return { label: "Zoom", color: "#2D8CFF", bg: "#E3F2FD" };
  if (url.includes("teams.microsoft")) return { label: "Teams", color: "#5B5FC7", bg: "#EDE7F6" };
  return { label: "Google Meet", color: "#00897B", bg: "#E8F5E9" };
}

function fmtRelative(minutes: number): string {
  if (minutes < 1) return "now";
  if (minutes < 60) return `in ${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h < 24) return m === 0 ? `in ${h}h` : `in ${h}h ${m}m`;
  const d = Math.floor(h / 24);
  return `in ${d}d`;
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

/** Idle state (no active meeting) — matches memoir-chrome-extension.jsx
 *  lines 100-169. Stats from the v1 dashboard are intentionally not
 *  shown — mockup keeps the popup focused on capture actions. The
 *  full stats live one click away at WEB_BASE/home. */
export function DashboardSummary({ state, onSettings, onAvatarClick, onAdHocStarted }: Props) {
  const initial = state?.auth?.user?.name?.[0]?.toUpperCase() ?? "?";
  const [nextMeeting, setNextMeeting] = useState<NextMeeting | null>(null);
  const [nextLoading, setNextLoading] = useState(true);

  useEffect(() => {
    sendMessage<NextMeeting | null>({ type: "GET_NEXT_MEETING" })
      .then((res) => {
        if (res.success) setNextMeeting(res.data ?? null);
      })
      .finally(() => setNextLoading(false));
  }, []);

  return (
    <div style={{ background: T.surface, fontFamily: FONT_STACK, color: T.ink }}>
      <PopupHeader initial={initial} onSettings={onSettings} onAvatarClick={onAvatarClick} />

      <div style={{ padding: "20px 20px 16px" }}>
        {/* Status pill */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: T.inkLight }} />
          <span style={{ fontSize: 13, color: T.inkSecondary, fontWeight: 600 }}>No active meeting</span>
        </div>

        {/* Next up card */}
        {nextLoading ? null : nextMeeting ? (
          (() => {
            const p = platformFromUrl(nextMeeting.meeting_url);
            return (
              <div style={{ background: T.surfaceSoft, border: `1px solid ${T.border}`, borderRadius: 12, padding: "14px 16px", marginBottom: 14 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.brand} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="4" width="18" height="18" rx="2" />
                    <line x1="16" y1="2" x2="16" y2="6" />
                    <line x1="8" y1="2" x2="8" y2="6" />
                    <line x1="3" y1="10" x2="21" y2="10" />
                  </svg>
                  <span style={{ fontSize: 11, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.8 }}>Next up</span>
                </div>
                <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>{nextMeeting.title}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: T.inkSecondary, fontWeight: 500 }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={T.inkSecondary} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10" />
                      <polyline points="12 6 12 12 16 14" />
                    </svg>
                    {fmtTime(nextMeeting.start_time)}
                  </span>
                  <span>·</span>
                  <span style={{ color: nextMeeting.minutes_until <= 60 ? T.orange : T.brand, fontWeight: 700 }}>
                    {fmtRelative(nextMeeting.minutes_until)}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: p.color, background: p.bg, padding: "3px 10px", borderRadius: 6 }}>{p.label}</span>
                  {nextMeeting.bot_scheduled && (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                      <span style={{ width: 6, height: 6, borderRadius: "50%", background: T.green }} />
                      <span style={{ fontSize: 11, color: T.green, fontWeight: 600 }}>Bot will auto-join</span>
                    </span>
                  )}
                </div>
              </div>
            );
          })()
        ) : null}

        {/* Quick actions */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 16 }}>
          <button
            onClick={async () => {
              // Ad-hoc recording: extension creates a web_recorder meeting
              // and opens offscreen in mic-only mode. Popup transitions to
              // RecordingStatus once meetingState flips to "recording".
              const res = await sendMessage({ type: "START_RECORDING_AD_HOC" });
              if (!res.success) {
                console.error("[Memoir] Start ad-hoc failed:", res.error);
                alert(`Could not start recording: ${res.error ?? "unknown error"}`);
                return;
              }
              onAdHocStarted?.();
            }}
            style={actionBtn}
          >
            <div style={{ ...iconWell, background: T.orangeLight }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={T.orange} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="1" width="6" height="12" rx="3" />
                <path d="M5 10v1a7 7 0 0 0 14 0v-1M12 19v4M8 23h8" />
              </svg>
            </div>
            <div style={{ flex: 1, textAlign: "left" }}>
              <div style={{ fontWeight: 700, fontSize: 13, color: T.ink }}>Start recording</div>
              <div style={{ fontSize: 11, color: T.inkSecondary, fontWeight: 450 }}>Record an ad-hoc conversation</div>
            </div>
            <Chevron />
          </button>

          <button
            onClick={() => chrome.tabs.create({ url: `${WEB_BASE}/meetings?upload=1` })}
            style={actionBtn}
          >
            <div style={{ ...iconWell, background: T.brandLight }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={T.brand} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
              </svg>
            </div>
            <div style={{ flex: 1, textAlign: "left" }}>
              <div style={{ fontWeight: 700, fontSize: 13, color: T.ink }}>Upload recording</div>
              <div style={{ fontSize: 11, color: T.inkSecondary, fontWeight: 450 }}>Transcribe an audio or video file</div>
            </div>
            <Chevron />
          </button>
        </div>

        {/* Footer: Open Memoir */}
        <div style={{ textAlign: "center", paddingTop: 4 }}>
          <button
            onClick={() => chrome.tabs.create({ url: `${WEB_BASE}/home` })}
            style={{
              background: "none",
              border: "none",
              color: T.brand,
              fontWeight: 700,
              fontSize: 12,
              cursor: "pointer",
              fontFamily: FONT_STACK,
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            Open Memoir
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={T.brand} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

const actionBtn: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  width: "100%",
  background: T.surface,
  border: `1px solid ${T.border}`,
  borderRadius: 10,
  padding: "12px 14px",
  cursor: "pointer",
  fontFamily: FONT_STACK,
  textAlign: "left",
};

const iconWell: React.CSSProperties = {
  width: 34,
  height: 34,
  borderRadius: 8,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
};

function Chevron() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.inkLight} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}
