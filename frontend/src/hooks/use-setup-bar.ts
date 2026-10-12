"use client";

import { useState } from "react";

const STORAGE_KEY = "memoir-setup-bar-dismissed";

export function useSetupBar() {
  const [dismissed, setDismissed] = useState(() =>
    typeof window !== "undefined"
      ? localStorage.getItem(STORAGE_KEY) === "true"
      : false,
  );

  const dismiss = () => {
    setDismissed(true);
    localStorage.setItem(STORAGE_KEY, "true");
  };

  const expand = () => {
    setDismissed(false);
    localStorage.removeItem(STORAGE_KEY);
  };

  return { dismissed, dismiss, expand };
}
