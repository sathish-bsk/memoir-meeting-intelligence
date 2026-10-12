"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [hydrated, setHydrated] = useState(false);
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  useEffect(() => {
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    if (!isAuthenticated || !user) {
      // Preserve current path+query so login can redirect back
      const fullPath = typeof window !== "undefined"
        ? window.location.pathname + window.location.search
        : "";
      const redirectParam = fullPath && fullPath !== "/home"
        ? `?redirect=${encodeURIComponent(fullPath)}`
        : "";
      router.replace(`/login${redirectParam}`);
    } else if (!user.onboarding_completed) {
      router.replace("/onboarding");
    }
  }, [hydrated, isAuthenticated, user, router]);

  if (!hydrated || !isAuthenticated || !user || !user.onboarding_completed) {
    return (
      <div className="min-h-screen bg-memoir-bg flex items-center justify-center">
        <div className="w-5 h-5 border-2 border-memoir-border border-t-memoir-indigo rounded-full animate-spin" />
      </div>
    );
  }

  return <>{children}</>;
}
