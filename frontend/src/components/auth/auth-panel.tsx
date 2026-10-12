"use client";

/**
 * Wide two-panel layout used by /login and /signup.
 * Left: brand + headline + v1 insight chips.
 * Right: glass form card — mode toggle, OAuth buttons, email form,
 * footer mode-switch link.
 *
 * Mode is route-driven: /login toggles to /signup via router.push, and
 * vice versa, so URL stays canonical and ?redirect= is preserved.
 *
 * Functional contract is identical to login/page.tsx + signup/page.tsx —
 * email/password auth, OAuth start handlers, error display, loading
 * states, auto-redirect-when-already-authenticated, redirect-param
 * preservation through localStorage during OAuth.
 */

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

type Mode = "signin" | "signup";

export function AuthPanel({ mode }: { mode: Mode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTo = searchParams.get("redirect");
  const emailFromUrl = searchParams.get("email") || "";
  const isInviteFlow =
    searchParams.get("invite") === "true" ||
    (redirectTo?.includes("/join?code=") ?? false);

  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const setTeams = useAuthStore((s) => s.setTeams);
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const [name, setName] = useState("");
  const [email, setEmail] = useState(emailFromUrl);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [microsoftLoading, setMicrosoftLoading] = useState(false);

  // Auto-redirect when already authenticated (mirrors today's behavior).
  useEffect(() => {
    if (isAuthenticated && user) {
      router.replace(
        redirectTo || (user.onboarding_completed ? "/home" : "/onboarding"),
      );
    }
  }, [isAuthenticated, user, router, redirectTo]);

  const switchMode = (next: Mode) => {
    // Preserve the redirect query so the invite-trampoline flow doesn't break.
    const target = next === "signin" ? "/login" : "/signup";
    const params = new URLSearchParams();
    if (redirectTo) params.set("redirect", redirectTo);
    if (next === "signup" && isInviteFlow) {
      params.set("invite", "true");
      if (emailFromUrl) params.set("email", emailFromUrl);
    }
    const qs = params.toString();
    router.push(qs ? `${target}?${qs}` : target);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "signin") {
        const result = await api.login(email, password);
        setAccessToken(result.access_token);
        try {
          const teams = await api.getTeams();
          const teamInfos = teams.map((t) => ({ id: t.id, name: t.name, slug: t.slug }));
          setTeams(teamInfos);
          if (teamInfos.length > 0) {
            const store = useAuthStore.getState();
            const validIds = new Set(teamInfos.map((t) => t.id));
            if (!store.activeTeamId || !validIds.has(store.activeTeamId)) {
              useAuthStore.setState({ activeTeamId: teamInfos[0].id });
            }
          }
        } catch {
          // user may not have teams yet — fine
        }
        // Phase 2 Track B (notetaker-email plan §4.5 layer B1) — async
        // refresh any of the user's calendar watch channels close to
        // expiry. Fire-and-forget; we never block signin on it.
        api.refreshCalendarHealthOnSignin().catch(() => {});
        const currentUser = useAuthStore.getState().user;
        if (redirectTo) {
          router.push(redirectTo);
        } else {
          router.push(currentUser?.onboarding_completed ? "/home" : "/onboarding");
        }
      } else {
        // signup — narrower flow: no team fetch (matches existing behavior).
        const result = await api.signup({ email, password, name });
        setAccessToken(result.access_token);
        router.push(redirectTo || "/onboarding");
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : mode === "signin"
          ? "Invalid email or password"
          : "Signup failed. Please try again.",
      );
    } finally {
      setLoading(false);
    }
  };

  const startOAuth = async (provider: "google" | "microsoft") => {
    const setProviderLoading = provider === "google" ? setGoogleLoading : setMicrosoftLoading;
    setProviderLoading(true);
    setError("");
    try {
      // Stash redirect so the callback can route back to the originating page
      // (e.g. /join?code=... for invite flow).
      if (redirectTo) localStorage.setItem("oauth_redirect", redirectTo);
      const { redirect_url } =
        provider === "google" ? await api.getGoogleAuthUrl() : await api.getMicrosoftAuthUrl();
      window.location.href = redirect_url;
    } catch {
      setError(
        provider === "google"
          ? "Failed to start Google sign-in. Please try again."
          : "Failed to start Microsoft sign-in. Please try again.",
      );
      setProviderLoading(false);
    }
  };

  // Form input style — focused state expands the border to brand color.
  const inputClass =
    "w-full rounded-[10px] border-[1.5px] border-[#E0E0EA] bg-white px-3.5 py-3 text-sm text-[#1B1B1F] outline-none transition-[border-color,box-shadow] duration-200 focus:border-[#5C5CE0] focus:shadow-[0_0_0_3px_rgba(92,92,224,0.15)] placeholder:text-[#A0A0AE]";

  return (
    <div className="flex min-h-screen items-center px-6 py-10 md:px-10 lg:px-[60px]">
      {/* Left — messaging (hidden on narrow viewports) */}
      <div
        className="hidden flex-1 pr-0 lg:block lg:pr-[80px]"
        style={{ animation: "auth-fade-up 0.8s ease" }}
      >
        {/* Brand lockup — matches the in-product sidebar's tight lockup.
            The wordmark SVG has built-in horizontal padding inside its
            viewBox (~46px of 283 → ~16% empty on each side), so a small
            negative margin pulls the wordmark visually flush with the
            icon. Same trick the sidebar relies on (just less visible at
            its smaller scale). */}
        <div className="mb-12 flex items-center">
          <Image src="/logo-icon.svg" alt="" width={42} height={42} className="rounded-lg" priority />
          <Image
            src="/logo-white.svg"
            alt="Memoir"
            width={150}
            height={36}
            className="-ml-3"
            priority
          />
        </div>

        <h1
          className="mb-5 max-w-[480px] text-[36px] font-extrabold leading-[1.15] tracking-[-1px] text-white xl:text-[44px] xl:tracking-[-1.5px]"
        >
          Every conversation holds
          <br />
          <span style={{ color: "#7B7BF5", filter: "brightness(1.3)" }}>
            intelligence waiting to surface
          </span>
        </h1>

        <p className="mb-11 max-w-[420px] text-[17px] font-[450] leading-[1.7] text-white/40 xl:text-[18px]">
          From raw meetings to structured insights — delivered directly to the
          people who need them, without dilution.
        </p>

        {/* Insight chips (v1 content — emoji + label, no timestamps) */}
        <div className="flex max-w-[380px] flex-wrap gap-2.5">
          {[
            { emoji: "🔥", label: "Competitor mentioned" },
            { emoji: "🎯", label: "Buying signal detected" },
            { emoji: "✅", label: "3 action items" },
          ].map((chip, i) => (
            <div
              key={chip.label}
              className="flex items-center gap-1.5 rounded-[10px] border border-white/[0.08] bg-white/[0.06] px-3.5 py-2 text-xs font-semibold text-white/70 backdrop-blur-[10px]"
              style={{
                animation: `auth-fade-up 0.6s ease ${0.3 + i * 0.15}s both, auth-float-y ${
                  3.5 + i * 0.4
                }s ease-in-out infinite ${i * 0.3}s`,
              }}
            >
              <span>{chip.emoji}</span> {chip.label}
            </div>
          ))}
        </div>
      </div>

      {/* Right — glass form card */}
      <div
        className="w-full max-w-[420px] flex-shrink-0 rounded-[20px] px-7 py-8 md:px-10 md:py-9 mx-auto lg:mx-0"
        style={{
          background: "rgba(255,255,255,0.97)",
          backdropFilter: "blur(20px)",
          boxShadow:
            "0 20px 60px rgba(0,0,0,0.3), 0 0 0 1px rgba(255,255,255,0.1)",
          animation: "auth-fade-up 0.7s ease 0.15s both",
        }}
      >
        {/* Mode toggle */}
        <div className="mb-7 flex gap-0 rounded-[10px] bg-[#F0F0F6] p-[3px]">
          {(["signup", "signin"] as const).map((m) => {
            const active = (m === "signup" ? "signup" : "signin") === mode;
            return (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m === "signup" ? "signup" : "signin")}
                className={`flex-1 rounded-[8px] border-0 py-2.5 text-[13.5px] font-bold transition-colors ${
                  active
                    ? "bg-white text-[#1B1B1F] shadow-[0_1px_4px_rgba(0,0,0,0.06)]"
                    : "bg-transparent text-[#7C7C8A]"
                }`}
              >
                {m === "signup" ? "Create account" : "Sign in"}
              </button>
            );
          })}
        </div>

        {isInviteFlow && (
          <div className="mb-4 rounded-[10px] border border-[#5C5CE0]/20 bg-[#5C5CE0]/[0.06] px-3.5 py-3">
            <p className="text-[13px] font-semibold text-[#5C5CE0]">
              You&apos;ve been invited to join a team. Create your account to continue.
            </p>
          </div>
        )}

        <h2 className="m-0 mb-1 text-[22px] font-extrabold tracking-[-0.3px] text-[#1B1B1F]">
          {mode === "signup" ? "Start listening" : "Welcome back"}
        </h2>
        <p className="m-0 mb-6 text-[13px] font-[450] text-[#7C7C8A]">
          {mode === "signup"
            ? "Your first meeting insight is minutes away"
            : "Pick up where you left off"}
        </p>

        {/* OAuth buttons first — faster path */}
        <div className="mb-5 flex flex-col gap-2">
          <button
            type="button"
            onClick={() => startOAuth("google")}
            disabled={googleLoading}
            className="flex w-full items-center justify-center gap-2.5 rounded-[10px] border-[1.5px] border-[#E0E0EA] bg-white py-2.5 text-[13.5px] font-semibold text-[#1B1B1F] transition-colors hover:border-[#5C5CE0]/40 disabled:opacity-50"
          >
            <svg width={16} height={16} viewBox="0 0 24 24" aria-hidden="true">
              <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
              <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
              <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
              <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
            </svg>
            {googleLoading ? "Redirecting..." : "Continue with Google"}
          </button>
          <button
            type="button"
            onClick={() => startOAuth("microsoft")}
            disabled={microsoftLoading}
            className="flex w-full items-center justify-center gap-2.5 rounded-[10px] border-[1.5px] border-[#E0E0EA] bg-white py-2.5 text-[13.5px] font-semibold text-[#1B1B1F] transition-colors hover:border-[#5C5CE0]/40 disabled:opacity-50"
          >
            <svg width={16} height={16} viewBox="0 0 24 24" aria-hidden="true">
              <rect x="1" y="1" width="10" height="10" fill="#F25022" />
              <rect x="13" y="1" width="10" height="10" fill="#7FBA00" />
              <rect x="1" y="13" width="10" height="10" fill="#00A4EF" />
              <rect x="13" y="13" width="10" height="10" fill="#FFB900" />
            </svg>
            {microsoftLoading ? "Redirecting..." : "Continue with Microsoft"}
          </button>
        </div>

        {/* Divider */}
        <div className="mb-5 flex items-center gap-3">
          <div className="h-px flex-1 bg-[#E0E0EA]" />
          <span className="text-[11px] font-medium text-[#A0A0AE]">
            or continue with email
          </span>
          <div className="h-px flex-1 bg-[#E0E0EA]" />
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3.5">
          {mode === "signup" && (
            <div>
              <label
                htmlFor="auth-name"
                className="mb-1.5 block text-[12px] font-bold text-[#1B1B1F]"
              >
                Full name
              </label>
              <input
                id="auth-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Jane Doe"
                required
                className={inputClass}
              />
            </div>
          )}
          <div>
            <label
              htmlFor="auth-email"
              className="mb-1.5 block text-[12px] font-bold text-[#1B1B1F]"
            >
              Email
            </label>
            <input
              id="auth-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              required
              className={inputClass}
            />
          </div>
          <div>
            <label
              htmlFor="auth-password"
              className="mb-1.5 block text-[12px] font-bold text-[#1B1B1F]"
            >
              Password
            </label>
            <input
              id="auth-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              className={inputClass}
            />
          </div>

          {error && (
            <p role="alert" className="text-[12.5px] font-medium text-red-600">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="mt-1 w-full rounded-[10px] border-0 bg-[#5C5CE0] py-3.5 text-[14px] font-extrabold text-white shadow-[0_4px_16px_rgba(92,92,224,0.35)] transition-colors hover:bg-[#4848B8] disabled:opacity-60"
          >
            {loading
              ? mode === "signup"
                ? "Creating account..."
                : "Signing in..."
              : mode === "signup"
              ? "Create account"
              : "Sign in"}
          </button>
        </form>

        <p className="mt-5 text-center text-[12.5px] font-[450] text-[#7C7C8A]">
          {mode === "signup" ? "Already have an account? " : "Don't have an account? "}
          <button
            type="button"
            onClick={() => switchMode(mode === "signup" ? "signin" : "signup")}
            className="font-bold text-[#5C5CE0]"
          >
            {mode === "signup" ? "Sign in" : "Create one"}
          </button>
        </p>
      </div>
    </div>
  );
}
