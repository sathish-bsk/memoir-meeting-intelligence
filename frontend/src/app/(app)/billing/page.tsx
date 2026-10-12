"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";

const PLAN_LABELS: Record<string, string> = {
  trial: "Trial",
  free: "Free",
  paid: "Paid",
};

const LIMIT_LABELS: Record<string, string> = {
  calls_per_month: "Calls / month",
  team_members: "Team members",
  storage_gb: "Storage (GB)",
  intel_reports_per_month: "Intel reports / month",
};

export default function BillingPage() {
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const isOwner = user?.team_role === "owner";

  const { data: planInfo, isLoading } = useQuery({
    queryKey: ["team-plan", activeTeamId],
    queryFn: () => api.getTeamPlan(activeTeamId!),
    enabled: !!activeTeamId,
  });

  const plan = planInfo?.plan || user?.plan || "free";
  const planLabel = PLAN_LABELS[plan] || plan;
  const limits = planInfo?.limits || {};
  const trialEndsAt = planInfo?.trial_ends_at;

  let trialDaysLeft: number | null = null;
  if (plan === "trial" && trialEndsAt) {
    const diff = new Date(trialEndsAt).getTime() - Date.now();
    trialDaysLeft = Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)));
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-xl font-bold text-memoir-text mb-2 font-display">Billing & Plan</h1>
      <p className="text-sm text-memoir-text-muted mb-6">
        Manage your team&apos;s subscription and plan limits.
      </p>

      {isLoading ? (
        <div className="space-y-4">
          <div className="h-24 bg-memoir-border rounded-card animate-pulse" />
          <div className="h-48 bg-memoir-border rounded-card animate-pulse" />
        </div>
      ) : (
        <>
          {/* Plan badge */}
          <div className="bg-memoir-surface border border-memoir-border rounded-card p-5 mb-6">
            <div className="flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-base font-semibold text-memoir-text">{planLabel} Plan</h3>
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      plan === "paid"
                        ? "bg-emerald-100 text-emerald-700"
                        : plan === "trial"
                          ? "bg-blue-100 text-blue-700"
                          : "bg-memoir-border text-memoir-text-muted"
                    }`}
                  >
                    {planLabel}
                  </span>
                </div>
                {plan === "trial" && trialDaysLeft !== null && (
                  <p className="text-sm text-memoir-text-muted mt-1">
                    {trialDaysLeft > 0
                      ? `${trialDaysLeft} day${trialDaysLeft !== 1 ? "s" : ""} remaining in your trial`
                      : "Your trial has expired"}
                  </p>
                )}
                {plan === "free" && (
                  <p className="text-sm text-memoir-text-muted mt-1">
                    Upgrade to unlock more calls and features.
                  </p>
                )}
              </div>
              {isOwner && plan !== "paid" && (
                <button
                  className="px-4 py-2 bg-memoir-indigo text-white text-sm font-medium rounded-button hover:bg-memoir-indigo/90 transition-colors"
                  onClick={() => alert("Contact sales at hello@meetmemoir.com to upgrade your plan.")}
                >
                  Upgrade
                </button>
              )}
            </div>
          </div>

          {/* Limits table */}
          <div className="bg-memoir-surface border border-memoir-border rounded-card overflow-hidden">
            <div className="px-5 py-3 border-b border-memoir-border">
              <h3 className="text-sm font-semibold text-memoir-text">Plan Limits</h3>
            </div>
            <div className="divide-y divide-memoir-border">
              {Object.entries(limits).map(([key, value]) => (
                <div key={key} className="px-5 py-3 flex items-center justify-between">
                  <span className="text-sm text-memoir-text-secondary">{LIMIT_LABELS[key] || key}</span>
                  <span className="text-sm font-medium text-memoir-text">
                    {value === -1 ? "Unlimited" : String(value)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {!isOwner && (
            <p className="text-xs text-memoir-text-muted mt-4">
              Only the team owner can manage billing and upgrade the plan.
            </p>
          )}
        </>
      )}
    </div>
  );
}
