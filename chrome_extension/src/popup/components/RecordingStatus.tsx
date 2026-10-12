import React, { useEffect, useState } from "react";
import type { AudioInputDevice, ExtensionState } from "../../shared/types";
import { sendMessage } from "../../shared/messaging";
import { WEB_BASE, NOTES_DEBOUNCE_MS } from "../../shared/constants";
import { T, FONT_STACK } from "../theme";
import { PopupHeader } from "./Header";

interface Participant {
  name: string;
  isSelf?: boolean;
}

interface Props {
  state: ExtensionState;
  loading?: boolean;
  onStop: () => void;
  onSettings: () => void;
  onAvatarClick: () => void;
}

/** Recording state — matches memoir-chrome-extension.jsx lines 242-306.
 *  Pulse-ring + timer-tick animations. Pause is decorative per plan §5
 *  (no PAUSE_RECORDING message yet) so the button is omitted and Stop
 *  takes the full row. */
export function RecordingStatus({ state, loading, onStop, onSettings, onAvatarClick }: Props) {
  const { meeting } = state;
  const isJoining = meeting.state === "joining" || meeting.state === "starting_to_record";
  const isFinalizing = meeting.state === "finalizing_upload";
  // Local optimistic flag — set true on Stop click. Without this, the
  // timer keeps ticking for up to ~1s until the popup polls the new
  // SW state (`finalizing_upload`) back. Locally freeze the moment the
  // user clicks Stop.
  const [stopRequested, setStopRequested] = useState(false);
  const [elapsed, setElapsed] = useState("0:00");
  const [notes, setNotes] = useState("");
  const [debounceTimer, setDebounceTimer] = useState<ReturnType<typeof setTimeout> | null>(null);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [participantCount, setParticipantCount] = useState<number | null>(null);
  const [micLabel, setMicLabel] = useState<string | null>(null);
  const initial = state.auth?.user?.name?.[0]?.toUpperCase() ?? "?";

  useEffect(() => {
    if (!meeting.recordingStartedAt || isJoining || isFinalizing || stopRequested) return;
    const update = () => {
      const diff = Math.floor((Date.now() - meeting.recordingStartedAt!) / 1000);
      const m = Math.floor(diff / 60);
      const s = diff % 60;
      setElapsed(`${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`);
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [meeting.recordingStartedAt, isJoining, isFinalizing, stopRequested]);

  useEffect(() => {
    sendMessage<{ content: string }>({ type: "GET_NOTES" }).then((res) => {
      if (res.success && res.data?.content) setNotes(res.data.content);
    });
  }, []);

  // Look up the active mic device label so we can show it read-only
  // during recording. Tab_audio recordings use the user-selected mic;
  // bot recordings ignore this (the bot's server-side capture is the
  // source of truth), so we only render the label for tab_audio.
  useEffect(() => {
    if (meeting.recordingMode !== "tab_audio") return;
    let cancelled = false;
    sendMessage<{ devices: AudioInputDevice[]; selectedDeviceId?: string }>({
      type: "GET_AUDIO_INPUT_DEVICES",
    }).then((res) => {
      if (cancelled || !res.success || !res.data) return;
      const { devices, selectedDeviceId } = res.data;
      const match = devices.find((d) => d.deviceId === selectedDeviceId);
      const fallbackDefault = devices.find((d) => d.isDefault);
      setMicLabel(match?.label ?? fallbackDefault?.label ?? "default microphone");
    });
    return () => {
      cancelled = true;
    };
  }, [meeting.recordingMode]);

  // Poll participants from the meeting tab DOM so the recording header
  // shows a live count. No auth-user fallback — only counts what the
  // scraper actually finds.
  useEffect(() => {
    if (isJoining || isFinalizing) return;
    let cancelled = false;
    const probe = async () => {
      const res = await sendMessage<{ participants: Participant[]; count: number | null }>({
        type: "GET_MEETING_PARTICIPANTS",
      });
      if (cancelled) return;
      const data = res.success && res.data ? res.data : { participants: [], count: null };
      setParticipants(data.participants ?? []);
      setParticipantCount(data.count ?? null);
    };
    probe();
    const id = setInterval(probe, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [isJoining, isFinalizing]);

  const handleNotesChange = (value: string) => {
    setNotes(value);
    if (debounceTimer) clearTimeout(debounceTimer);
    setDebounceTimer(setTimeout(() => sendMessage({ type: "SAVE_NOTES", content: value }), NOTES_DEBOUNCE_MS));
  };

  return (
    <div style={{ background: T.surface, fontFamily: FONT_STACK, color: T.ink }}>
      <PopupHeader initial={initial} recording onSettings={onSettings} onAvatarClick={onAvatarClick} />

      <div style={{ padding: "20px 20px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          {isJoining || isFinalizing ? (
            <>
              <span style={{ width: 14, height: 14, borderRadius: "50%", border: `2px solid ${T.border}`, borderTopColor: T.brand, animation: "spin 0.8s linear infinite" }} />
              <span style={{ fontSize: 13, color: T.ink, fontWeight: 700 }}>
                {isJoining ? "Joining meeting…" : "Uploading recording…"}
              </span>
            </>
          ) : (
            <>
              <div style={{ position: "relative", width: 12, height: 12 }}>
                <span style={{ position: "absolute", inset: 0, borderRadius: "50%", background: T.red, animation: "pulse-ring 1.5s ease-in-out infinite" }} />
                <span style={{ position: "absolute", inset: 2, borderRadius: "50%", background: T.red }} />
              </div>
              <span style={{ fontSize: 13, color: T.red, fontWeight: 700 }}>Recording</span>
              <span style={{ fontSize: 13, color: T.ink, fontWeight: 800, fontVariantNumeric: "tabular-nums", marginLeft: "auto", animation: "timer-tick 2s ease infinite" }}>
                {elapsed}
              </span>
            </>
          )}
        </div>

        <h2 style={{ fontSize: 18, fontWeight: 800, margin: "0 0 6px", color: T.ink }}>
          {meeting.meetingTitle || "Meeting"}
        </h2>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, fontSize: 12, color: T.inkSecondary, fontWeight: 500 }}>
          <span>{meeting.recordingMode === "tab_audio" ? "Tab audio (private)" : "Bot recording"}</span>
          {(participantCount ?? participants.length) > 0 && (
            <>
              <span>·</span>
              <span>
                {participantCount ?? participants.length}{" "}
                {(participantCount ?? participants.length) === 1 ? "participant" : "participants"}
              </span>
            </>
          )}
        </div>

        {/* Read-only mic-in-use label — only shown for tab_audio recordings.
            Pre-recording, the dropdown in MeetingDetected lets users pick;
            mid-recording we lock it (mic hot-swap is deferred). */}
        {meeting.recordingMode === "tab_audio" && micLabel && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 12, fontSize: 11, color: T.inkSecondary, fontWeight: 500 }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" />
            </svg>
            <span>Mic: {micLabel}</span>
          </div>
        )}

        {/* Participant chips — scraped from meeting DOM. Helps the user
            verify the live scrape result vs the count alone. */}
        {participants.length > 0 && (
          <div style={{ marginBottom: 14, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {participants.slice(0, 6).map((p) => (
              <span
                key={p.name}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: T.ink,
                  background: T.surfaceSoft,
                  border: `1px solid ${T.border}`,
                  padding: "3px 8px",
                  borderRadius: 6,
                }}
              >
                {p.name}
                {p.isSelf && (
                  <span style={{ marginLeft: 4, color: T.brand, fontWeight: 700 }}>(you)</span>
                )}
              </span>
            ))}
            {participants.length > 6 && (
              <span style={{ fontSize: 11, color: T.inkSecondary, alignSelf: "center" }}>
                +{participants.length - 6} more
              </span>
            )}
          </div>
        )}

        {!isJoining && !isFinalizing && (
          <div style={{ marginBottom: 14 }}>
            <label style={{ fontSize: 11, fontWeight: 700, color: T.inkSecondary, textTransform: "uppercase", letterSpacing: 0.8, display: "block", marginBottom: 6 }}>
              Quick notes
            </label>
            <textarea
              value={notes}
              onChange={(e) => handleNotesChange(e.target.value)}
              placeholder="Jot something while it's fresh…"
              style={{
                width: "100%",
                minHeight: 70,
                padding: "10px 12px",
                border: `1px solid ${T.border}`,
                borderRadius: 8,
                fontSize: 12,
                fontFamily: FONT_STACK,
                resize: "none",
                outline: "none",
                background: T.surface,
                color: T.ink,
                boxSizing: "border-box",
              }}
            />
          </div>
        )}

        <button
          onClick={() => {
            // Freeze the timer instantly — don't wait for the SW round-trip.
            setStopRequested(true);
            onStop();
          }}
          disabled={loading || isJoining || isFinalizing || stopRequested}
          style={{
            width: "100%",
            padding: "12px 0",
            borderRadius: 10,
            background: T.red,
            color: "#fff",
            border: "none",
            fontSize: 13,
            fontWeight: 700,
            cursor: loading || isJoining || isFinalizing ? "default" : "pointer",
            fontFamily: FONT_STACK,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
            boxShadow: `0 4px 12px ${T.red}30`,
            opacity: loading || isJoining || isFinalizing ? 0.6 : 1,
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="#fff" stroke="none">
            <rect x="4" y="4" width="16" height="16" rx="3" />
          </svg>
          {loading || stopRequested || isFinalizing ? "Uploading…" : isJoining ? "Joining…" : "Stop"}
        </button>

        {meeting.meetingId && (
          <div style={{ marginTop: 14, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <button
              onClick={() => chrome.tabs.create({ url: `${WEB_BASE}/meetings/${meeting.meetingId}` })}
              style={{ background: "none", border: "none", color: T.brand, fontSize: 11, fontWeight: 600, cursor: "pointer", fontFamily: FONT_STACK, display: "inline-flex", alignItems: "center", gap: 4 }}
            >
              Open in Memoir
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={T.brand} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                <polyline points="15 3 21 3 21 9" />
                <line x1="10" y1="14" x2="21" y2="3" />
              </svg>
            </button>
          </div>
        )}

        <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 6, justifyContent: "center" }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={T.green} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          <span style={{ fontSize: 11, color: T.inkSecondary, fontWeight: 500 }}>Summary will be ready after the meeting</span>
        </div>
      </div>
    </div>
  );
}
