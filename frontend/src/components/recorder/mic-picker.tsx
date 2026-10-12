"use client";

import { useEffect, useState, useCallback } from "react";

const STORAGE_KEY = "memoir_selected_mic_device_id";

export interface MicPickerProps {
  selectedDeviceId: string | null;
  onChange: (deviceId: string | null) => void;
  /** True during recording — picker collapses to read-only. */
  disabled?: boolean;
  /** Optional override for the read-only label (e.g. "iPhone Microphone"). */
  inUseLabel?: string | null;
}

interface MicDevice {
  deviceId: string;
  label: string;
}

export function MicPicker({
  selectedDeviceId,
  onChange,
  disabled = false,
  inUseLabel,
}: MicPickerProps) {
  const [devices, setDevices] = useState<MicDevice[]>([]);

  const refresh = useCallback(async () => {
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) return;
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      const mics: MicDevice[] = all
        .filter((d) => d.kind === "audioinput")
        .map((d) => ({
          deviceId: d.deviceId,
          label: d.label || `Microphone (${d.deviceId.slice(0, 6)}…)`,
        }));
      setDevices(mics);
    } catch (e) {
      console.warn("[MicPicker] enumerateDevices failed:", e);
    }
  }, []);

  useEffect(() => {
    if (typeof navigator === "undefined") return;
    const md = navigator.mediaDevices;

    // Initial population. Device labels are empty until at least one
    // getUserMedia({audio}) has been granted for this origin (browser
    // privacy rule). If labels are missing, fire a transient mic
    // request so the prompt appears once on /record visit, then
    // re-enumerate to populate real device names.
    const bootstrap = async () => {
      if (!md?.enumerateDevices) return;
      const initial = await md.enumerateDevices();
      const audioInputs = initial.filter((d) => d.kind === "audioinput");
      const hasLabels = audioInputs.some((d) => !!d.label);
      if (!hasLabels && audioInputs.length > 0 && md.getUserMedia) {
        try {
          const stream = await md.getUserMedia({ audio: true });
          stream.getTracks().forEach((t) => t.stop());
        } catch {
          // Permission denied — fall through with placeholder labels.
        }
      }
      await refresh();
    };
    void bootstrap();

    if (!md?.addEventListener) return;
    const handler = () => void refresh();
    md.addEventListener("devicechange", handler);
    return () => md.removeEventListener("devicechange", handler);
  }, [refresh]);

  if (disabled) {
    const labelFromList = devices.find((d) => d.deviceId === selectedDeviceId)?.label;
    const label = inUseLabel ?? labelFromList ?? "default microphone";
    return (
      <div className="flex items-center gap-1.5 text-xs text-memoir-text-secondary">
        <MicIcon className="w-3.5 h-3.5" />
        <span>Recording from: {label}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <MicIcon className="w-4 h-4 text-memoir-text-secondary shrink-0" />
      <select
        value={selectedDeviceId ?? ""}
        onChange={(e) => {
          const next = e.target.value || null;
          onChange(next);
          try {
            if (next) localStorage.setItem(STORAGE_KEY, next);
            else localStorage.removeItem(STORAGE_KEY);
          } catch {
            /* localStorage unavailable */
          }
        }}
        className="flex-1 px-3 py-2 border border-memoir-border rounded-button text-sm bg-white focus:outline-none focus:ring-2 focus:ring-memoir-indigo/30 focus:border-memoir-indigo"
        aria-label="Select microphone"
      >
        <option value="">Default microphone</option>
        {devices.map((d) => (
          <option key={d.deviceId} value={d.deviceId}>
            {d.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/** Read the persisted mic deviceId from localStorage. Safe on SSR (returns null). */
export function loadStoredMicDeviceId(): string | null {
  if (typeof localStorage === "undefined") return null;
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function MicIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" />
    </svg>
  );
}
