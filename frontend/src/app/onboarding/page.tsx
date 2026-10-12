"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";

type OnboardingStep =
  | "solo_or_team"
  | "team_sub_choice"
  | "domain_lookup"
  | "request_sent"
  | "role_select";

const ROLES = [
  { key: "founder", label: "Founder / CEO", icon: "\uD83C\uDFAF" },
  { key: "sales_lead", label: "Sales Lead / Manager", icon: "\uD83D\uDCCA" },
  { key: "sales_rep", label: "Sales Rep / AE", icon: "\uD83D\uDCBC" },
  { key: "revops", label: "RevOps / Product", icon: "\u2699\uFE0F" },
];

export default function OnboardingPage() {
  const router = useRouter();
  const setTeams = useAuthStore((s) => s.setTeams);
  const updateUser = useAuthStore((s) => s.updateUser);
  const refresh = useAuthStore((s) => s.refresh);
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [hydrated, setHydrated] = useState(false);

  const [step, setStep] = useState<OnboardingStep>("solo_or_team");
  const [companyName, setCompanyName] = useState("");
  const [role, setRole] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Domain lookup state
  const [lookupLoading, setLookupLoading] = useState(false);
  const [domainTeams, setDomainTeams] = useState<{ id: string; name: string }[]>([]);
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [domainNotFound, setDomainNotFound] = useState(false);
  const [requestSentTeam, setRequestSentTeam] = useState("");

  useEffect(() => {
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    if (!isAuthenticated || !user) {
      router.replace("/login");
    } else if (user.onboarding_completed) {
      router.replace("/home");
    }
  }, [hydrated, isAuthenticated, user, router]);

  const userDomain = user?.email?.split("@")[1]?.toLowerCase() || "";

  // Prefill company name with email domain once available
  useEffect(() => {
    if (userDomain && !companyName) {
      setCompanyName(userDomain);
    }
  }, [userDomain]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSoloOrTeam = (choice: "solo" | "team") => {
    if (choice === "solo") {
      setStep("role_select");
    } else {
      setStep("team_sub_choice");
    }
  };

  const handleTeamSubChoice = (choice: "new" | "join") => {
    if (choice === "new") {
      setStep("role_select");
    } else {
      handleDomainLookup();
    }
  };

  const handleDomainLookup = async () => {
    if (!userDomain) {
      setDomainNotFound(true);
      setStep("domain_lookup");
      return;
    }
    setLookupLoading(true);
    setError("");
    setStep("domain_lookup");

    try {
      const result = await api.lookupTeamByDomain(userDomain);
      if (result.found && result.teams.length > 0) {
        setDomainTeams(result.teams);
        setDomainNotFound(false);
      } else {
        setDomainTeams([]);
        setDomainNotFound(true);
      }
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    } catch (err) {
      setDomainNotFound(true);
      setDomainTeams([]);
    } finally {
      setLookupLoading(false);
    }
  };

  const handleRequestAccess = async (teamId: string, teamName: string) => {
    setSaving(true);
    setError("");
    try {
      await api.requestJoinTeam(teamId);
      setRequestSentTeam(teamName);
      setStep("request_sent");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send request");
    } finally {
      setSaving(false);
    }
  };

  const handleFinishRequestSent = async () => {
    setSaving(true);
    try {
      await api.updateProfile({ onboarding_completed: true });
      updateUser({ onboarding_completed: true });
      await refresh();
      router.push("/home");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to complete setup");
      setSaving(false);
    }
  };

  const handleComplete = async () => {
    setSaving(true);
    setError("");

    try {
      if (companyName) {
        const team = await api.createTeam({
          id: user?.team_id || undefined,
          name: companyName,
        });
        setTeams([{ id: team.id, name: team.name, slug: team.slug }]);
      }

      await api.updateProfile({ onboarding_completed: true });
      updateUser({ onboarding_completed: true });
      await refresh();
      router.push("/home");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to complete setup. Please try again.");
      setSaving(false);
    }
  };

  if (!hydrated || !isAuthenticated || !user || user.onboarding_completed) {
    return (
      <div className="min-h-screen bg-memoir-bg flex items-center justify-center">
        <div className="w-5 h-5 border-2 border-memoir-border border-t-memoir-indigo rounded-full animate-spin" />
      </div>
    );
  }

  const stepNumber = step === "solo_or_team" || step === "team_sub_choice" || step === "domain_lookup" || step === "request_sent" ? 1 : 2;
  const totalSteps = 2;

  return (
    <div className="min-h-screen bg-memoir-bg flex items-center justify-center px-4">
      <div className="w-full max-w-lg">
        <div className="flex items-center justify-between mb-8">
          <span className="flex items-center gap-1.5">
            <Image src="/logo-icon.svg" alt="" width={32} height={32} className="rounded-lg" priority />
            <Image src="/logo.svg" alt="Memoir" width={110} height={28} priority />
          </span>
          <span className="text-sm text-memoir-text-muted">
            Step {stepNumber}/{totalSteps}
          </span>
        </div>

        {/* Progress bar */}
        <div className="w-full h-1 bg-memoir-border rounded-full mb-8">
          <div
            className="h-full bg-memoir-indigo rounded-full transition-all duration-300"
            style={{ width: `${(stepNumber / totalSteps) * 100}%` }}
          />
        </div>

        <div className="bg-memoir-surface border border-memoir-border rounded-lg p-6">
          {error && (
            <p className="text-sm text-red-600 mb-4">{error}</p>
          )}

          {/* Step 1: Solo or Team */}
          {step === "solo_or_team" && (
            <div>
              <h2 className="text-lg font-semibold text-memoir-text mb-1">
                Welcome{user.name ? `, ${user.name}` : ""}!
              </h2>
              <p className="text-sm text-memoir-text-muted mb-6">
                Are you setting up Memoir for yourself or your team?
              </p>

              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => handleSoloOrTeam("solo")}
                  className="p-5 rounded-lg border border-memoir-border hover:border-memoir-indigo hover:bg-memoir-bg transition-colors text-center"
                >
                  <div className="text-2xl mb-2">{"\uD83D\uDC64"}</div>
                  <p className="text-sm font-medium text-memoir-text">Just for me</p>
                  <p className="text-xs text-memoir-text-muted mt-0.5">Solo workspace</p>
                </button>
                <button
                  onClick={() => handleSoloOrTeam("team")}
                  className="p-5 rounded-lg border border-memoir-border hover:border-memoir-indigo hover:bg-memoir-bg transition-colors text-center"
                >
                  <div className="text-2xl mb-2">{"\uD83D\uDC65"}</div>
                  <p className="text-sm font-medium text-memoir-text">For my team</p>
                  <p className="text-xs text-memoir-text-muted mt-0.5">Invite teammates</p>
                </button>
              </div>
            </div>
          )}

          {/* Team sub-choice: New workspace or Join existing */}
          {step === "team_sub_choice" && (
            <div>
              <h2 className="text-lg font-semibold text-memoir-text mb-1">
                Team setup
              </h2>
              <p className="text-sm text-memoir-text-muted mb-6">
                Are you setting up a new workspace or joining your company&apos;s existing one?
              </p>

              <div className="space-y-3">
                <button
                  onClick={() => handleTeamSubChoice("new")}
                  className="w-full p-4 rounded-lg border border-memoir-border hover:border-memoir-indigo hover:bg-memoir-bg transition-colors text-left"
                >
                  <p className="text-sm font-medium text-memoir-text">Setting up a new workspace</p>
                  <p className="text-xs text-memoir-text-muted mt-0.5">
                    I&apos;m the first person from my company
                  </p>
                </button>
                <button
                  onClick={() => handleTeamSubChoice("join")}
                  className="w-full p-4 rounded-lg border border-memoir-border hover:border-memoir-indigo hover:bg-memoir-bg transition-colors text-left"
                >
                  <p className="text-sm font-medium text-memoir-text">Joining existing workspace</p>
                  <p className="text-xs text-memoir-text-muted mt-0.5">
                    My company already uses Memoir
                  </p>
                </button>
              </div>

              <Button variant="outline" className="mt-4" onClick={() => setStep("solo_or_team")}>
                Back
              </Button>
            </div>
          )}

          {/* Domain lookup results */}
          {step === "domain_lookup" && (
            <div>
              {lookupLoading ? (
                <div className="py-8 text-center">
                  <div className="w-6 h-6 border-2 border-memoir-border border-t-memoir-indigo rounded-full animate-spin mx-auto mb-3" />
                  <p className="text-sm text-memoir-text-muted">
                    Looking for workspaces at {userDomain}...
                  </p>
                </div>
              ) : domainTeams.length > 0 ? (
                <div>
                  <h2 className="text-lg font-semibold text-memoir-text mb-1">
                    We found a workspace!
                  </h2>
                  <p className="text-sm text-memoir-text-muted mb-6">
                    There&apos;s an existing workspace for <span className="font-medium">@{userDomain}</span>.
                  </p>
                  <div className="space-y-3 mb-6">
                    {domainTeams.map((t) => (
                      <div
                        key={t.id}
                        className="flex items-center justify-between p-4 rounded-lg border border-memoir-border"
                      >
                        <div>
                          <p className="text-sm font-medium text-memoir-text">{t.name}</p>
                          <p className="text-xs text-memoir-text-muted">@{userDomain}</p>
                        </div>
                        <Button
                          onClick={() => handleRequestAccess(t.id, t.name)}
                          disabled={saving}
                          size="sm"
                        >
                          {saving ? "Requesting..." : "Request Access"}
                        </Button>
                      </div>
                    ))}
                  </div>
                  <Button variant="outline" onClick={() => setStep("team_sub_choice")}>
                    Back
                  </Button>
                </div>
              ) : (
                <div>
                  <h2 className="text-lg font-semibold text-memoir-text mb-1">
                    No workspace found
                  </h2>
                  <p className="text-sm text-memoir-text-muted mb-6">
                    We couldn&apos;t find an existing workspace for <span className="font-medium">@{userDomain}</span>.
                    Ask your admin to invite you, or set up your own.
                  </p>
                  <div className="flex gap-3">
                    <Button onClick={() => setStep("role_select")}>
                      Set up my own
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => router.push("/join")}
                    >
                      I have an invite code
                    </Button>
                  </div>
                  <button
                    onClick={() => setStep("team_sub_choice")}
                    className="text-xs text-memoir-text-muted mt-4 hover:text-memoir-text-secondary block"
                  >
                    &larr; Back
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Request sent confirmation */}
          {step === "request_sent" && (
            <div className="text-center py-4">
              <div className="w-12 h-12 bg-emerald-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-6 h-6 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              </div>
              <h2 className="text-lg font-semibold text-memoir-text mb-2">
                Request sent!
              </h2>
              <p className="text-sm text-memoir-text-muted mb-6">
                Your request to join <span className="font-medium">{requestSentTeam}</span> has been sent.
                You&apos;ll be notified when an admin approves it.
              </p>
              <Button onClick={handleFinishRequestSent} disabled={saving}>
                {saving ? "Finishing..." : "Continue to Dashboard"}
              </Button>
            </div>
          )}

          {/* Step 2: Role selection */}
          {step === "role_select" && (
            <div>
              <h2 className="text-lg font-semibold text-memoir-text mb-1">
                What best describes your role?
              </h2>
              <p className="text-sm text-memoir-text-muted mb-6">
                This helps us customize your experience.
              </p>

              <div className="space-y-1 mb-6">
                <Label className="text-sm text-memoir-text-secondary">Company / workspace name</Label>
                <Input
                  value={companyName}
                  onChange={(e) => setCompanyName(e.target.value)}
                  placeholder="Acme Corp"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                {ROLES.map((r) => (
                  <button
                    key={r.key}
                    onClick={() => setRole(r.key)}
                    className={`p-4 rounded-lg border transition-colors text-center ${
                      role === r.key
                        ? "border-memoir-indigo bg-memoir-bg"
                        : "border-memoir-border hover:border-memoir-indigo/40"
                    }`}
                  >
                    <div className="text-xl mb-1">{r.icon}</div>
                    <p className="text-sm font-medium text-memoir-text">{r.label}</p>
                  </button>
                ))}
              </div>
              <Button
                className="w-full mt-6"
                disabled={!role || !companyName || saving}
                onClick={handleComplete}
              >
                {saving ? "Setting up..." : "Continue"}
              </Button>
              <button
                onClick={() => setStep("solo_or_team")}
                className="text-xs text-memoir-text-muted mt-3 hover:text-memoir-text-secondary block mx-auto"
              >
                &larr; Start over
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
