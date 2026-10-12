"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { AdminTeamLookup } from "@/lib/types";

const PLAN_OPTIONS = [
  { value: "trial", label: "Trial" },
  { value: "free", label: "Free" },
  { value: "paid", label: "Paid" },
];

export default function SuperAdminPage() {
  const user = useAuthStore((s) => s.user);
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [lookupResult, setLookupResult] = useState<AdminTeamLookup | null>(null);
  const [selectedPlan, setSelectedPlan] = useState("");
  const [updateSuccess, setUpdateSuccess] = useState("");
  const [updateError, setUpdateError] = useState("");

  useEffect(() => {
    if (user && !user.is_super_admin) {
      router.replace("/meetings");
    }
  }, [user, router]);

  const lookupMutation = useMutation({
    mutationFn: (email: string) => api.adminLookupTeam(email),
    onSuccess: (data) => {
      setLookupResult(data);
      setSelectedPlan(data.plan);
      setUpdateSuccess("");
      setUpdateError("");
    },
    onError: (error: Error) => {
      setLookupResult(null);
      setUpdateError(error.message);
    },
  });

  const updateMutation = useMutation({
    mutationFn: () =>
      api.adminUpdatePlan(lookupResult!.user_email, selectedPlan),
    onSuccess: (data) => {
      setUpdateSuccess(data.message);
      setUpdateError("");
      if (lookupResult) {
        setLookupResult({ ...lookupResult, plan: selectedPlan, plan_limits: data.limits });
      }
    },
    onError: (error: Error) => {
      setUpdateError(error.message);
      setUpdateSuccess("");
    },
  });

  if (!user?.is_super_admin) {
    return null;
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-xl font-bold text-zinc-900 mb-1">Super Admin</h1>
      <p className="text-sm text-zinc-500 mb-8">Manage team plans and limits.</p>

      {/* Lookup */}
      <div className="bg-white border border-zinc-200 rounded-lg p-5 mb-6">
        <h2 className="text-sm font-semibold text-zinc-900 mb-3">Upgrade Plan</h2>
        <div className="flex gap-2">
          <input
            type="email"
            placeholder="Enter user email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && email) lookupMutation.mutate(email);
            }}
            className="flex-1 px-3 py-2 text-sm border border-zinc-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-900"
          />
          <button
            onClick={() => lookupMutation.mutate(email)}
            disabled={!email || lookupMutation.isPending}
            className="px-4 py-2 bg-zinc-900 text-white text-sm font-medium rounded-lg hover:bg-zinc-800 transition-colors disabled:opacity-50"
          >
            {lookupMutation.isPending ? "Looking up..." : "Lookup"}
          </button>
        </div>

        {lookupMutation.isError && !lookupResult && (
          <p className="text-xs text-red-500 mt-2">{updateError}</p>
        )}
      </div>

      {/* Results */}
      {lookupResult && (
        <div className="bg-white border border-zinc-200 rounded-lg p-5">
          <h3 className="text-sm font-semibold text-zinc-900 mb-4">Team Details</h3>
          <div className="space-y-2 mb-5">
            <div className="flex justify-between text-sm">
              <span className="text-zinc-500">User</span>
              <span className="text-zinc-900 font-medium">{lookupResult.user_name}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-zinc-500">Email</span>
              <span className="text-zinc-900">{lookupResult.user_email}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-zinc-500">Team</span>
              <span className="text-zinc-900">{lookupResult.team_name}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-zinc-500">Current Plan</span>
              <span className={`font-medium ${
                lookupResult.plan === "paid"
                  ? "text-emerald-600"
                  : lookupResult.plan === "trial"
                    ? "text-blue-600"
                    : "text-zinc-600"
              }`}>
                {lookupResult.plan}
              </span>
            </div>
            {lookupResult.trial_ends_at && (
              <div className="flex justify-between text-sm">
                <span className="text-zinc-500">Trial ends</span>
                <span className="text-zinc-900">
                  {new Date(lookupResult.trial_ends_at).toLocaleDateString()}
                </span>
              </div>
            )}
          </div>

          <div className="border-t border-zinc-100 pt-4">
            <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">
              Current Limits
            </h4>
            <div className="grid grid-cols-2 gap-2 mb-5">
              {Object.entries(lookupResult.plan_limits).map(([key, value]) => (
                <div key={key} className="text-xs">
                  <span className="text-zinc-400">{key.replace(/_/g, " ")}: </span>
                  <span className="text-zinc-700 font-medium">{value === -1 ? "Unlimited" : value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="border-t border-zinc-100 pt-4">
            <h4 className="text-sm font-medium text-zinc-900 mb-3">Change Plan</h4>
            <div className="flex gap-2 items-center">
              <select
                value={selectedPlan}
                onChange={(e) => setSelectedPlan(e.target.value)}
                className="px-3 py-2 text-sm border border-zinc-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-900"
              >
                {PLAN_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <button
                onClick={() => updateMutation.mutate()}
                disabled={selectedPlan === lookupResult.plan || updateMutation.isPending}
                className="px-4 py-2 bg-zinc-900 text-white text-sm font-medium rounded-lg hover:bg-zinc-800 transition-colors disabled:opacity-50"
              >
                {updateMutation.isPending ? "Updating..." : "Update Plan"}
              </button>
            </div>

            {updateSuccess && (
              <p className="text-sm text-emerald-600 mt-3 bg-emerald-50 px-3 py-2 rounded-lg">
                {updateSuccess}
              </p>
            )}
            {updateError && updateMutation.isError && (
              <p className="text-sm text-red-500 mt-3 bg-red-50 px-3 py-2 rounded-lg">
                {updateError}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
