import React, { useEffect, useState } from "react";
import type { AudioInputDevice, ExtensionState, UploadAudioMode } from "../../shared/types";
import { sendMessage } from "../../shared/messaging";
import { T, FONT_STACK } from "../theme";
import { PopupHeader } from "./Header";

interface Participant {
  name: string;
  email?: string;
  organizer?: boolean;
  isSelf?: boolean;
}

interface Props {
  state: ExtensionState;
  error?: string | null;
  loading?: boolean;
  micDevices?: AudioInputDevice[];
  selectedMicDeviceId?: string;
  selectedUploadMode?: UploadAudioMode;
  micLoading?: boolean;
  micError?: string | null;
  onMicChange?: (deviceId: string) => void;
  onUploadModeChange?: (mode: UploadAudioMode) => void;
  onRecord: () => void;
  onRecordBot: () => void;
  onSettings: () => void;
  onAvatarClick: () => void;
}

const PLATFORM_META = {
  google_meet: { bg: "#E8F5E9", color: "#00897B", label: "Google Meet" },
  zoom: { bg: "#E3F2FD", color: "#2D8CFF", label: "Zoom" },
  teams: { bg: "#EDE7F6", color: "#5B5FC7", label: "Teams" },
} as const;

/** Detected state — matches memoir-chrome-extension.jsx lines 172-238.
 *  Primary CTA: bot recording (default).
 *  Secondary "Record privately instead" toggle exposes the tab-audio
 *  start option. The separate/mixed UploadAudioMode dial isn't exposed
 *  (defaults to "separate") per plan §5. */
export function MeetingDetected({
  state,
  error,
  loading,
  micDevices = [],
  selectedMicDeviceId,
  micLoading,
  micError,
  onMicChange,
  onRecord,
  onRecordBot,
  onSettings,
  onAvatarClick,
}: Props) {
  const [showPrivate, setShowPrivate] = useState(false);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [participantsLoading, setParticipantsLoading] = useState(true);
  const { meeting } = state;
  const platform = meeting.platform ?? "google_meet";
  const platformMeta = PLATFORM_META[platform];
  const initial = state.auth?.user?.name?.[0]?.toUpperCase() ?? "?";

  const [participantCount, setParticipantCount] = useState<number | null>(null);

  // Fetch participants from the meeting tab DOM. Scraper returns
  // {participants, count} — count is sourced from the platform's
  // People button aria-label, names from tile/roster scrape. They can
  // differ (count > names.length) when the panel isn't open and we only
  // see the button badge.
  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      const res = await sendMessage<{ participants: Participant[]; count: number | null }>({
        type: "GET_MEETING_PARTICIPANTS",
      });
      if (cancelled) return;
      const data = res.success && res.data ? res.data : { participants: [], count: null };
      setParticipants(data.participants ?? []);
      setParticipantCount(data.count ?? null);
      setParticipantsLoading(false);
    };
    probe();
    const interval = setInterval(probe, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [meeting.tabId]);

  return (
    <div style={{ background: T.surface, fontFamily: FONT_STACK, color: T.ink }}>
      <PopupHeader initial={initial} onSettings={onSettings} onAvatarClick={onAvatarClick} />

      <div style={{ padding: "20px 20px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: T.green, boxShadow: `0 0 8px ${T.green}60` }} />
          <span style={{ fontSize: 13, color: T.green, fontWeight: 700 }}>Meeting detected</span>
        </div>

        <h2 style={{ fontSize: 18, fontWeight: 800, margin: "0 0 6px", color: T.ink }}>
          {meeting.meetingTitle || "Untitled meeting"}
        </h2>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20 }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: platformMeta.color, background: platformMeta.bg, padding: "3px 10px", borderRadius: 6 }}>
            {platformMeta.label}
          </span>
          <span style={{ fontSize: 12, color: T.inkSecondary, fontWeight: 500 }}>Ready to record</span>
        </div>

        {error && (
          <div style={{ background: T.redLight, border: `1px solid ${T.red}40`, borderRadius: 8, padding: "8px 12px", fontSize: 12, color: T.red, marginBottom: 12 }}>
            {error}
          </div>
        )}

        {micDevices.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <select
              value={selectedMicDeviceId ?? ""}
              onChange={(e) => onMicChange?.(e.target.value)}
              disabled={loading || micLoading}
              style={{
                width: "100%",
                border: `1px solid ${T.border}`,
                borderRadius: 8,
                padding: "8px 10px",
                fontSize: 12,
                color: T.ink,
                background: T.surface,
                fontFamily: FONT_STACK,
              }}
            >
              {micDevices.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label}
                  {d.isDefault ? " (default)" : ""}
                </option>
              ))}
            </select>
          </div>
        )}
        {micError && <div style={{ fontSize: 11, color: T.red, marginBottom: 8 }}>{micError}</div>}

        <button
          onClick={onRecordBot}
          disabled={loading}
          style={{
            width: "100%",
            padding: "14px 0",
            borderRadius: 12,
            background: T.brand,
            color: "#fff",
            border: "none",
            fontSize: 15,
            fontWeight: 800,
            cursor: loading ? "default" : "pointer",
            fontFamily: FONT_STACK,
            boxShadow: `0 4px 16px ${T.brand}40`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            opacity: loading ? 0.7 : 1,
          }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="6" width="13" height="12" rx="2" />
            <path d="M15 10l5-3v10l-5-3" />
          </svg>
          {loading ? "Starting…" : "Start Recording"}
        </button>

        <div style={{ textAlign: "center", marginTop: 12 }}>
          <button
            onClick={() => setShowPrivate((v) => !v)}
            style={{
              background: "none",
              border: "none",
              color: T.inkSecondary,
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
              fontFamily: FONT_STACK,
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={T.inkSecondary} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            Record privately instead
          </button>
        </div>

        {showPrivate && (
          <div
            style={{
              marginTop: 10,
              background: T.surfaceSoft,
              border: `1px solid ${T.border}`,
              borderRadius: 10,
              padding: "12px 14px",
              animation: "fade-in 0.2s ease",
            }}
          >
            <div style={{ fontSize: 12, color: T.inkSecondary, lineHeight: 1.5, marginBottom: 10, fontWeight: 450 }}>
              Records audio directly from your browser tab. No bot joins the call — only you know it&apos;s recording.
            </div>
            <button
              onClick={onRecord}
              disabled={loading}
              style={{
                width: "100%",
                padding: "10px 0",
                borderRadius: 8,
                background: T.surface,
                color: T.brand,
                border: `1.5px solid ${T.brand}`,
                fontSize: 13,
                fontWeight: 700,
                cursor: loading ? "default" : "pointer",
                fontFamily: FONT_STACK,
                opacity: loading ? 0.7 : 1,
              }}
            >
              Record with Tab Audio
            </button>
          </div>
        )}

        {/* Participants scraped from the meeting page DOM. Always render
            the section so the user knows we're looking — pre-join Meet
            pages haven't rendered participant tiles yet, so the empty
            state explains why and the 3s poll above will pick them up. */}
        <div style={{ marginTop: 16, paddingTop: 12, borderTop: `1px solid ${T.border}` }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 8,
            }}
          >
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: T.inkSecondary,
                textTransform: "uppercase",
                letterSpacing: 0.8,
              }}
            >
              In this meeting
            </span>
            {participantCount != null && (
              <span style={{ fontSize: 11, fontWeight: 700, color: T.brand }}>
                {participantCount}
              </span>
            )}
          </div>
          {participants.length > 0 ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {participants.slice(0, 8).map((p) => (
                <span
                  key={p.name}
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: T.ink,
                    background: T.surfaceSoft,
                    border: `1px solid ${T.border}`,
                    padding: "4px 10px",
                    borderRadius: 6,
                  }}
                >
                  {p.name}
                  {p.isSelf && (
                    <span style={{ marginLeft: 4, color: T.brand, fontWeight: 700 }}>(you)</span>
                  )}
                </span>
              ))}
              {participants.length > 8 && (
                <span style={{ fontSize: 11, color: T.inkSecondary, alignSelf: "center" }}>
                  +{participants.length - 8} more
                </span>
              )}
            </div>
          ) : participantsLoading ? (
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: T.inkSecondary }}>
              <span style={{ width: 10, height: 10, borderRadius: "50%", border: `2px solid ${T.border}`, borderTopColor: T.brand, animation: "spin 0.8s linear infinite" }} />
              Scanning…
            </div>
          ) : (
            <div style={{ fontSize: 11, color: T.inkSecondary, lineHeight: 1.5 }}>
              Participant list will appear once you join the meeting.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
