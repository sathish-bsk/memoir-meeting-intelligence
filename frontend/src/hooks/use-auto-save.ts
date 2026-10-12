"use client";

import { useCallback, useRef, useState } from "react";

export type SaveStatus = "idle" | "saving" | "saved" | "error";

interface UseAutoSaveOptions {
  /** Debounce delay in ms (default 1500) */
  delay?: number;
  /** Callback that performs the actual save */
  onSave: () => Promise<void>;
}

/**
 * Debounced auto-save hook with status tracking.
 * Returns { status, trigger, reset }.
 */
export function useAutoSave({ delay = 1500, onSave }: UseAutoSaveOptions) {
  const [status, setStatus] = useState<SaveStatus>("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const trigger = useCallback(() => {
    // Clear previous timers
    if (timerRef.current) clearTimeout(timerRef.current);
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);

    setStatus("saving");

    timerRef.current = setTimeout(async () => {
      try {
        await onSave();
        setStatus("saved");
        // Revert to idle after 2s
        savedTimerRef.current = setTimeout(() => setStatus("idle"), 2000);
      } catch {
        setStatus("error");
        savedTimerRef.current = setTimeout(() => setStatus("idle"), 3000);
      }
    }, delay);
  }, [delay, onSave]);

  const reset = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    setStatus("idle");
  }, []);

  return { status, trigger, reset };
}
