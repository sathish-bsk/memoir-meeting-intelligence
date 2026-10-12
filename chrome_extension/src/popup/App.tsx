import React, { useEffect, useState } from "react";
import type { AudioInputDevice, ExtensionState, UploadAudioMode } from "../shared/types";
import { sendMessage } from "../shared/messaging";
import { LoginForm } from "./components/LoginForm";
import { DashboardSummary } from "./components/DashboardSummary";
import { MeetingDetected } from "./components/MeetingDetected";
import { RecordingStatus } from "./components/RecordingStatus";
import { SettingsPanel } from "./components/SettingsPanel";
import { T, FONT_STACK, POPUP_KEYFRAMES } from "./theme";

type PopupView = "loading" | "logged-out" | "idle" | "detected" | "recording";

/** When popup.html is opened in a standalone Chrome window
 *  (chrome.windows.create) with ?action=permission, render the mic
 *  permission flow instead of the normal popup UI. Detached windows
 *  survive Chrome's permission-prompt focus loss, so getUserMedia
 *  here actually lands the prompt cleanly — unlike the toolbar popup. */
function MicPermissionApp() {
  const [state, setState] = useState<"idle" | "requesting" | "granted" | "denied">("idle");
  const [error, setError] = useState<string | null>(null);

  const request = () => {
    setState("requesting");
    setError(null);
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        stream.getTracks().forEach((t) => t.stop());
        setState("granted");
        setTimeout(() => window.close(), 700);
      })
      .catch((e) => {
        setState("denied");
        setError((e as Error)?.message ?? "Permission denied");
      });
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#F4F4F7", fontFamily: FONT_STACK, padding: 24, boxSizing: "border-box" }}>
      <div style={{ width: "100%", maxWidth: 360, background: "#FFF", border: "1px solid #E4E4E7", borderRadius: 14, padding: 28, textAlign: "center" }}>
        <div style={{ width: 56, height: 56, borderRadius: "50%", background: "#5C5CE01A", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 18px" }}>
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#5C5CE0" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
        </div>
        <div style={{ fontSize: 16, fontWeight: 800, color: "#131316", marginBottom: 8 }}>
          {state === "granted" ? "Microphone access granted" : "Allow microphone access"}
        </div>
        <div style={{ fontSize: 13, color: "#5C5C66", lineHeight: 1.55, marginBottom: 18 }}>
          {state === "granted"
            ? "You can close this window."
            : "Click the button below. Chrome will ask for permission — choose Allow."}
        </div>
        {state !== "granted" && (
          <button
            onClick={request}
            disabled={state === "requesting"}
            style={{ width: "100%", padding: "11px 0", borderRadius: 10, background: "#5C5CE0", color: "#FFF", border: 0, fontSize: 14, fontWeight: 700, cursor: state === "requesting" ? "default" : "pointer", opacity: state === "requesting" ? 0.6 : 1, fontFamily: FONT_STACK }}
          >
            {state === "requesting" ? "Waiting for Chrome's prompt…" : "Allow microphone access"}
          </button>
        )}
        {error && (
          <div style={{ marginTop: 12, fontSize: 12, color: "#C0392B", lineHeight: 1.5 }}>
            {error}. You can also click the microphone icon in this window&apos;s address bar to grant access manually.
          </div>
        )}
      </div>
    </div>
  );
}

export function App() {
  // Standalone permission-window mode — bypasses the normal popup UI.
  if (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("action") === "permission") {
    return <MicPermissionApp />;
  }

  // Inject popup animation keyframes once on mount (popup CSS can't @apply
  // Tailwind; head injection avoids prop-drilling a <style> element through
  // every sub-component).
  useEffect(() => {
    const id = "popup-keyframes";
    if (document.getElementById(id)) return;
    const tag = document.createElement("style");
    tag.id = id;
    tag.textContent = POPUP_KEYFRAMES;
    document.head.appendChild(tag);
  }, []);

  const [view, setView] = useState<PopupView>("loading");
  const [state, setState] = useState<ExtensionState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [micDevices, setMicDevices] = useState<AudioInputDevice[]>([]);
  const [selectedMicDeviceId, setSelectedMicDeviceId] = useState<string | undefined>(undefined);
  const [micLoading, setMicLoading] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const [selectedUploadMode, setSelectedUploadMode] = useState<UploadAudioMode>("separate");
  // Settings overlay: gear → "devices" (mic + stream mode);
  // avatar → "account" (profile + sign out).
  const [settingsView, setSettingsView] = useState<"devices" | "account" | null>(null);

  const fetchState = async () => {
    const res = await sendMessage<ExtensionState>({ type: "GET_STATE" });
    if (res.success && res.data) {
      setState(res.data);
      if (!res.data.auth) {
        setView("logged-out");
      } else {
        const ms = res.data.meeting.state;
        if (ms === "recording" || ms === "joining" || ms === "finalizing_upload") {
          setView("recording");
        } else if (ms === "detected") {
          setView("detected");
        } else {
          setView("idle");
        }
      }
    } else {
      setView("logged-out");
    }
  };

  useEffect(() => {
    fetchState();
    // Poll for state changes while popup is open. 1s keeps the popup in
    // sync with /record stops within a beat (cross-surface lag was up to
    // 3s before this drop).
    const interval = setInterval(fetchState, 1000);
    return () => clearInterval(interval);
  }, []);

  const fetchMicDevices = async () => {
    setMicLoading(true);
    setMicError(null);
    const res = await sendMessage<{ devices: AudioInputDevice[]; selectedDeviceId?: string }>({
      type: "GET_AUDIO_INPUT_DEVICES",
    });
    setMicLoading(false);
    if (!res.success || !res.data) {
      setMicDevices([]);
      setSelectedMicDeviceId(undefined);
      setMicError(res.error ?? "Failed to load microphones");
      return;
    }

    setMicDevices(res.data.devices);
    const fallback = res.data.devices[0]?.deviceId;
    setSelectedMicDeviceId(res.data.selectedDeviceId ?? fallback);
  };

  const fetchUploadMode = async () => {
    const res = await sendMessage<{ mode: UploadAudioMode }>({ type: "GET_RECORDING_MODE_PREF" });
    if (res.success && res.data?.mode) {
      setSelectedUploadMode(res.data.mode);
    } else {
      setSelectedUploadMode("separate");
    }
  };

  useEffect(() => {
    if (view === "detected") {
      fetchMicDevices();
      fetchUploadMode();
    }
  }, [view, state?.auth?.activeTeamId]);

  const handleLoginSuccess = () => {
    setError(null);
    fetchState();
  };

  const handleLoginError = (msg: string) => {
    setError(msg);
  };

  const openDeviceSettings = () => {
    fetchMicDevices();
    fetchUploadMode();
    setSettingsView("devices");
  };

  const openAccount = () => {
    setSettingsView("account");
  };

  const handleLogout = async () => {
    await sendMessage({ type: "LOGOUT" });
    setSettingsView(null);
    fetchState();
  };

  // Settings overlay — opens from gear (devices) or avatar (account)
  if (settingsView && state?.auth) {
    return (
      <div style={styles.container}>
        <SettingsPanel
          view={settingsView}
          user={state.auth.user}
          teamName={state.auth.activeTeamName}
          micDevices={micDevices}
          selectedMicDeviceId={selectedMicDeviceId}
          selectedUploadMode={selectedUploadMode}
          micLoading={micLoading}
          micError={micError}
          onMicChange={async (deviceId) => {
            setSelectedMicDeviceId(deviceId);
            const res = await sendMessage({ type: "SET_SELECTED_MIC_DEVICE", deviceId });
            if (!res.success) setMicError(res.error ?? "Failed to save microphone");
            else setMicError(null);
          }}
          onUploadModeChange={async (mode) => {
            setSelectedUploadMode(mode);
            const res = await sendMessage({ type: "SET_RECORDING_MODE_PREF", mode });
            if (!res.success) setError(res.error ?? "Failed to save recording mode");
          }}
          onLogout={handleLogout}
          onClose={() => setSettingsView(null)}
          onPermissionGranted={fetchMicDevices}
        />
      </div>
    );
  }

  if (view === "loading") {
    return (
      <div style={styles.container}>
        <div style={styles.center}>
          <div style={styles.logo}>m</div>
          <p style={styles.loadingText}>Loading...</p>
        </div>
      </div>
    );
  }

  if (view === "logged-out") {
    return (
      <div style={styles.container}>
        <LoginForm
          onSuccess={handleLoginSuccess}
          onError={handleLoginError}
          error={error}
        />
      </div>
    );
  }

  if (view === "detected" && state) {
    return (
      <div style={styles.container}>
        <MeetingDetected
          state={state}
          error={error}
          loading={actionLoading}
          micDevices={micDevices}
          selectedMicDeviceId={selectedMicDeviceId}
          selectedUploadMode={selectedUploadMode}
          micLoading={micLoading}
          micError={micError}
          onSettings={openDeviceSettings}
          onAvatarClick={openAccount}
          onMicChange={async (deviceId) => {
            setSelectedMicDeviceId(deviceId);
            const res = await sendMessage({ type: "SET_SELECTED_MIC_DEVICE", deviceId });
            if (!res.success) {
              setMicError(res.error ?? "Failed to save microphone");
            } else {
              setMicError(null);
            }
          }}
          onUploadModeChange={async (mode) => {
            setSelectedUploadMode(mode);
            const res = await sendMessage({ type: "SET_RECORDING_MODE_PREF", mode });
            if (!res.success) {
              setError(res.error ?? "Failed to save recording mode");
            }
          }}
          onRecord={async () => {
            setError(null);
            setActionLoading(true);
            const res = await sendMessage({
              type: "START_RECORDING_TAB_AUDIO",
              micDeviceId: selectedMicDeviceId,
              uploadMode: selectedUploadMode,
            });
            setActionLoading(false);
            if (!res.success) {
              setError(res.error ?? "Failed to start recording");
            }
            fetchState();
          }}
          onRecordBot={async () => {
            setError(null);
            setActionLoading(true);
            const res = await sendMessage({ type: "START_RECORDING_BOT" });
            setActionLoading(false);
            if (!res.success) {
              setError(res.error ?? "Failed to start recording");
            }
            fetchState();
          }}
        />
      </div>
    );
  }

  if (view === "recording" && state) {
    return (
      <div style={styles.container}>
        <RecordingStatus
          state={state}
          loading={actionLoading}
          onSettings={openDeviceSettings}
          onAvatarClick={openAccount}
          onStop={async () => {
            setActionLoading(true);
            const res = await sendMessage({ type: "STOP_RECORDING" });
            setActionLoading(false);
            if (!res.success) setError(res.error ?? "Failed to stop recording");
            fetchState();
          }}
        />
      </div>
    );
  }

  // Idle
  return (
    <div style={styles.container}>
      <DashboardSummary
        state={state}
        onSettings={openDeviceSettings}
        onAvatarClick={openAccount}
        onAdHocStarted={fetchState}
      />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    width: 340,
    display: "flex",
    flexDirection: "column",
    fontFamily: FONT_STACK,
    background: T.surface,
    color: T.ink,
    animation: "fade-in 0.3s ease",
  },
  center: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  logo: {
    width: 48,
    height: 48,
    borderRadius: 14,
    background: T.brand,
    color: "white",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 26,
    fontWeight: 400,
    fontFamily: "'DM Serif Display', Georgia, serif",
    marginBottom: 16,
  },
  loadingText: {
    color: T.inkSecondary,
    fontSize: 13,
    fontWeight: 500,
  },
};
