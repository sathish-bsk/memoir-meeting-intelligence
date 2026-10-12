"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { notifyMeetingsChanged } from "@/lib/utils";
import { ChunkUploader, ChunkUploaderState } from "./chunk-uploader";
import {
  CaptureError,
  requestMicOnlyCapture,
  stopMixedCapture,
} from "./path-a-capture";
import { MicPicker, loadStoredMicDeviceId } from "./mic-picker";
import { ExtensionHandoffModal } from "./extension-handoff-modal";
import {
  isExtensionInstalled,
  listMeetingTabs,
  startHandoffRecording,
  stopHandoffRecording,
  startAdHocRecording,
  getExtensionMicDevice,
  setExtensionMicDevice,
  getExtensionRecordingState,
  ExtensionBridgeError,
  type MeetingTabInfo,
} from "./extension-bridge";

type RecorderState =
  | "idle"
  | "preparing"
  | "tab_picker"
  | "recording"
  | "finalizing"
  | "error";

type RecordingMode = "extension_handoff" | "mic_only";

interface WebRecorderProps {
  teamId: string;
}

function formatTimer(elapsedSec: number): string {
  const h = Math.floor(elapsedSec / 3600);
  const m = Math.floor((elapsedSec % 3600) / 60);
  const s = Math.floor(elapsedSec % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// Cross-surface sync — /record needs to flip quickly when the extension
// popup stops the recording (and vice versa). 1s is short enough that
// stop-from-popup is reflected on /record within a beat, without
// hammering the API too hard.
const HANDOFF_POLL_INTERVAL_MS = 1000;

export function WebRecorder({ teamId }: WebRecorderProps) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const language: string | null = null;
  const [state, setState] = useState<RecorderState>("idle");
  const [recordingMode, setRecordingMode] = useState<RecordingMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const [extensionInstalled, setExtensionInstalled] = useState<boolean>(false);
  const [selectedMicDeviceId, setSelectedMicDeviceId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState<boolean>(false);
  const [tabPickerTabs, setTabPickerTabs] = useState<MeetingTabInfo[] | null>(null);
  const [handoffTabTitle, setHandoffTabTitle] = useState<string | null>(null);

  // Recording-time state
  const meetingIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const uploaderRef = useRef<ChunkUploader | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const startTimeRef = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const accessToken = useAuthStore((s) => s.accessToken);

  // Detect extension presence + load mic device on mount. Mic device:
  // prefer extension's chrome.storage value (the cross-surface source of
  // truth when extension is installed), fall back to localStorage.
  useEffect(() => {
    const installed = isExtensionInstalled();
    setExtensionInstalled(installed);

    if (installed) {
      getExtensionMicDevice()
        .then((extDeviceId) => {
          if (extDeviceId) {
            setSelectedMicDeviceId(extDeviceId);
          } else {
            // Extension has no preference yet — try localStorage and seed
            // the extension with it so both surfaces agree.
            const local = loadStoredMicDeviceId();
            setSelectedMicDeviceId(local);
            if (local) {
              setExtensionMicDevice(local).catch(() => {});
            }
          }
        })
        .catch(() => {
          setSelectedMicDeviceId(loadStoredMicDeviceId());
        });
    } else {
      setSelectedMicDeviceId(loadStoredMicDeviceId());
    }
  }, []);

  // MicPicker onChange — write to localStorage (built into the picker)
  // AND mirror to the extension's storage if installed, so the popup
  // sees the same mic on next open and the offscreen document uses it
  // when starting any recording.
  const handleMicDeviceChange = useCallback(
    (deviceId: string | null) => {
      setSelectedMicDeviceId(deviceId);
      if (extensionInstalled) {
        setExtensionMicDevice(deviceId).catch((e) => {
          console.warn("[web-recorder] failed to sync mic to extension:", e);
        });
      }
    },
    [extensionInstalled],
  );

  // Cleanup pollers/timers on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const stopLocalTimers = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  /** Cancel current mic-only recording + cleanup. */
  const cancelMicOnlyRecording = useCallback(async (reason: string) => {
    stopLocalTimers();
    if (streamRef.current) {
      stopMixedCapture(streamRef.current);
      streamRef.current = null;
    }
    if (uploaderRef.current) {
      uploaderRef.current.cancel();
      uploaderRef.current = null;
    }
    const mid = meetingIdRef.current;
    if (mid) {
      try {
        await api.cancelWebRecorderMeeting(mid);
      } catch (e) {
        console.warn("[web-recorder] cancel API call failed:", e);
      }
    }
    meetingIdRef.current = null;
    sessionIdRef.current = null;
    setError(reason);
    setState("error");
    setElapsed(0);
    setRecordingMode(null);
  }, [stopLocalTimers]);

  const startElapsedTimer = useCallback(() => {
    startTimeRef.current = Date.now();
    timerRef.current = setInterval(() => {
      setElapsed((Date.now() - startTimeRef.current) / 1000);
    }, 250);
  }, []);

  // ─── Mic-only flow (local — no extension) ─────────────────────────
  // Direct getUserMedia + page-side chunk uploader. Used only when the
  // extension is NOT installed (modal "Mic only" CTA) or when the
  // extension-handoff bridge fails. When the extension IS installed,
  // ad-hoc routes through it instead — see startAdHocViaExtension below.
  const startLocalMicOnly = useCallback(async () => {
    setError(null);
    setState("preparing");
    setRecordingMode("mic_only");

    // 1. Create meeting + session
    let meetingId: string;
    let sessionId: string;
    try {
      const meeting = await api.createWebRecorderMeeting({
        title: title.trim() || undefined,
        team_id: teamId,
        language,
      });
      meetingId = meeting.id;
      meetingIdRef.current = meetingId;
      const session = await api.createTabAudioSession(meetingId);
      sessionId = session.session_id;
      sessionIdRef.current = sessionId;
    } catch (e) {
      setError(`Could not create meeting: ${(e as Error).message}`);
      setState("error");
      setRecordingMode(null);
      return;
    }

    // 2. Get mic stream — no screen-share picker
    let stream: MediaStream;
    try {
      stream = await requestMicOnlyCapture(selectedMicDeviceId ?? undefined);
      streamRef.current = stream;
    } catch (e) {
      const cap = e as CaptureError;
      const friendly =
        cap.kind === "permission_denied"
          ? "Microphone permission denied. Allow microphone access in Chrome site settings and try again."
          : cap.kind === "not_supported"
          ? "This browser doesn't support microphone capture. Try Chrome or Edge."
          : `Microphone capture failed: ${cap.message}`;
      await cancelMicOnlyRecording(friendly);
      return;
    }

    if (!accessToken) {
      await cancelMicOnlyRecording("Not authenticated. Please sign in again.");
      return;
    }

    // 3. Start chunk uploader
    const uploader = new ChunkUploader({
      meetingId,
      sessionId,
      getAccessToken: () => api.getAccessToken(),
      onStateChange: (s: ChunkUploaderState) => {
        if (s.kind === "error") {
          setError(s.message);
          setState("error");
        }
      },
    });
    uploaderRef.current = uploader;
    await uploader.start(stream, "mixed");

    startElapsedTimer();
    setState("recording");
    notifyMeetingsChanged();
  }, [
    title,
    teamId,
    language,
    accessToken,
    selectedMicDeviceId,
    cancelMicOnlyRecording,
    startElapsedTimer,
  ]);

  // ─── Extension handoff flow ───────────────────────────────────────
  const beginHandoffStatusPolling = useCallback(
    (meetingId: string) => {
      pollRef.current = setInterval(async () => {
        // 1. Cross-surface stop detection. The extension's meetingState
        //    transitions out of "recording" the instant the popup Stop
        //    button is clicked — well before the backend status flip
        //    (which only happens after the offscreen finishes uploading).
        //    Freeze the local timer + flip UI to "finalizing" so /record
        //    mirrors the popup immediately.
        try {
          const extState = await getExtensionRecordingState();
          if (extState.state !== "recording" && extState.state !== "joining" && extState.state !== "starting_to_record") {
            if (timerRef.current) {
              clearInterval(timerRef.current);
              timerRef.current = null;
            }
            setState((prev) => (prev === "recording" ? "finalizing" : prev));
          }
        } catch {
          // Bridge transient errors — fall through to backend poll.
        }

        // 2. Backend status flip → redirect to the meeting detail page.
        try {
          const meeting = await api.getMeeting(meetingId);
          if (
            meeting.status === "waiting_for_transcription" ||
            meeting.status === "transcribing" ||
            meeting.status === "completed" ||
            meeting.status === "failed"
          ) {
            stopLocalTimers();
            router.push(`/meetings/${meetingId}`);
          }
        } catch (e) {
          console.warn("[web-recorder] poll error:", e);
        }
      }, HANDOFF_POLL_INTERVAL_MS);
    },
    [router, stopLocalTimers],
  );

  const startHandoff = useCallback(
    async (tab: MeetingTabInfo) => {
      setError(null);
      setState("preparing");
      setRecordingMode("extension_handoff");
      setHandoffTabTitle(tab.title);

      try {
        const { meetingId } = await startHandoffRecording({
          tabId: tab.tabId,
          title: title.trim() || tab.title,
          language,
        });
        meetingIdRef.current = meetingId;
        startElapsedTimer();
        setState("recording");
        notifyMeetingsChanged();
        beginHandoffStatusPolling(meetingId);
      } catch (e) {
        const msg =
          e instanceof ExtensionBridgeError
            ? `Extension communication failed: ${e.message}`
            : `Failed to start recording via extension: ${(e as Error).message}`;
        setError(msg);
        setState("error");
        setRecordingMode(null);
        setHandoffTabTitle(null);
      }
    },
    [title, language, startElapsedTimer, beginHandoffStatusPolling],
  );

  // ─── Ad-hoc via extension (mic-only routed through extension) ────
  // Same surface as the popup's "Start recording — Record an ad-hoc
  // conversation" CTA. Extension's offscreen document records mic,
  // chunks via the same upload pipeline as handoff. Page polls status.
  const startAdHocViaExtension = useCallback(async () => {
    setError(null);
    setState("preparing");
    setRecordingMode("extension_handoff");
    setHandoffTabTitle(null);

    try {
      // Sync the page's selected mic to the extension before starting,
      // so the offscreen document uses the user's choice.
      if (selectedMicDeviceId) {
        try {
          await setExtensionMicDevice(selectedMicDeviceId);
        } catch {
          // Non-fatal — extension uses its own stored default.
        }
      }

      const { meetingId } = await startAdHocRecording({
        title: title.trim() || undefined,
      });
      meetingIdRef.current = meetingId;
      startElapsedTimer();
      setState("recording");
      notifyMeetingsChanged();
      beginHandoffStatusPolling(meetingId);
    } catch (e) {
      const msg =
        e instanceof ExtensionBridgeError
          ? `Extension communication failed: ${e.message}`
          : `Failed to start recording: ${(e as Error).message}`;
      setError(msg);
      setState("error");
      setRecordingMode(null);
    }
  }, [title, selectedMicDeviceId, startElapsedTimer, beginHandoffStatusPolling]);

  // ─── Top-level Start handler ──────────────────────────────────────
  const handleStart = useCallback(async () => {
    setError(null);

    // Re-check extension at click time. The content script that sets
    // `data-memoir-ext` runs at document_idle and may not have run yet
    // when our initial useEffect fired.
    const extPresent = isExtensionInstalled();
    if (extPresent !== extensionInstalled) setExtensionInstalled(extPresent);

    // Branch 1: no extension → show modal
    if (!extPresent) {
      setModalOpen(true);
      return;
    }

    // Branch 2: extension installed → ask for meeting tabs
    setState("preparing");
    let tabs: MeetingTabInfo[];
    try {
      tabs = await listMeetingTabs();
    } catch (e) {
      // Bridge timed out or errored → fall back to ad-hoc via extension if
      // possible, else local mic-only.
      console.warn("[web-recorder] listMeetingTabs failed:", e);
      await startAdHocViaExtension();
      return;
    }

    if (tabs.length === 0) {
      // No meeting tab → ad-hoc via extension (extension is the recorder
      // for all cases when installed).
      await startAdHocViaExtension();
      return;
    }
    if (tabs.length === 1) {
      await startHandoff(tabs[0]);
      return;
    }
    // 2+ tabs → inline picker
    setTabPickerTabs(tabs);
    setState("tab_picker");
  }, [extensionInstalled, startAdHocViaExtension, startHandoff]);

  // ─── Stop handler ─────────────────────────────────────────────────
  const handleStop = useCallback(async () => {
    if (recordingMode === "extension_handoff") {
      const meetingId = meetingIdRef.current;
      if (!meetingId) {
        setError("Recorder state lost. Please refresh and try again.");
        setState("error");
        return;
      }
      // Freeze the local elapsed timer immediately — the recording is
      // logically over from the user's perspective even though the
      // extension is still uploading the final chunks. Keep the poller
      // running so we redirect once the backend status flips.
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      setState("finalizing");
      try {
        await stopHandoffRecording(meetingId);
      } catch (e) {
        setError(`Stop failed: ${(e as Error).message}`);
        setState("error");
      }
      return;
    }

    // Mic-only path (legacy web-recorder finalize flow)
    setState("finalizing");
    stopLocalTimers();
    const uploader = uploaderRef.current;
    const stream = streamRef.current;
    const meetingId = meetingIdRef.current;
    const sessionId = sessionIdRef.current;
    if (!uploader || !stream || !meetingId || !sessionId) {
      setError("Recorder state lost. Please refresh and try again.");
      setState("error");
      return;
    }
    try {
      await uploader.stop("mixed");
      stopMixedCapture(stream);
      streamRef.current = null;
      await api.completeWebRecorderSession(meetingId, sessionId, {
        speaker_events: [],
        participants: [],
      });
      router.push(`/meetings/${meetingId}`);
    } catch (e) {
      setError(`Finalize failed: ${(e as Error).message}`);
      setState("error");
    }
  }, [recordingMode, router, stopLocalTimers]);

  const handleRetryAfterError = useCallback(() => {
    setError(null);
    setState("idle");
    setElapsed(0);
    setRecordingMode(null);
    setHandoffTabTitle(null);
    setTabPickerTabs(null);
    meetingIdRef.current = null;
    sessionIdRef.current = null;
  }, []);

  // Modal callbacks
  const onModalClose = useCallback(() => {
    setModalOpen(false);
    setState("idle");
  }, []);
  const onModalConfirmMicOnly = useCallback(async () => {
    setModalOpen(false);
    await startLocalMicOnly();
  }, [startLocalMicOnly]);

  // Tab picker callbacks
  const onTabPicked = useCallback(
    async (t: MeetingTabInfo) => {
      setTabPickerTabs(null);
      await startHandoff(t);
    },
    [startHandoff],
  );
  const onTabPickerCancel = useCallback(() => {
    setTabPickerTabs(null);
    setState("idle");
  }, []);

  const isRecording = state === "recording";
  const buttonDisabled = state === "preparing" || state === "finalizing";

  return (
    <div className="max-w-md mx-auto py-12 px-4">
      <h1 className="text-center text-xl font-medium text-memoir-text mb-6">
        New Recording
      </h1>

      <div className="flex justify-center mb-6">
        <div className="w-12 h-12 rounded-lg bg-memoir-bg border border-memoir-border flex items-center justify-center">
          <svg
            className="w-6 h-6 text-memoir-text-secondary"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"
            />
          </svg>
        </div>
      </div>

      <div className="flex items-center gap-2 mb-4">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Recording Title"
          disabled={state !== "idle"}
          className="flex-1 px-4 py-2.5 border border-memoir-border rounded-button text-sm focus:outline-none focus:ring-2 focus:ring-memoir-indigo/30 focus:border-memoir-indigo disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <ReadOnlyLanguageIndicator />
      </div>

      <div className="mb-6">
        <MicPicker
          selectedDeviceId={selectedMicDeviceId}
          onChange={handleMicDeviceChange}
          disabled={state !== "idle"}
        />
      </div>

      <div className="text-center mb-2">
        <div className="text-5xl font-light text-memoir-text tabular-nums">
          {formatTimer(elapsed)}
        </div>
      </div>

      <div className="text-center mb-10">
        <span className="text-sm text-memoir-text-secondary">
          {isRecording && (
            <span className="inline-block w-2 h-2 rounded-full bg-red-500 mr-2 animate-pulse" />
          )}
          {state === "preparing" && "Preparing…"}
          {state === "recording" && recordingMode === "extension_handoff" &&
            `Recording via extension${handoffTabTitle ? ` — ${handoffTabTitle}` : ""}`}
          {state === "recording" && recordingMode === "mic_only" && "Recording (microphone only)"}
          {state === "finalizing" && "Saving…"}
          {state === "error" && "Stopped"}
          {state === "idle" && " "}
        </span>
      </div>

      {error && state === "error" && (
        <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Tab picker (shown when extension found 2+ meeting tabs) */}
      {state === "tab_picker" && tabPickerTabs && (
        <div className="mb-6">
          <p className="text-sm text-memoir-text-secondary mb-3">
            Multiple meeting tabs found. Choose one to record:
          </p>
          <ul className="space-y-2">
            {tabPickerTabs.map((t) => (
              <li key={t.tabId}>
                <button
                  type="button"
                  onClick={() => void onTabPicked(t)}
                  className="w-full text-left px-3 py-2.5 border border-memoir-border rounded-button hover:bg-memoir-bg flex items-center gap-3"
                >
                  <PlatformIcon platform={t.platform} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-memoir-text truncate">
                      {t.title || hostnameFromUrl(t.url)}
                    </div>
                    <div className="text-xs text-memoir-text-muted truncate">
                      {hostnameFromUrl(t.url)}
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={onTabPickerCancel}
            className="mt-3 w-full text-xs text-memoir-text-muted hover:text-memoir-text-secondary"
          >
            Cancel
          </button>
        </div>
      )}

      {state !== "tab_picker" && (
        <button
          type="button"
          onClick={
            state === "error"
              ? handleRetryAfterError
              : isRecording
              ? handleStop
              : handleStart
          }
          disabled={buttonDisabled}
          className={`w-full py-3 rounded-button font-semibold text-white text-base transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
            isRecording
              ? "bg-red-600 hover:bg-red-700"
              : state === "error"
              ? "bg-memoir-text hover:bg-memoir-text-secondary"
              : "bg-memoir-indigo hover:bg-memoir-indigo-dark"
          }`}
        >
          <span className="inline-flex items-center justify-center gap-2">
            {!isRecording && (
              <svg
                className="w-5 h-5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"
                />
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"
                />
              </svg>
            )}
            {state === "idle" && "Start Recording"}
            {state === "preparing" && "Preparing…"}
            {state === "recording" && "Stop Recording"}
            {state === "finalizing" && "Saving…"}
            {state === "error" && "Try Again"}
          </span>
        </button>
      )}

      <p className="mt-4 text-center text-xs text-memoir-text-muted">
        Make sure to get participants consent when recording.
      </p>

      <ExtensionHandoffModal
        open={modalOpen}
        onClose={onModalClose}
        onConfirmMicOnly={onModalConfirmMicOnly}
      />
    </div>
  );
}

function ReadOnlyLanguageIndicator() {
  return (
    <span
      aria-label="Recording language: English (Global) (read-only)"
      className="inline-flex items-center gap-1.5 text-sm text-memoir-text-secondary cursor-default select-none"
    >
      <svg
        className="w-4 h-4"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={2}
      >
        <circle cx="12" cy="12" r="9" />
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"
        />
      </svg>
      English (Global)
    </span>
  );
}

function PlatformIcon({ platform }: { platform: MeetingTabInfo["platform"] }) {
  const initial =
    platform === "google_meet" ? "M" : platform === "zoom" ? "Z" : "T";
  const bg =
    platform === "google_meet"
      ? "bg-green-100 text-green-700"
      : platform === "zoom"
      ? "bg-blue-100 text-blue-700"
      : "bg-purple-100 text-purple-700";
  return (
    <span
      className={`shrink-0 inline-flex items-center justify-center w-8 h-8 rounded text-xs font-semibold ${bg}`}
      aria-hidden
    >
      {initial}
    </span>
  );
}

function hostnameFromUrl(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}
