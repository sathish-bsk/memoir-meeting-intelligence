"use client";

/**
 * Invite acceptance — routing trampoline, not a form.
 *
 * Behavior is identical to the pre-redesign page:
 *   - Unauthenticated + ?code= present → fetch invite details and
 *     redirect to /signup (or /login if the email already has an
 *     account), carrying the code via ?redirect=/join?code=...
 *   - Authenticated + ?code= present → auto-call api.joinTeam(code)
 *     once (StrictMode-guarded by useRef) and route to /home.
 *   - Authenticated + no code → small manual-code-entry form.
 *
 * Only the rendered chrome changed: the old white card is now an
 * <AuthCard> on the dark <AuthShell> background.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import { AuthShell } from "@/components/auth/auth-shell";
import { AuthCard } from "@/components/auth/auth-card";

function JoinFallback() {
  return <AuthCard state="loading" message="Checking invite..." />;
}

function JoinTeamContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const codeFromUrl = searchParams.get("code") || "";

  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const switchTeam = useAuthStore((s) => s.switchTeam);
  const updateUser = useAuthStore((s) => s.updateUser);

  const [inviteCode, setInviteCode] = useState(codeFromUrl);
  const [checking, setChecking] = useState(!!codeFromUrl && !isAuthenticated);
  const [inviteError, setInviteError] = useState<string | null>(null);

  useEffect(() => {
    if (codeFromUrl) setInviteCode(codeFromUrl);
  }, [codeFromUrl]);

  // Invited users skip onboarding — mark complete if not already.
  useEffect(() => {
    if (isAuthenticated && user && !user.onboarding_completed) {
      api.updateProfile({ onboarding_completed: true }).catch(() => {});
      updateUser({ onboarding_completed: true });
    }
  }, [isAuthenticated, user, updateUser]);

  // Smart routing: unauthenticated + code → look up invite + redirect.
  useEffect(() => {
    if (isAuthenticated || !codeFromUrl) return;
    let cancelled = false;
    setChecking(true);

    api
      .getInviteDetails(codeFromUrl)
      .then((result) => {
        if (cancelled) return;
        const joinRedirect = `/join?code=${codeFromUrl}`;
        if (result.account_exists) {
          router.replace(`/login?redirect=${encodeURIComponent(joinRedirect)}`);
        } else {
          const signupUrl = result.email
            ? `/signup?redirect=${encodeURIComponent(joinRedirect)}&email=${encodeURIComponent(result.email)}&invite=true`
            : `/signup?redirect=${encodeURIComponent(joinRedirect)}&invite=true`;
          router.replace(signupUrl);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setChecking(false);
          setInviteError("This invite link is invalid or has been revoked.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, codeFromUrl, router]);

  const joinMutation = useMutation({
    mutationFn: (code: string) => api.joinTeam(code),
    onSuccess: async (team) => {
      await switchTeam(team.id);
      try {
        await api.updateProfile({ onboarding_completed: true });
      } catch {
        // non-critical
      }
      updateUser({ onboarding_completed: true });
      router.push("/home");
    },
  });

  // Auto-join when authenticated + code present (StrictMode guard).
  const autoJoinRef = useRef(false);
  useEffect(() => {
    if (!isAuthenticated || !codeFromUrl || autoJoinRef.current) return;
    if (joinMutation.isPending || joinMutation.isSuccess) return;
    autoJoinRef.current = true;
    joinMutation.mutate(codeFromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, codeFromUrl]);

  // ── Rendered states ─────────────────────────────────────────────

  if (checking && !isAuthenticated) {
    return <AuthCard state="loading" message="Checking invite..." />;
  }

  if (inviteError && !isAuthenticated) {
    return (
      <AuthCard
        state="error"
        title="Invalid invite"
        message={`${inviteError} Please ask your team admin for a new invite link.`}
        primaryCta={{ label: "Sign in", href: "/login" }}
      />
    );
  }

  if (!isAuthenticated) {
    const redirectQs = encodeURIComponent(`/join?code=${inviteCode}`);
    return (
      <AuthCard
        state="loading"
        title="Join a team"
        message="You need to be logged in to accept a team invite."
        primaryCta={{ label: "Log in", href: `/login?redirect=${redirectQs}` }}
        secondaryCta={{
          label: "Don't have an account? Sign up",
          href: `/signup?redirect=${redirectQs}&invite=true`,
        }}
      />
    );
  }

  // Authenticated with code — auto-joining in progress.
  if (codeFromUrl) {
    if (joinMutation.isError) {
      return (
        <AuthCard
          state="error"
          title="Could not join team"
          message={(joinMutation.error as Error).message}
          primaryCta={{
            label: "Try again",
            onClick: () => {
              autoJoinRef.current = false;
              joinMutation.mutate(codeFromUrl);
            },
          }}
          secondaryCta={{ label: "Back to home", onClick: () => router.push("/home") }}
        />
      );
    }
    return (
      <AuthCard
        state="loading"
        title="Joining team..."
        message={`Hi ${user?.name ?? ""}! We're adding you to the team now.`}
      />
    );
  }

  // Authenticated without code — manual invite code entry.
  return (
    <AuthCard
      state="success"
      title="Join a team"
      message={`Hi ${user?.name ?? ""}! Enter your invite code to join the team.`}
      extra={
        <div className="mt-2 flex flex-col gap-3">
          <input
            type="text"
            placeholder="MEM-XXXX-XXXX"
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
            className="w-full rounded-[10px] border-[1.5px] border-[#E0E0EA] bg-white px-3 py-2.5 text-center font-mono text-sm text-[#1B1B1F] outline-none focus:border-[#5C5CE0] focus:shadow-[0_0_0_3px_rgba(92,92,224,0.15)]"
          />
          {joinMutation.isError && (
            <p role="alert" className="text-xs font-medium text-red-600">
              {(joinMutation.error as Error).message}
            </p>
          )}
          {joinMutation.isSuccess && (
            <p className="text-xs font-medium text-emerald-600">
              Welcome to the team! Redirecting...
            </p>
          )}
        </div>
      }
      primaryCta={{
        label: joinMutation.isPending ? "Joining..." : "Accept invite",
        onClick: () => {
          if (inviteCode && !joinMutation.isPending) joinMutation.mutate(inviteCode);
        },
      }}
      secondaryCta={{ label: "Back to home", onClick: () => router.push("/home") }}
    />
  );
}

export default function JoinTeamPage() {
  return (
    <AuthShell>
      <Suspense fallback={<JoinFallback />}>
        <JoinTeamContent />
      </Suspense>
    </AuthShell>
  );
}
