"use client";

import { useEffect, useState } from "react";
import { apiLoadingSubscribe } from "@/lib/api-loading";

export function GlobalLoadingBar() {
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    return apiLoadingSubscribe(setLoading);
  }, []);

  if (!loading) return null;

  return (
    <div className="fixed top-0 left-0 right-0 z-[9999] h-[2px]">
      <div className="h-full bg-memoir-indigo animate-loading-bar" />
    </div>
  );
}
