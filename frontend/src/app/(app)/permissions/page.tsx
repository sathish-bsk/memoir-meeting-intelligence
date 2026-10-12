"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/store/auth";
import { api, ApiError } from "@/lib/api";
import type { TeamMember } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Check } from "lucide-react";

type PermissionsTab = "members" | "module-permissions" | "role-reference";

const PERMISSION_TABS: { key: PermissionsTab; label: string }[] = [
  { key: "members", label: "Members & Roles" },
  { key: "module-permissions", label: "Module Permissions" },
  { key: "role-reference", label: "Role Reference" },
];

// ─── Constants ──────────────────────────────────────────────────

const MODULES = ["calls", "intelligence", "team"] as const;
const MODULE_ACTIONS: Record<string, string[]> = {
  calls: ["read", "write", "delete"],
  intelligence: ["read", "write"],
  team: ["read", "manage"],
};

const ROLE_SCOPES: Record<string, Set<string>> = {
  member: new Set([
    "calls:read",
    "calls:write",
    "calls:share",
    "intelligence:read",
    "team:read",
  ]),
  admin: new Set([
    "calls:read",
    "calls:write",
    "calls:delete",
    "calls:share",
    "intelligence:read",
    "intelligence:write",
    "team:read",
    "team:manage",
    "team:members:invite",
    "team:members:remove",
    "team:billing:read",
  ]),
  owner: new Set([
    "calls:read",
    "calls:write",
    "calls:delete",
    "calls:share",
    "intelligence:read",
    "intelligence:write",
    "team:read",
    "team:manage",
    "team:members:invite",
    "team:members:remove",
    "team:members:role",
    "team:billing:read",
    "team:billing:manage",
  ]),
};

const ALL_SCOPES_GROUPED = [
  {
    category: "Meetings",
    scopes: ["calls:read", "calls:write", "calls:delete", "calls:share"],
  },
  {
    category: "Intelligence",
    scopes: ["intelligence:read", "intelligence:write"],
  },
  {
    category: "Team",
    scopes: [
      "team:read",
      "team:manage",
      "team:members:invite",
      "team:members:remove",
      "team:members:role",
      "team:billing:read",
      "team:billing:manage",
    ],
  },
];

const ROLE_BADGE_CLASSES: Record<string, string> = {
  owner:
    "bg-violet-100 text-violet-700 border-violet-200 hover:bg-violet-100",
  admin: "bg-blue-100 text-blue-700 border-blue-200 hover:bg-blue-100",
  member: "bg-memoir-border text-memoir-text-secondary border-memoir-border hover:bg-memoir-border",
};

function scopeLabel(scope: string): string {
  return scope
    .replace(":", " ")
    .replace("team members ", "")
    .replace("team billing ", "billing ")
    .replace("team ", "")
    .replace(/^calls /, "meetings ");
}

// ─── Main Page ──────────────────────────────────────────────────

export default function PermissionsPage() {
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);

  if (user?.team_role !== "owner") {
    return (
      <div className="max-w-4xl mx-auto">
        <h1 className="text-xl font-bold text-memoir-text mb-6 font-display">Permissions</h1>
        <div className="rounded-card border border-memoir-border bg-memoir-surface p-8 text-center">
          <p className="text-memoir-text-muted">
            You don&apos;t have permission to access this page.
          </p>
        </div>
      </div>
    );
  }

  return <PermissionsContent teamId={activeTeamId!} currentUserId={user.id} />;
}

function PermissionsContent({ teamId, currentUserId }: { teamId: string; currentUserId: string }) {
  const [activeTab, setActiveTab] = useState<PermissionsTab>("members");

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-xl font-bold text-memoir-text mb-2 font-display">Permissions</h1>
      <p className="text-sm text-memoir-text-muted mb-6">
        Manage team roles, member permissions, and access controls.
      </p>

      <div className="flex items-center gap-1 mb-6 border-b border-memoir-border">
        {PERMISSION_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
              activeTab === tab.key
                ? "border-memoir-indigo text-memoir-indigo"
                : "border-transparent text-memoir-text-secondary hover:text-memoir-text hover:border-memoir-border"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "members" && (
        <MembersTab teamId={teamId} currentUserId={currentUserId} />
      )}
      {activeTab === "module-permissions" && (
        <ModulePermissionsTab teamId={teamId} />
      )}
      {activeTab === "role-reference" && <RoleReferenceTab />}
    </div>
  );
}

// ─── Tab 1: Members & Roles ─────────────────────────────────────

function MembersTab({
  teamId,
  currentUserId,
}: {
  teamId: string;
  currentUserId: string;
}) {
  const queryClient = useQueryClient();
  const refresh = useAuthStore((s) => s.refresh);
  const [transferTarget, setTransferTarget] = useState<TeamMember | null>(null);
  const [error, setError] = useState("");

  const { data: members, isLoading } = useQuery({
    queryKey: ["team-members", teamId],
    queryFn: () => api.getTeamMembers(teamId),
  });

  const changeRole = useMutation({
    mutationFn: ({
      memberUserId,
      role,
    }: {
      memberUserId: string;
      role: string;
    }) => api.changeMemberRole(teamId, memberUserId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["team-members", teamId] });
      setError("");
    },
    onError: (err: Error) => {
      setError(err instanceof ApiError ? err.message : "Failed to change role");
    },
  });

  const handleRoleChange = (member: TeamMember, newRole: string) => {
    if (newRole === "owner") {
      setTransferTarget(member);
      return;
    }
    changeRole.mutate({ memberUserId: member.user_id, role: newRole });
  };

  const confirmTransfer = async () => {
    if (!transferTarget) return;
    changeRole.mutate(
      { memberUserId: transferTarget.user_id, role: "owner" },
      {
        onSuccess: async () => {
          setTransferTarget(null);
          await refresh();
          window.location.href = "/home";
        },
      },
    );
  };

  if (isLoading) {
    return (
      <div className="mt-4 space-y-3">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="h-14 animate-pulse rounded-card bg-memoir-border"
          />
        ))}
      </div>
    );
  }

  const sorted = [...(members || [])].sort((a, b) => {
    const order = { owner: 0, admin: 1, member: 2 };
    return (
      (order[a.role as keyof typeof order] ?? 3) -
      (order[b.role as keyof typeof order] ?? 3)
    );
  });

  return (
    <div>
      {error && (
        <p className="mb-3 text-sm text-red-600">{error}</p>
      )}
      <div className="space-y-2">
        {sorted
          .filter((m) => m.invite_status === "accepted" || !m.invite_status)
          .map((member) => {
            const isCurrentUser = member.user_id === currentUserId;
            const isOwner = member.role === "owner";

            return (
              <div
                key={member.id}
                className="flex items-center justify-between rounded-card border border-memoir-border bg-memoir-surface p-3"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-memoir-indigo/10 text-sm font-medium text-memoir-indigo">
                    {(member.name || member.email || "?")
                      .charAt(0)
                      .toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-memoir-text truncate">
                      {member.name || "Unknown"}
                      {isCurrentUser && (
                        <span className="ml-1 text-memoir-text-muted">(you)</span>
                      )}
                    </p>
                    <p className="text-xs text-memoir-text-muted truncate">
                      {member.email}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  {isOwner || isCurrentUser ? (
                    <Badge
                      variant="outline"
                      className={ROLE_BADGE_CLASSES[member.role] || ""}
                    >
                      {member.role}
                    </Badge>
                  ) : (
                    <Select
                      value={member.role}
                      onValueChange={(val) => handleRoleChange(member, val)}
                      disabled={changeRole.isPending}
                    >
                      <SelectTrigger className="w-28 h-8 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="member">Member</SelectItem>
                        <SelectItem value="admin">Admin</SelectItem>
                        <SelectItem value="owner">Owner</SelectItem>
                      </SelectContent>
                    </Select>
                  )}
                </div>
              </div>
            );
          })}
      </div>

      {/* Ownership transfer confirmation dialog */}
      <Dialog
        open={!!transferTarget}
        onOpenChange={(open) => !open && setTransferTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Transfer Ownership</DialogTitle>
            <DialogDescription>
              You are about to transfer ownership to{" "}
              <strong>{transferTarget?.name || transferTarget?.email}</strong>.
              You will be demoted to Admin. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setTransferTarget(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmTransfer}
              disabled={changeRole.isPending}
            >
              {changeRole.isPending ? "Transferring..." : "Transfer Ownership"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── Tab 2: Module Permissions ──────────────────────────────────

interface MemberOverrides {
  [memberUserId: string]: Record<string, boolean>;
}

function ModulePermissionsTab({ teamId }: { teamId: string }) {
  const queryClient = useQueryClient();
  const [overridesMap, setOverridesMap] = useState<MemberOverrides>({});
  const [error, setError] = useState("");
  const debounceTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>(
    {},
  );

  const { data: members, isLoading } = useQuery({
    queryKey: ["team-members", teamId],
    queryFn: () => api.getTeamMembers(teamId),
  });

  const eligibleMembers = (members || []).filter(
    (m) =>
      m.role !== "owner" &&
      (m.invite_status === "accepted" || !m.invite_status),
  );

  const { data: scopesData } = useQuery({
    queryKey: ["member-scopes", teamId, eligibleMembers.map((m) => m.user_id)],
    queryFn: async () => {
      const results: Record<
        string,
        { overrides: Record<string, boolean>; role: string }
      > = {};
      await Promise.all(
        eligibleMembers.map(async (m) => {
          try {
            const data = await api.getMemberScopes(teamId, m.user_id);
            results[m.user_id] = { overrides: data.overrides, role: m.role };
          } catch {
            results[m.user_id] = { overrides: {}, role: m.role };
          }
        }),
      );
      return results;
    },
    enabled: eligibleMembers.length > 0,
  });

  useEffect(() => {
    if (scopesData) {
      const map: MemberOverrides = {};
      for (const [uid, data] of Object.entries(scopesData)) {
        map[uid] = { ...data.overrides };
      }
      setOverridesMap(map);
    }
  }, [scopesData]);

  const updateScopes = useMutation({
    mutationFn: ({
      memberUserId,
      overrides,
    }: {
      memberUserId: string;
      overrides: Record<string, boolean>;
    }) => api.updateMemberScopes(teamId, memberUserId, overrides),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["member-scopes"] });
      setError("");
    },
    onError: (err: Error) => {
      setError(
        err instanceof ApiError ? err.message : "Failed to update permissions",
      );
    },
  });

  const handleToggle = useCallback(
    (memberUserId: string, scope: string, member: TeamMember) => {
      const roleDefaults = ROLE_SCOPES[member.role] || new Set();
      const currentOverrides = overridesMap[memberUserId] || {};

      const isOverridden = scope in currentOverrides;
      const isEnabled = isOverridden
        ? currentOverrides[scope]
        : roleDefaults.has(scope);

      const newOverrides = { ...currentOverrides };

      if (isEnabled) {
        if (roleDefaults.has(scope)) {
          newOverrides[scope] = false;
        } else {
          delete newOverrides[scope];
        }
      } else {
        if (!roleDefaults.has(scope)) {
          newOverrides[scope] = true;
        } else {
          delete newOverrides[scope];
        }
      }

      setOverridesMap((prev) => ({
        ...prev,
        [memberUserId]: newOverrides,
      }));

      const timerKey = memberUserId;
      if (debounceTimers.current[timerKey]) {
        clearTimeout(debounceTimers.current[timerKey]);
      }
      debounceTimers.current[timerKey] = setTimeout(() => {
        updateScopes.mutate({
          memberUserId,
          overrides: newOverrides,
        });
      }, 300);
    },
    [overridesMap, updateScopes],
  );

  const handleReset = (memberUserId: string) => {
    setOverridesMap((prev) => ({ ...prev, [memberUserId]: {} }));
    updateScopes.mutate({ memberUserId, overrides: {} });
  };

  if (isLoading) {
    return (
      <div className="mt-4 h-40 animate-pulse rounded-card bg-memoir-border" />
    );
  }

  if (eligibleMembers.length === 0) {
    return (
      <div className="mt-4 rounded-card border border-memoir-border bg-memoir-surface p-6 text-center text-sm text-memoir-text-muted">
        No non-owner members to configure. Invite team members first.
      </div>
    );
  }

  return (
    <div>
      {error && (
        <p className="mb-3 text-sm text-red-600">{error}</p>
      )}
      <div className="overflow-x-auto rounded-card border border-memoir-border bg-memoir-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-memoir-border">
              <th className="p-3 text-left font-medium text-memoir-text-muted w-40">
                Member
              </th>
              {MODULES.map((mod) => (
                <th
                  key={mod}
                  colSpan={MODULE_ACTIONS[mod]?.length || 1}
                  className="p-2 text-center font-medium text-memoir-text-muted capitalize border-l border-memoir-border"
                >
                  {mod}
                </th>
              ))}
              <th className="p-2 w-16 border-l border-memoir-border" />
            </tr>
            <tr className="border-b border-memoir-border bg-memoir-bg">
              <th />
              {MODULES.map((mod) =>
                (MODULE_ACTIONS[mod] || []).map((action, idx) => (
                  <th
                    key={`${mod}:${action}`}
                    className={`p-2 text-center text-xs font-normal text-memoir-text-muted ${
                      idx === 0 ? "border-l border-memoir-border" : ""
                    }`}
                  >
                    {action.charAt(0).toUpperCase()}
                  </th>
                )),
              )}
              <th className="border-l border-memoir-border" />
            </tr>
          </thead>
          <tbody>
            {eligibleMembers.map((member) => {
              const roleDefaults = ROLE_SCOPES[member.role] || new Set();
              const memberOverrides = overridesMap[member.user_id] || {};
              const hasOverrides = Object.keys(memberOverrides).length > 0;

              return (
                <tr
                  key={member.user_id}
                  className="border-b border-memoir-border/50 last:border-b-0"
                >
                  <td className="p-3">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-memoir-text truncate text-sm">
                        {member.name || member.email}
                      </span>
                      <Badge
                        variant="outline"
                        className={`text-[10px] px-1.5 py-0 ${ROLE_BADGE_CLASSES[member.role] || ""}`}
                      >
                        {member.role.charAt(0).toUpperCase()}
                      </Badge>
                    </div>
                  </td>
                  {MODULES.map((mod) =>
                    (MODULE_ACTIONS[mod] || []).map((action, idx) => {
                      const scope = `${mod}:${action}`;
                      const isOverridden = scope in memberOverrides;
                      const isEnabled = isOverridden
                        ? memberOverrides[scope]
                        : roleDefaults.has(scope);

                      return (
                        <td
                          key={scope}
                          className={`p-2 text-center ${
                            idx === 0
                              ? "border-l border-memoir-border"
                              : ""
                          }`}
                        >
                          <div className="flex justify-center">
                            <Checkbox
                              checked={isEnabled}
                              onCheckedChange={() =>
                                handleToggle(member.user_id, scope, member)
                              }
                              className={
                                isOverridden
                                  ? "ring-2 ring-memoir-indigo/50 ring-offset-1"
                                  : ""
                              }
                            />
                          </div>
                        </td>
                      );
                    }),
                  )}
                  <td className="p-2 text-center border-l border-memoir-border">
                    {hasOverrides && (
                      <button
                        onClick={() => handleReset(member.user_id)}
                        className="text-xs text-memoir-indigo hover:text-memoir-indigo/80 whitespace-nowrap"
                      >
                        Reset
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-memoir-text-muted">
        Checkboxes with a ring indicate permissions that differ from the
        role default.
      </p>
    </div>
  );
}

// ─── Tab 3: Role Reference ──────────────────────────────────────

function RoleReferenceTab() {
  return (
    <div>
      <p className="mb-4 text-sm text-memoir-text-muted">
        Roles define default permissions. Use the Module Permissions tab to
        customize per member.
      </p>
      <div className="overflow-x-auto rounded-card border border-memoir-border bg-memoir-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-memoir-border bg-memoir-bg">
              <th className="p-3 text-left font-medium text-memoir-text-secondary">
                Permission
              </th>
              <th className="p-3 text-center font-medium text-memoir-text-secondary">
                Member
              </th>
              <th className="p-3 text-center font-medium text-memoir-text-secondary">
                Admin
              </th>
              <th className="p-3 text-center font-medium text-memoir-text-secondary">
                Owner
              </th>
            </tr>
          </thead>
          <tbody>
            {ALL_SCOPES_GROUPED.map((group) => (
              <Fragment key={group.category}>
                <tr className="bg-memoir-bg/50 border-b border-memoir-border">
                  <td
                    colSpan={4}
                    className="px-3 py-1.5 text-xs font-semibold text-memoir-text-muted uppercase tracking-wider"
                  >
                    {group.category}
                  </td>
                </tr>
                {group.scopes.map((scope) => (
                  <tr
                    key={scope}
                    className="border-b border-memoir-border/50 last:border-b-0"
                  >
                    <td className="p-3 text-memoir-text-secondary capitalize">
                      {scopeLabel(scope)}
                    </td>
                    {(["member", "admin", "owner"] as const).map((role) => (
                      <td key={role} className="p-3 text-center">
                        {ROLE_SCOPES[role]?.has(scope) ? (
                          <Check className="h-4 w-4 text-emerald-600 mx-auto" />
                        ) : (
                          <span className="text-memoir-text-muted">-</span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
