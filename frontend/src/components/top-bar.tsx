"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import { NotificationDropdown } from "./notification-dropdown";
import { CreateCallModal } from "./capture/create-call-modal";
import { UploadCallModal } from "./capture/upload-call-modal";
import { ScheduleMeetingModal } from "./capture/schedule-meeting-modal";
import { notifyMeetingsChanged } from "@/lib/utils";

interface TopBarProps {
  onToggleSidebar?: () => void;
  onSearchClick?: () => void;
  setupPill?: { count: number; total: number; onExpand: () => void } | null;
}

export function TopBar({ onToggleSidebar, onSearchClick, setupPill }: TopBarProps) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const teams = useAuthStore((s) => s.teams);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const switchTeam = useAuthStore((s) => s.switchTeam);
  const clearAuth = useAuthStore((s) => s.logout);

  const activeTeam = teams.find((t) => t.id === activeTeamId);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [showTeamMenu, setShowTeamMenu] = useState(false);
  const [showNotifications, setShowNotifications] = useState(false);
  const [showCaptureMenu, setShowCaptureMenu] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  // Capture flows wired to the same modals the /meetings page uses, so
  // creating a call or uploading a file from the topbar follows the
  // identical contract (no route detour, no duplicated form code).
  const [showCreateCallModal, setShowCreateCallModal] = useState(false);
  const [showUploadCallModal, setShowUploadCallModal] = useState(false);
  const [showScheduleMeetingModal, setShowScheduleMeetingModal] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const teamMenuRef = useRef<HTMLDivElement>(null);
  const notifRef = useRef<HTMLDivElement>(null);
  const captureMenuRef = useRef<HTMLDivElement>(null);

  // Close menus on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setShowUserMenu(false);
      }
      if (teamMenuRef.current && !teamMenuRef.current.contains(e.target as Node)) {
        setShowTeamMenu(false);
      }
      if (captureMenuRef.current && !captureMenuRef.current.contains(e.target as Node)) {
        setShowCaptureMenu(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Unread badge updates ONLY when the user opens the notification
  // dropdown — the dropdown's own fetch returns unread_count and feeds
  // it back via onUnreadCountChange. No fetch on mount, no fetch on
  // close. Trade-off: the badge starts at 0 each page load until the
  // user opens the bell once. Acceptable per product call (2026-05-29).

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch {
      // Server logout failed — still clear local state
    }
    clearAuth();
    router.push("/login");
  };

  const handleSwitchTeam = async (teamId: string) => {
    setShowTeamMenu(false);
    await switchTeam(teamId);
  };

  return (
    <header
      className="flex h-[58px] shrink-0 items-center border-b border-surface-border bg-white px-6"
    >
      {/* Left: hamburger (mobile only) + search */}
      <button
        onClick={onToggleSidebar}
        className="mr-3 rounded-button p-1.5 text-ink-secondary transition-colors hover:bg-surface-soft hover:text-ink lg:hidden"
        aria-label="Toggle sidebar"
      >
        <MenuIcon className="h-5 w-5" />
      </button>

      <button
        onClick={() => onSearchClick?.()}
        className="hidden sm:flex items-center gap-2 w-full max-w-sm bg-surface-soft border border-surface-border rounded-button px-3.5 py-2 text-sm text-ink-secondary hover:border-brand/30 transition-colors"
      >
        <SearchIcon className="h-4 w-4 flex-shrink-0" />
        <span className="flex-1 text-left">Search meetings, contacts, topics...</span>
        <kbd className="hidden lg:inline-flex items-center gap-0.5 text-[10px] font-mono text-ink-secondary bg-white border border-surface-border rounded px-1.5 py-0.5">
          <span className="text-xs">&#8984;</span>K
        </kbd>
      </button>

      <div className="flex-1" />

      {/* Right: capture + team + notifications + user menu */}
      <div className="flex items-center gap-2">
        {/* Capture dropdown (revamp v2 — quick-action launcher) */}
        <div className="relative" ref={captureMenuRef}>
          <button
            onClick={() => setShowCaptureMenu((v) => !v)}
            className="flex items-center gap-1.5 bg-brand text-white rounded-button px-3.5 py-1.5 text-sm font-semibold shadow-sm hover:bg-brand-dark transition-colors"
          >
            <VideoIcon className="w-4 h-4" />
            <span className="hidden sm:inline">Capture</span>
            <ChevronIcon className="w-3 h-3 opacity-80" />
          </button>
          {showCaptureMenu && (
            <div className="absolute right-0 top-full mt-1 w-56 bg-memoir-surface border border-memoir-border rounded-card shadow-elevated py-1.5 z-50">
              {[
                {
                  label: "Join live meeting",
                  icon: VideoIcon,
                  action: () => setShowCreateCallModal(true),
                },
                {
                  label: "Schedule new meeting",
                  icon: CalendarIcon,
                  action: () => setShowScheduleMeetingModal(true),
                },
                {
                  label: "Upload audio or video",
                  icon: UploadIcon,
                  action: () => setShowUploadCallModal(true),
                },
                {
                  label: "Start recording",
                  icon: RecordIcon,
                  action: () => router.push("/record"),
                },
              ].map((item) => (
                <button
                  key={item.label}
                  onClick={() => { setShowCaptureMenu(false); item.action(); }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-memoir-text hover:bg-memoir-bg transition-colors"
                >
                  <item.icon className="w-4 h-4 text-memoir-text-secondary" />
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="w-px h-6 bg-memoir-border mx-1" />

        {/* Team switcher — only renders when the user has > 1 team to switch between */}
        <div className="relative" ref={teamMenuRef} hidden={teams.length <= 1}>
          <button
            onClick={() => { if (teams.length > 1) setShowTeamMenu(!showTeamMenu); }}
            className="flex items-center gap-1.5 text-xs border border-memoir-border rounded-pill px-3 py-1.5 text-memoir-text-secondary hover:bg-memoir-bg transition-colors cursor-pointer"
          >
            <span className="max-w-[120px] truncate" title={activeTeam?.name || "Team"}>{activeTeam?.name || "Team"}</span>
            <ChevronIcon className="w-3.5 h-3.5" />
          </button>

          {showTeamMenu && teams.length > 1 && (
            <div className="absolute right-0 top-full mt-1 w-48 bg-memoir-surface border border-memoir-border rounded-card shadow-elevated py-1 z-50">
              {teams.map((t) => (
                <button
                  key={t.id}
                  onClick={() => handleSwitchTeam(t.id)}
                  className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                    t.id === activeTeamId
                      ? "bg-brand/10 text-brand font-medium"
                      : "text-memoir-text hover:bg-memoir-bg"
                  }`}
                >
                  {t.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Notification bell */}
        <div className="relative" ref={notifRef}>
          <button
            onClick={() => setShowNotifications(!showNotifications)}
            className="relative p-2 rounded-button text-memoir-text-secondary hover:bg-memoir-bg hover:text-memoir-text transition-colors"
            aria-label="Notifications"
          >
            <BellIcon className="w-5 h-5" />
            {unreadCount > 0 && (
              <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-memoir-error rounded-full" />
            )}
          </button>
          {showNotifications && (
            <NotificationDropdown
              onClose={() => setShowNotifications(false)}
              onUnreadCountChange={setUnreadCount}
            />
          )}
        </div>

        {/* Setup pill — shown when setup bar is dismissed but incomplete */}
        {setupPill && (
          <button
            onClick={setupPill.onExpand}
            className="flex items-center gap-1.5 text-xs border border-brand/30 bg-brand/5 text-brand rounded-pill px-2.5 py-1 hover:bg-brand/10 transition-colors"
          >
            <span className="text-[10px]">{"\u26A1"}</span>
            <span className="font-medium">Setup: {setupPill.count}/{setupPill.total}</span>
          </button>
        )}

        {/* User menu */}
        <div className="relative" ref={userMenuRef}>
          <button
            onClick={() => setShowUserMenu(!showUserMenu)}
            className="flex items-center gap-2 rounded-full p-0.5 transition-colors hover:bg-surface-soft"
            title={user?.name || "User"}
            aria-label="User menu"
          >
            <div
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
              style={{ background: "linear-gradient(135deg, #5C5CE0, #4848B8)" }}
            >
              {user?.name?.[0]?.toUpperCase() || "?"}
            </div>
          </button>

          {showUserMenu && (
            <div className="absolute right-0 top-full mt-1 w-52 bg-memoir-surface border border-memoir-border rounded-card shadow-elevated py-1 z-50">
              <div className="px-3 py-2 border-b border-memoir-border">
                <p className="text-sm font-medium text-memoir-text truncate" title={user?.name || "User"}>{user?.name || "User"}</p>
                <p className="text-xs text-memoir-text-muted truncate" title={user?.email || ""}>{user?.email || ""}</p>
              </div>
              <button
                onClick={() => { setShowUserMenu(false); router.push("/settings/profile"); }}
                className="w-full text-left px-3 py-2 text-sm text-memoir-text hover:bg-memoir-bg transition-colors"
              >
                Profile
              </button>
              <button
                onClick={handleLogout}
                className="w-full text-left px-3 py-2 text-sm text-memoir-error hover:bg-red-50 transition-colors"
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Capture flows — render here so the modals overlay the entire app */}
      {activeTeamId && (
        <>
          <CreateCallModal
            open={showCreateCallModal}
            onOpenChange={setShowCreateCallModal}
            teamId={activeTeamId}
            onCreated={() => { notifyMeetingsChanged(); router.push("/meetings"); }}
          />
          <UploadCallModal
            open={showUploadCallModal}
            onOpenChange={setShowUploadCallModal}
            teamId={activeTeamId}
            onCreated={() => { notifyMeetingsChanged(); router.push("/meetings"); }}
          />
        </>
      )}

      {/* Schedule Meeting modal — no team_id needed (just opens the
          calendar provider's create-event URL in a new tab). Available
          to any authenticated user. */}
      <ScheduleMeetingModal
        open={showScheduleMeetingModal}
        onOpenChange={setShowScheduleMeetingModal}
      />

    </header>
  );
}

// ─── Icons ───────────────────────────────────────────────────

function MenuIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
    </svg>
  );
}

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
    </svg>
  );
}

function BellIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0" />
    </svg>
  );
}

function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
    </svg>
  );
}

function VideoIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="6" width="13" height="12" rx="2" />
      <path d="M15 10l5-3v10l-5-3" />
    </svg>
  );
}

function CalendarIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  );
}

function UploadIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12" />
    </svg>
  );
}

function RecordIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="8" fill="#E5484D" />
      <circle cx="12" cy="12" r="10" stroke="#E5484D" strokeWidth="1.5" fill="none" />
    </svg>
  );
}
