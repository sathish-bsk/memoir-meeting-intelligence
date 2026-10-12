"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import type { UserProfile } from "@/lib/types";

// ─── Avatar helper ──────────────────────────────────────────────

const AVATAR_COLORS = [
  "bg-blue-500",
  "bg-emerald-500",
  "bg-violet-500",
  "bg-amber-500",
  "bg-rose-500",
  "bg-cyan-500",
  "bg-indigo-500",
  "bg-teal-500",
];

function getAvatarColor(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

// ─── Profile Page ───────────────────────────────────────────────

export default function ProfilePage() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editError, setEditError] = useState("");

  // Change email dialog
  const [emailDialogOpen, setEmailDialogOpen] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [emailPassword, setEmailPassword] = useState("");
  const [emailError, setEmailError] = useState("");
  const [emailSuccess, setEmailSuccess] = useState("");

  // Change password
  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [passwordSuccess, setPasswordSuccess] = useState("");

  // Resend verification
  const [resendMessage, setResendMessage] = useState("");

  const { data: profile, isLoading } = useQuery<UserProfile>({
    queryKey: ["profile"],
    queryFn: () => api.getProfile(),
  });

  // ─── Mutations ──────────────────────────────────────────────

  const updateProfile = useMutation({
    mutationFn: (data: { name: string }) => api.updateProfile(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      setEditing(false);
      setEditError("");
    },
    onError: (err: Error) => {
      setEditError(
        err instanceof ApiError ? err.message : "Failed to update profile",
      );
    },
  });

  const changeEmail = useMutation({
    mutationFn: (data: { new_email: string; password: string }) =>
      api.changeEmail(data),
    onSuccess: (res) => {
      setEmailSuccess(res.message);
      setEmailError("");
    },
    onError: (err: Error) => {
      setEmailError(
        err instanceof ApiError ? err.message : "Failed to change email",
      );
      setEmailSuccess("");
    },
  });

  const changePassword = useMutation({
    mutationFn: (data: { current_password: string; new_password: string }) =>
      api.changePassword(data),
    onSuccess: (res) => {
      setPasswordSuccess(res.message);
      setPasswordError("");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    },
    onError: (err: Error) => {
      setPasswordError(
        err instanceof ApiError ? err.message : "Failed to change password",
      );
      setPasswordSuccess("");
    },
  });

  const resendVerification = useMutation({
    mutationFn: () => api.resendVerification(),
    onSuccess: (res) => {
      setResendMessage(res.message);
    },
    onError: () => {
      setResendMessage("Failed to send verification email");
    },
  });

  // ─── Handlers ───────────────────────────────────────────────

  const startEditing = () => {
    setEditName(profile?.name || "");
    setEditError("");
    setEditing(true);
  };

  const cancelEditing = () => {
    setEditing(false);
    setEditError("");
  };

  const saveProfile = () => {
    if (!editName.trim()) {
      setEditError("Name is required");
      return;
    }
    updateProfile.mutate({ name: editName.trim() });
  };

  const openEmailDialog = () => {
    setNewEmail("");
    setEmailPassword("");
    setEmailError("");
    setEmailSuccess("");
    setEmailDialogOpen(true);
  };

  const submitEmailChange = () => {
    if (!newEmail.trim() || !emailPassword) {
      setEmailError("All fields are required");
      return;
    }
    changeEmail.mutate({ new_email: newEmail.trim(), password: emailPassword });
  };

  const submitPasswordChange = () => {
    if (!currentPassword || !newPassword || !confirmPassword) {
      setPasswordError("All fields are required");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("New passwords do not match");
      return;
    }
    changePassword.mutate({
      current_password: currentPassword,
      new_password: newPassword,
    });
  };

  // ─── Loading / empty ───────────────────────────────────────

  if (isLoading) {
    return (
      <div>
        <div className="h-8 w-48 bg-memoir-border rounded-card animate-pulse mb-6" />
        <div className="h-32 bg-memoir-border rounded-card animate-pulse" />
      </div>
    );
  }

  if (!profile) return null;

  const initials = profile.name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  const createdDate = new Date(profile.created_at).toLocaleDateString(
    "en-US",
    {
      month: "long",
      year: "numeric",
    },
  );

  // ─── Render ─────────────────────────────────────────────────

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-memoir-text font-display">Profile</h1>
          <p className="text-sm text-memoir-text-muted mt-1">
            Manage your personal information and account settings.
          </p>
        </div>
        <Link
          href="/settings/notifications"
          className="text-sm text-memoir-indigo hover:text-memoir-indigo/80 font-medium transition-colors"
        >
          Notification settings &rarr;
        </Link>
      </div>

      <div className="space-y-6">
        {/* Profile card */}
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-6">
          <div className="flex items-start justify-between mb-6">
            <div className="flex items-center gap-4">
              <div
                className={`w-14 h-14 rounded-full flex items-center justify-center text-white text-lg font-semibold ${getAvatarColor(profile.name)}`}
              >
                {initials}
              </div>
              <div>
                {editing ? (
                  <div className="space-y-2">
                    <div>
                      <Label
                        htmlFor="edit-name"
                        className="text-xs text-memoir-text-muted"
                      >
                        Full name
                      </Label>
                      <Input
                        id="edit-name"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="mt-1 w-64"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") saveProfile();
                          if (e.key === "Escape") cancelEditing();
                        }}
                      />
                    </div>
                  </div>
                ) : (
                  <>
                    <h2 className="text-lg font-semibold text-memoir-text">
                      {profile.name}
                    </h2>
                    <p className="text-sm text-memoir-text-muted">
                      Member since {createdDate}
                    </p>
                  </>
                )}
              </div>
            </div>

            {!editing ? (
              <button
                onClick={startEditing}
                className="text-sm text-memoir-text-muted hover:text-memoir-text transition-colors"
              >
                <svg
                  className="w-4 h-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1.5}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10"
                  />
                </svg>
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={cancelEditing}
                  disabled={updateProfile.isPending}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={saveProfile}
                  disabled={updateProfile.isPending}
                >
                  {updateProfile.isPending ? "Saving..." : "Save"}
                </Button>
              </div>
            )}
          </div>

          {editError && (
            <p className="text-sm text-red-600 mb-4">{editError}</p>
          )}

          <div className="border-t border-memoir-border pt-4">
            {/* Email */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-memoir-text-muted uppercase tracking-wide">
                    Email
                  </p>
                  <div className="flex items-center gap-2 mt-1">
                    <p className="text-sm text-memoir-text">{profile.email}</p>
                    {profile.email_verified ? (
                      <Badge
                        variant="secondary"
                        className="bg-emerald-50 text-emerald-700 border-emerald-200 text-xs"
                      >
                        Verified
                      </Badge>
                    ) : (
                      <Badge
                        variant="secondary"
                        className="bg-amber-50 text-amber-700 border-amber-200 text-xs"
                      >
                        Unverified
                      </Badge>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {!profile.email_verified && (
                    <button
                      onClick={() => resendVerification.mutate()}
                      disabled={resendVerification.isPending}
                      className="text-xs text-memoir-text-muted hover:text-memoir-text transition-colors"
                    >
                      {resendVerification.isPending
                        ? "Sending..."
                        : "Resend verification"}
                    </button>
                  )}
                  {profile.has_password && (
                    <button
                      onClick={openEmailDialog}
                      className="text-xs text-memoir-text-muted hover:text-memoir-text transition-colors"
                    >
                      Change email
                    </button>
                  )}
                </div>
              </div>

              {resendMessage && (
                <p className="text-xs text-emerald-600">{resendMessage}</p>
              )}

              {/* OAuth providers */}
              {profile.oauth_providers.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-memoir-text-muted uppercase tracking-wide mt-4">
                    Connected accounts
                  </p>
                  <div className="flex gap-2 mt-1">
                    {profile.oauth_providers.map((provider) => (
                      <Badge key={provider} variant="outline" className="text-xs">
                        {provider.charAt(0).toUpperCase() + provider.slice(1)}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Change Password section */}
        {profile.has_password && (
          <div className="bg-memoir-surface border border-memoir-border rounded-card p-6">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-memoir-text">Password</h3>
                <p className="text-xs text-memoir-text-muted mt-0.5">
                  Update your password
                </p>
              </div>
              {!showPasswordForm && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setShowPasswordForm(true);
                    setPasswordError("");
                    setPasswordSuccess("");
                  }}
                >
                  Change password
                </Button>
              )}
            </div>

            {showPasswordForm && (
              <div className="mt-4 space-y-3 max-w-sm">
                <div>
                  <Label
                    htmlFor="current-password"
                    className="text-xs text-memoir-text-secondary"
                  >
                    Current password
                  </Label>
                  <Input
                    id="current-password"
                    type="password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    className="mt-1"
                  />
                </div>
                <div>
                  <Label
                    htmlFor="new-password"
                    className="text-xs text-memoir-text-secondary"
                  >
                    New password
                  </Label>
                  <Input
                    id="new-password"
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="mt-1"
                  />
                </div>
                <div>
                  <Label
                    htmlFor="confirm-password"
                    className="text-xs text-memoir-text-secondary"
                  >
                    Confirm new password
                  </Label>
                  <Input
                    id="confirm-password"
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="mt-1"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") submitPasswordChange();
                    }}
                  />
                </div>

                {passwordError && (
                  <p className="text-sm text-red-600">{passwordError}</p>
                )}
                {passwordSuccess && (
                  <p className="text-sm text-emerald-600">{passwordSuccess}</p>
                )}

                <div className="flex items-center gap-2 pt-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setShowPasswordForm(false);
                      setCurrentPassword("");
                      setNewPassword("");
                      setConfirmPassword("");
                      setPasswordError("");
                      setPasswordSuccess("");
                    }}
                    disabled={changePassword.isPending}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={submitPasswordChange}
                    disabled={changePassword.isPending}
                  >
                    {changePassword.isPending
                      ? "Updating..."
                      : "Update password"}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Change Email Dialog */}
        <Dialog open={emailDialogOpen} onOpenChange={setEmailDialogOpen}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Change email address</DialogTitle>
            </DialogHeader>

            {emailSuccess ? (
              <div className="py-4">
                <div className="w-10 h-10 bg-emerald-100 rounded-full flex items-center justify-center mx-auto mb-3">
                  <svg
                    className="w-5 h-5 text-emerald-600"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M5 13l4 4L19 7"
                    />
                  </svg>
                </div>
                <p className="text-sm text-memoir-text-secondary text-center">
                  {emailSuccess}
                </p>
                <DialogFooter className="mt-4">
                  <Button
                    variant="outline"
                    onClick={() => setEmailDialogOpen(false)}
                  >
                    Close
                  </Button>
                </DialogFooter>
              </div>
            ) : (
              <>
                <div className="space-y-3 py-2">
                  <div>
                    <Label htmlFor="new-email" className="text-xs text-memoir-text-secondary">
                      New email address
                    </Label>
                    <Input
                      id="new-email"
                      type="email"
                      value={newEmail}
                      onChange={(e) => setNewEmail(e.target.value)}
                      placeholder="new@example.com"
                      className="mt-1"
                    />
                  </div>
                  <div>
                    <Label
                      htmlFor="email-password"
                      className="text-xs text-memoir-text-secondary"
                    >
                      Current password
                    </Label>
                    <Input
                      id="email-password"
                      type="password"
                      value={emailPassword}
                      onChange={(e) => setEmailPassword(e.target.value)}
                      className="mt-1"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") submitEmailChange();
                      }}
                    />
                    <p className="text-xs text-memoir-text-muted mt-1">
                      Required for security
                    </p>
                  </div>

                  {emailError && (
                    <p className="text-sm text-red-600">{emailError}</p>
                  )}
                </div>

                <DialogFooter>
                  <Button
                    variant="outline"
                    onClick={() => setEmailDialogOpen(false)}
                    disabled={changeEmail.isPending}
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={submitEmailChange}
                    disabled={changeEmail.isPending}
                  >
                    {changeEmail.isPending
                      ? "Sending..."
                      : "Send verification"}
                  </Button>
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
