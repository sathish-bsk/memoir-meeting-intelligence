"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { WebRecorder } from "@/components/recorder/web-recorder";

export default function RecordPage() {
  const router = useRouter();
  const accessToken = useAuthStore((s) => s.accessToken);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (mounted && !accessToken) {
      router.replace("/auth/login?next=/record");
    }
  }, [mounted, accessToken, router]);

  if (!mounted || !accessToken || !activeTeamId) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-sm text-memoir-text-secondary">Loading…</div>
      </div>
    );
  }

  return <WebRecorder teamId={activeTeamId} />;
}
