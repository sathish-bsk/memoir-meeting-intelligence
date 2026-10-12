import React from "react";
import { T, FONT_STACK } from "../theme";

interface Props {
  initial: string;
  recording?: boolean;
  onSettings?: () => void;
  onAvatarClick?: () => void;
}

/** Shared popup header — logo + settings + avatar gradient.
 *  Matches memoir-chrome-extension.jsx header (lines 80-97). */
export function PopupHeader({ initial, recording = false, onSettings, onAvatarClick }: Props) {
  return (
    <div
      style={{
        padding: "16px 20px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        borderBottom: `1px solid ${T.border}`,
        background: recording ? `linear-gradient(135deg, ${T.red}08, ${T.red}03)` : T.surface,
        fontFamily: FONT_STACK,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {/* Use the same /logo-icon.svg + /logo.svg the web app sidebar uses
            — keeps the brand mark consistent across surfaces. Built into
            dist/ via webpack CopyPlugin (assets/logo-{icon,}.svg). */}
        <img src="logo-icon.svg" alt="" width={30} height={30} style={{ borderRadius: 8, display: "block" }} />
        <img src="logo.svg" alt="Memoir" height={28} style={{ display: "block" }} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {onSettings && (
          <button
            onClick={onSettings}
            style={{ background: "none", border: "none", cursor: "pointer", display: "flex", padding: 4 }}
            aria-label="Settings"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={T.inkSecondary} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
        )}
        <button
          onClick={onAvatarClick}
          style={{
            width: 28,
            height: 28,
            borderRadius: "50%",
            background: `linear-gradient(135deg, ${T.brand}, ${T.brandDark})`,
            color: "#fff",
            fontWeight: 700,
            fontSize: 11,
            border: "none",
            cursor: onAvatarClick ? "pointer" : "default",
            fontFamily: FONT_STACK,
          }}
          aria-label="Account"
        >
          {initial}
        </button>
      </div>
    </div>
  );
}
