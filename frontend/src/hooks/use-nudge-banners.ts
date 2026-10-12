"use client";

import { useState, useCallback } from "react";

const STORAGE_PREFIX = "memoir-nudge-dismissed-";
const REAPPEAR_DAYS = 7;

function isDismissed(id: string): boolean {
  if (typeof window === "undefined") return false;
  const raw = localStorage.getItem(`${STORAGE_PREFIX}${id}`);
  if (!raw) return false;
  const dismissed = Number(raw);
  const elapsed = Date.now() - dismissed;
  return elapsed < REAPPEAR_DAYS * 24 * 60 * 60 * 1000;
}

function dismiss(id: string) {
  localStorage.setItem(`${STORAGE_PREFIX}${id}`, String(Date.now()));
}

export function useNudgeBanners(ids: string[]) {
  const [dismissed, setDismissed] = useState<Record<string, boolean>>(() => {
    const map: Record<string, boolean> = {};
    ids.forEach((id) => {
      map[id] = isDismissed(id);
    });
    return map;
  });

  const dismissBanner = useCallback((id: string) => {
    dismiss(id);
    setDismissed((prev) => ({ ...prev, [id]: true }));
  }, []);

  const isVisible = useCallback(
    (id: string) => !dismissed[id],
    [dismissed],
  );

  return { isVisible, dismissBanner };
}
