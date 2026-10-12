import React, { useEffect, useState } from "react";
import type { AudioInputDevice, UploadAudioMode } from "../../shared/types";
import { T, FONT_STACK } from "../theme";

interface Props {
  /** Which slice of settings to render. "devices" = mic + stream mode.
   *  "account" = profile + sign out. Triggered by the gear and the
   *  avatar in the header respectively. */
  view: "devices" | "account";
  user?: { name: string; email: string };
  teamName?: string;
  micDevices: AudioInputDevice[];
  selectedMicDeviceId?: string;
  selectedUploadMode: UploadAudioMode;
  micLoading: boolean;
  micError: string | null;
  onMicChange: (id: string) => void;
  onUploadModeChange: (mode: UploadAudioMode) => void;
  onLogout: () => void;
  onClose: () => void;
  /** Called after the user grants mic permission so the parent can
   *  re-fetch the device list (labels become available post-grant). */
  onPermissionGranted?: () => void;
}

type MicPermState = "unknown" | "prompt" | "granted" | "denied";

/** Settings panel — opened by clicking the gear (view="devices") or the
 *  avatar (view="account") in the popup header. The two slices stay
 *  visually separate so users don't see account info when they're
 *  reaching for the mic selector and vice versa. */
export function SettingsPanel({
  view,
  user,
  teamName,
  micDevices,
  selectedMicDeviceId,
  selectedUploadMode,
  micLoading,
  micError,
  onMicChange,
  onUploadModeChange,
  onLogout,
  onClose,
  onPermissionGranted,
}: Props) {
  const heading = view === "devices" ? "Devices & streams" : "Account";

  // Track mic permission state so the Devices view can decide between
  // "show grant prompt CTA" vs "show device list". We rely on
  // navigator.permissions.query when supported; the actual prompt
  // appears on click of the CTA button, which IS a user gesture so
  // Chrome reliably shows its native prompt (the same one /record
  // page triggers).
  const [micPerm, setMicPerm] = useState<MicPermState>("unknown");
  const [granting, setGranting] = useState(false);
  const [grantError, setGrantError] = useState<string | null>(null);

  useEffect(() => {
    if (view !== "devices") return;
    let cancelled = false;
    (async () => {
      try {
        const res = await navigator.permissions.query({
          name: "microphone" as PermissionName,
        });
        if (cancelled) return;
        setMicPerm(res.state as MicPermState);
        // Reflect runtime changes (e.g., user revoked from chrome settings).
        res.onchange = () => setMicPerm(res.state as MicPermState);
      } catch {
        // Some Chromium builds don't expose "microphone" via permissions
        // API. Fall back to inferring from device labels: enumerateDevices
        // returns labeled entries only when permission is granted.
        try {
          const devices = await navigator.mediaDevices.enumerateDevices();
          const audio = devices.filter((d) => d.kind === "audioinput");
          if (cancelled) return;
          setMicPerm(audio.some((d) => d.label) ? "granted" : "prompt");
        } catch {
          if (!cancelled) setMicPerm("prompt");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [view]);

  const requestPermission = () => {
    // Open the existing popup.html in a standalone Chrome window
    // (chrome.windows.create with type: 'popup'). That window — unlike
    // the toolbar popup — survives focus loss to Chrome's permission
    // prompt, so getUserMedia called inside it lands the prompt
    // cleanly. The window detects the ?action=permission query flag
    // and renders a minimal mic-permission view, then closes itself
    // on grant. After the window closes, refresh device labels here.
    setGranting(true);
    setGrantError(null);
    const url = chrome.runtime.getURL("popup.html?action=permission");
    chrome.windows.create(
      { url, type: "popup", width: 400, height: 360, focused: true },
      (win) => {
        if (!win?.id) {
          setGrantError("Could not open permission window");
          setGranting(false);
          return;
        }
        // Watch for the window to close, then re-query permission.
        const onRemoved = (closedId: number) => {
          if (closedId !== win.id) return;
          chrome.windows.onRemoved.removeListener(onRemoved);
          (async () => {
            try {
              const res = await navigator.permissions.query({
                name: "microphone" as PermissionName,
              });
              setMicPerm(res.state as MicPermState);
              if (res.state === "granted") onPermissionGranted?.();
              else if (res.state === "denied") setGrantError("Permission denied");
            } catch {
              // Fallback — assume granted if labels are now populated.
              try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                const granted = devices.some((d) => d.kind === "audioinput" && d.label);
                setMicPerm(granted ? "granted" : "denied");
                if (granted) onPermissionGranted?.();
              } catch {
                /* ignore */
              }
            } finally {
              setGranting(false);
            }
          })();
        };
        chrome.windows.onRemoved.addListener(onRemoved);
      }
    );
  };

  return (
    <div style={{ background: T.surface, fontFamily: FONT_STACK, color: T.ink, animation: "fade-in 0.2s ease" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "16px 20px",
          borderBottom: `1px solid ${T.border}`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            onClick={onClose}
            aria-label="Back"
            style={{ background: "none", border: "none", cursor: "pointer", padding: 2, display: "flex" }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={T.ink} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <span style={{ fontSize: 15, fontWeight: 800 }}>{heading}</span>
        </div>
      </div>

      {view === "account" && (
        <div style={{ padding: "16px 20px 20px" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              background: T.surfaceSoft,
              border: `1px solid ${T.border}`,
              borderRadius: 12,
              padding: "16px",
              marginBottom: 16,
            }}
          >
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: "50%",
                background: `linear-gradient(135deg, ${T.brand}, ${T.brandDark})`,
                color: "#fff",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontWeight: 700,
                fontSize: 17,
                flexShrink: 0,
              }}
            >
              {user?.name?.[0]?.toUpperCase() ?? "?"}
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {user?.name ?? "—"}
              </div>
              <div style={{ fontSize: 11, color: T.inkSecondary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {user?.email ?? ""}
              </div>
              {teamName && (
                <div style={{ fontSize: 11, color: T.inkSecondary, marginTop: 2 }}>Team: {teamName}</div>
              )}
            </div>
          </div>

          <button
            onClick={onLogout}
            style={{
              width: "100%",
              padding: "10px 0",
              borderRadius: 8,
              background: T.surface,
              color: T.red,
              border: `1px solid ${T.red}40`,
              fontSize: 13,
              fontWeight: 700,
              cursor: "pointer",
              fontFamily: FONT_STACK,
            }}
          >
            Sign out
          </button>
        </div>
      )}

      {view === "devices" && micPerm !== "granted" && (
        <div style={{ padding: "20px" }}>
          <div
            style={{
              border: `1px solid ${T.border}`,
              borderRadius: 12,
              padding: "20px 18px",
              background: T.surfaceSoft,
            }}
          >
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: "50%",
                background: `${T.brand}1A`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                marginBottom: 14,
              }}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={T.brand} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            </div>
            <div style={{ fontSize: 15, fontWeight: 800, color: T.ink, marginBottom: 6 }}>
              Allow microphone access
            </div>
            <div style={{ fontSize: 12, color: T.inkSecondary, lineHeight: 1.5, marginBottom: 14 }}>
              {micPerm === "denied"
                ? "Microphone is blocked. Open chrome://settings/content/microphone, remove the block for this extension, then try again."
                : "Memoir needs your microphone to record meetings. Click below — Chrome will show its permission prompt."}
            </div>
            <button
              onClick={requestPermission}
              disabled={granting}
              style={{
                width: "100%",
                padding: "10px 0",
                borderRadius: 8,
                background: T.brand,
                color: "#fff",
                border: "none",
                fontSize: 13,
                fontWeight: 700,
                cursor: granting ? "default" : "pointer",
                opacity: granting ? 0.65 : 1,
                fontFamily: FONT_STACK,
              }}
            >
              {granting ? "Waiting for permission…" : "Allow microphone access"}
            </button>
            {grantError && (
              <div style={{ fontSize: 11, color: T.red, marginTop: 8 }}>{grantError}</div>
            )}
          </div>
        </div>
      )}

      {view === "devices" && micPerm === "granted" && (
        <div style={{ padding: "16px 20px 20px" }}>
          {/* Microphone */}
          <SectionLabel>Microphone</SectionLabel>
        <select
          value={selectedMicDeviceId ?? ""}
          onChange={(e) => onMicChange(e.target.value)}
          disabled={micLoading || micDevices.length === 0}
          style={selectStyle}
        >
          {micDevices.length === 0 ? (
            <option value="">{micLoading ? "Loading microphones…" : "No microphones found"}</option>
          ) : (
            micDevices.map((d) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label}
                {d.isDefault ? " (default)" : ""}
              </option>
            ))
          )}
        </select>
        {micError && (
          <div style={{ fontSize: 11, color: T.red, marginTop: 6 }}>{micError}</div>
        )}
        <p style={{ fontSize: 11, color: T.inkSecondary, marginTop: 6, lineHeight: 1.5 }}>
          Picks the input device used when recording tab audio. Bot recordings ignore this — the bot captures the meeting&apos;s mixed feed server-side.
        </p>

        <div style={{ height: 18 }} />

        {/* Stream mode */}
        <SectionLabel>Stream mode</SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <RadioRow
            label="Separate"
            description="Two tracks uploaded independently — tab audio + your microphone. Better diarization."
            value="separate"
            checked={selectedUploadMode === "separate"}
            onChange={() => onUploadModeChange("separate")}
          />
          <RadioRow
            label="Mixed"
            description="One single combined stream (Web Audio mix). Slightly smaller upload, but speaker separation is weaker."
            value="mixed"
            checked={selectedUploadMode === "mixed"}
            onChange={() => onUploadModeChange("mixed")}
          />
        </div>
        <p style={{ fontSize: 11, color: T.inkSecondary, marginTop: 10, lineHeight: 1.5 }}>
          Applies to private (tab audio) recordings only.
        </p>
        </div>
      )}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 10,
        fontWeight: 800,
        color: T.inkSecondary,
        textTransform: "uppercase",
        letterSpacing: 1.2,
        marginBottom: 8,
      }}
    >
      {children}
    </div>
  );
}

function RadioRow({
  label,
  description,
  value,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  value: string;
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <label
      style={{
        display: "flex",
        gap: 10,
        cursor: "pointer",
        padding: "10px 12px",
        borderRadius: 8,
        border: `1px solid ${checked ? T.brand : T.border}`,
        background: checked ? T.brandLight : T.surface,
        transition: "all 0.12s",
      }}
    >
      <input
        type="radio"
        name="stream-mode"
        value={value}
        checked={checked}
        onChange={onChange}
        style={{ marginTop: 2, accentColor: T.brand }}
      />
      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: T.ink }}>{label}</div>
        <div style={{ fontSize: 11, color: T.inkSecondary, marginTop: 2, lineHeight: 1.4 }}>{description}</div>
      </div>
    </label>
  );
}

const selectStyle: React.CSSProperties = {
  width: "100%",
  border: `1px solid ${T.border}`,
  borderRadius: 8,
  padding: "9px 11px",
  fontSize: 13,
  color: T.ink,
  background: T.surface,
  fontFamily: FONT_STACK,
};
