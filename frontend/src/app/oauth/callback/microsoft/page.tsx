"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";

function MicrosoftCallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const setTeams = useAuthStore((s) => s.setTeams);
  const [error, setError] = useState("");
  const calledRef = useRef(false);

  useEffect(() => {
    if (calledRef.current) return;
    calledRef.current = true;

    const code = searchParams.get("code");
    const state = searchParams.get("state");

    if (!code || !state) {
      router.replace("/login?error=oauth_failed");
      return;
    }

    (async () => {
      try {
        const result = await api.handleMicrosoftAuthCallback(code, state);
        setAccessToken(result.access_token);

        // Fetch teams
        try {
          const teams = await api.getTeams();
          setTeams(teams.map((t) => ({ id: t.id, name: t.name, slug: t.slug })));
        } catch {
          // User may not have teams yet
        }

        // Check for stored redirect (e.g. invite flow)
        const oauthRedirect = localStorage.getItem("oauth_redirect");
        if (oauthRedirect) {
          localStorage.removeItem("oauth_redirect");
          router.replace(oauthRedirect);
        } else {
          const user = useAuthStore.getState().user;
          router.replace(user?.onboarding_completed ? "/home" : "/onboarding");
        }
      } catch {
        setError("Sign-in failed. Please try again.");
        setTimeout(() => router.replace("/login?error=oauth_failed"), 2000);
      }
    })();
  }, [searchParams, setAccessToken, setTeams, router]);

  if (error) {
    return (
      <div className="min-h-screen bg-memoir-bg flex items-center justify-center">
        <p className="text-sm text-red-600">{error}</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-memoir-bg flex items-center justify-center">
      <div className="text-center">
        <div className="w-6 h-6 border-2 border-memoir-border border-t-memoir-indigo rounded-full animate-spin mx-auto" />
        <p className="text-sm text-memoir-text-muted mt-3">Signing you in...</p>
      </div>
    </div>
  );
}

export default function MicrosoftCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-memoir-bg flex items-center justify-center">
          <div className="w-6 h-6 border-2 border-memoir-border border-t-memoir-indigo rounded-full animate-spin" />
        </div>
      }
    >
      <MicrosoftCallbackInner />
    </Suspense>
  );
}
