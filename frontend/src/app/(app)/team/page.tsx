"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { TeamMember } from "@/lib/types";

export default function TeamPage() {
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const queryClient = useQueryClient();

  const [inviteEmail, setInviteEmail] = useState("");
  const [showInviteForm, setShowInviteForm] = useState(false);
  const [inviteResult, setInviteResult] = useState<{ code: string; teamName: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const isAdminOrOwner = user?.team_role === "owner" || user?.team_role === "admin";
  const isOwner = user?.team_role === "owner";

  const { data: members = [], isLoading: membersLoading } = useQuery({
    queryKey: ["team-members", activeTeamId],
    queryFn: () => api.getTeamMembers(activeTeamId!),
    enabled: !!activeTeamId,
  });

  const { data: planInfo } = useQuery({
    queryKey: ["team-plan", activeTeamId],
    queryFn: () => api.getTeamPlan(activeTeamId!),
    enabled: !!activeTeamId,
  });

  const inviteMutation = useMutation({
    mutationFn: (email: string) => api.inviteToTeam(activeTeamId!, email),
    onSuccess: (data) => {
      setInviteResult({ code: data.invite_code, teamName: data.team_name });
      setInviteEmail("");
      queryClient.invalidateQueries({ queryKey: ["team-members", activeTeamId] });
    },
  });

  const reinviteMutation = useMutation({
    mutationFn: (memberId: string) => api.reinviteTeamMember(activeTeamId!, memberId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["team-members", activeTeamId] });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (memberId: string) => api.removeTeamMember(activeTeamId!, memberId),
    onSuccess: () => {
      setConfirmRemove(null);
      queryClient.invalidateQueries({ queryKey: ["team-members", activeTeamId] });
    },
  });

  const approveMutation = useMutation({
    mutationFn: (memberId: string) => api.approveJoinRequest(activeTeamId!, memberId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["team-members", activeTeamId] });
    },
  });

  const declineMutation = useMutation({
    mutationFn: (memberId: string) => api.declineJoinRequest(activeTeamId!, memberId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["team-members", activeTeamId] });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (memberId: string) => api.revokeInvite(activeTeamId!, memberId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["team-members", activeTeamId] });
    },
  });

  // Group members by status
  const activeMembers = members.filter((m: TeamMember) => m.invite_status === "accepted");
  const pendingMembers = members.filter((m: TeamMember) => m.invite_status === "pending");
  const requestedMembers = members.filter((m: TeamMember) => m.invite_status === "requested");

  const memberLimit = planInfo?.limits?.team_members ?? 3;
  const planName = planInfo?.plan || user?.plan || "free";

  const canRemoveMember = (m: TeamMember) => {
    if (!isAdminOrOwner) return false;
    if (m.role === "owner") return false;
    if (m.role === "admin" && !isOwner) return false;
    return true;
  };

  const ROLE_BADGE: Record<string, string> = {
    owner: "bg-violet-100 text-violet-700",
    admin: "bg-blue-100 text-blue-700",
    member: "bg-memoir-border text-memoir-text-secondary",
  };

  const STATUS_DOT: Record<string, string> = {
    accepted: "bg-emerald-400",
    pending: "bg-amber-400",
    requested: "bg-blue-400",
  };

  const renderMemberRow = (m: TeamMember) => {
    const isYou = m.user_id === user?.id;
    const initial = (m.name || m.email || "?")[0].toUpperCase();
    const isPending = m.invite_status === "pending";
    const isRequested = m.invite_status === "requested";
    const isActive = m.invite_status === "accepted";

    return (
      <div
        key={m.id}
        className={`px-4 py-3 flex items-center justify-between ${isPending || isRequested ? "opacity-80" : ""}`}
      >
        <div className="flex items-center gap-3 min-w-0">
          {/* Status dot */}
          <div className="relative flex-shrink-0">
            <div className="w-8 h-8 bg-memoir-indigo/10 rounded-full flex items-center justify-center">
              <span className="text-xs font-semibold text-memoir-indigo">{initial}</span>
            </div>
            <div
              className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-memoir-surface ${STATUS_DOT[m.invite_status] || STATUS_DOT.accepted}`}
            />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-memoir-text truncate">
                {m.name || m.email || "Pending invite"}
                {isYou && <span className="text-memoir-text-muted ml-1">(you)</span>}
              </p>
              {isActive && m.calls_this_week > 0 && (
                <span className="text-[10px] text-memoir-text-muted flex-shrink-0">
                  {m.calls_this_week} call{m.calls_this_week !== 1 ? "s" : ""} this week
                </span>
              )}
            </div>
            <p className="text-xs text-memoir-text-muted truncate">
              {m.email}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Requested: Approve / Decline */}
          {isRequested && isAdminOrOwner && (
            <>
              <button
                onClick={() => approveMutation.mutate(m.id)}
                disabled={approveMutation.isPending}
                className="px-2.5 py-1 text-xs font-medium text-emerald-600 bg-emerald-50 rounded-button hover:bg-emerald-100 transition-colors disabled:opacity-50"
              >
                {approveMutation.isPending ? "..." : "Approve"}
              </button>
              <button
                onClick={() => declineMutation.mutate(m.id)}
                disabled={declineMutation.isPending}
                className="px-2.5 py-1 text-xs font-medium text-red-600 bg-red-50 rounded-button hover:bg-red-100 transition-colors disabled:opacity-50"
              >
                {declineMutation.isPending ? "..." : "Decline"}
              </button>
            </>
          )}

          {isRequested && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">
              Requested
            </span>
          )}

          {/* Pending: Resend / Revoke */}
          {isPending && isAdminOrOwner && (
            <>
              <button
                onClick={() => reinviteMutation.mutate(m.id)}
                disabled={reinviteMutation.isPending}
                className="px-2.5 py-1 text-xs font-medium text-memoir-indigo hover:text-memoir-indigo/80 transition-colors disabled:opacity-50"
              >
                {reinviteMutation.isPending ? "Sending..." : "Resend"}
              </button>
              <button
                onClick={() => revokeMutation.mutate(m.id)}
                disabled={revokeMutation.isPending}
                className="px-2.5 py-1 text-xs font-medium text-red-500 hover:text-red-600 transition-colors disabled:opacity-50"
              >
                {revokeMutation.isPending ? "..." : "Revoke"}
              </button>
            </>
          )}

          {isPending && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">
              Pending
            </span>
          )}

          {/* Active: role badge */}
          {isActive && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium capitalize ${ROLE_BADGE[m.role] || ROLE_BADGE.member}`}>
              {m.role}
            </span>
          )}

          {/* Remove button for active members only (pending uses Revoke, requested uses Decline) */}
          {canRemoveMember(m) && isActive && (
            <>
              {confirmRemove === m.id ? (
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => removeMutation.mutate(m.id)}
                    disabled={removeMutation.isPending}
                    className="px-2.5 py-1 text-xs font-medium text-red-600 bg-red-50 rounded-button hover:bg-red-100 transition-colors disabled:opacity-50"
                  >
                    {removeMutation.isPending ? "..." : "Confirm"}
                  </button>
                  <button
                    onClick={() => setConfirmRemove(null)}
                    className="px-2 py-1 text-xs text-memoir-text-muted hover:text-memoir-text-secondary"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmRemove(m.id)}
                  className="p-1 text-memoir-text-muted hover:text-red-500 transition-colors"
                  title="Remove member"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-xl font-bold text-memoir-text mb-2 font-display">Team</h1>
      <p className="text-sm text-memoir-text-muted mb-6">
        Manage your team members and send invitations.
      </p>

      {membersLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-14 animate-pulse rounded-card bg-memoir-border" />
          ))}
        </div>
      ) : (
        <>
          {/* Members list */}
          <div className="bg-memoir-surface border border-memoir-border rounded-card overflow-hidden">
            <div className="px-4 py-3 border-b border-memoir-border flex items-center justify-between">
              <h2 className="text-sm font-semibold text-memoir-text">
                Members
                <span className="ml-2 text-xs font-normal text-memoir-text-muted">
                  {activeMembers.length} / {memberLimit === -1 ? "Unlimited" : memberLimit}
                </span>
              </h2>
              {isAdminOrOwner && (
                <button
                  onClick={() => { setShowInviteForm(true); setInviteResult(null); }}
                  className="px-3 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors"
                >
                  Invite member
                </button>
              )}
            </div>

            <div className="divide-y divide-memoir-border">
              {/* Active members */}
              {activeMembers.map(renderMemberRow)}

              {/* Pending invites */}
              {pendingMembers.length > 0 && (
                <>
                  {activeMembers.length > 0 && (
                    <div className="px-4 py-2 bg-memoir-bg">
                      <span className="text-[10px] font-semibold text-memoir-text-muted uppercase tracking-wider">
                        Pending Invites
                      </span>
                    </div>
                  )}
                  {pendingMembers.map(renderMemberRow)}
                </>
              )}

              {/* Requested joins */}
              {requestedMembers.length > 0 && (
                <>
                  <div className="px-4 py-2 bg-memoir-bg">
                    <span className="text-[10px] font-semibold text-memoir-text-muted uppercase tracking-wider">
                      Join Requests
                    </span>
                  </div>
                  {requestedMembers.map(renderMemberRow)}
                </>
              )}

              {/* Empty state */}
              {members.length <= 1 && isAdminOrOwner && (
                <div className="px-4 py-8 text-center">
                  <p className="text-sm text-memoir-text-muted">
                    No other team members yet. Invite your colleagues to get started.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Invite form — shown inline below the members card */}
          {isAdminOrOwner && showInviteForm && (
            <div className="mt-4 bg-memoir-surface border border-memoir-border rounded-card p-5">
              {!inviteResult ? (
                <div>
                  <h3 className="text-sm font-semibold text-memoir-text mb-3">Invite by email</h3>
                  <div className="flex gap-2">
                    <input
                      type="email"
                      placeholder="colleague@company.com"
                      value={inviteEmail}
                      onChange={(e) => setInviteEmail(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && inviteEmail) inviteMutation.mutate(inviteEmail);
                      }}
                      className="flex-1 px-3 py-2 text-sm border border-memoir-border rounded-button bg-white focus:outline-none focus:ring-2 focus:ring-memoir-indigo/40 focus:border-memoir-indigo"
                    />
                    <button
                      onClick={() => inviteMutation.mutate(inviteEmail)}
                      disabled={!inviteEmail || inviteMutation.isPending}
                      className="px-4 py-2 bg-memoir-indigo text-white text-sm font-medium rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
                    >
                      {inviteMutation.isPending ? "Sending..." : "Send invite"}
                    </button>
                  </div>
                  {inviteMutation.isError && (
                    <p className="text-xs text-red-500 mt-2">
                      {(inviteMutation.error as Error).message}
                    </p>
                  )}
                  <button
                    onClick={() => setShowInviteForm(false)}
                    className="text-xs text-memoir-text-muted mt-2 hover:text-memoir-text-secondary"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-5 h-5 rounded-full bg-emerald-100 flex items-center justify-center">
                      <svg className="w-3 h-3 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                      </svg>
                    </div>
                    <h3 className="text-sm font-semibold text-emerald-600">Invite sent!</h3>
                  </div>
                  <p className="text-sm text-memoir-text-muted mb-2">
                    Invite code: <span className="font-mono font-bold text-memoir-text">{inviteResult.code}</span>
                  </p>
                  <p className="text-xs text-memoir-text-muted mb-3">
                    They can also join at <span className="font-medium">/join?code={inviteResult.code}</span>
                  </p>
                  <div className="flex gap-3">
                    <button
                      onClick={() => { setInviteResult(null); }}
                      className="text-xs font-medium text-memoir-indigo hover:text-memoir-indigo/80"
                    >
                      Invite another
                    </button>
                    <button
                      onClick={() => { setInviteResult(null); setShowInviteForm(false); }}
                      className="text-xs text-memoir-text-muted hover:text-memoir-text-secondary"
                    >
                      Done
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Usage meter */}
          <div className="mt-4 bg-memoir-surface border border-memoir-border rounded-card p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-memoir-text-muted">Team members</span>
              <span className="text-xs text-memoir-text-muted">
                {activeMembers.length} / {memberLimit === -1 ? "Unlimited" : memberLimit} &middot; {planName} plan
              </span>
            </div>
            <div className="w-full h-1.5 bg-memoir-bg rounded-full overflow-hidden">
              <div
                className="h-full bg-memoir-indigo rounded-full transition-all duration-500"
                style={{ width: memberLimit === -1 ? "0%" : `${Math.min((activeMembers.length / memberLimit) * 100, 100)}%` }}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
