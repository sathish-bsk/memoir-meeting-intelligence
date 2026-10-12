"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

interface NotificationItem {
  id: string;
  event_type: string;
  title: string;
  body: string | null;
  link: string | null;
  channel: string | null;
  status: string;
  read: boolean;
  created_at: string | null;
}

interface NotificationDropdownProps {
  onClose: () => void;
  /** Receives the latest unread_count whenever the dropdown fetches or
   *  mutates read-state. Lets the bell badge stay in sync without the
   *  parent firing its own GET /api/notifications/log on close. */
  onUnreadCountChange?: (count: number) => void;
}

const EVENT_ICONS: Record<string, string> = {
  transcription_complete: "M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z",
  intelligence_digest: "M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5",
  faq_digest: "M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 5.25h.008v.008H12v-.008Z",
};

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "";
  const now = Date.now();
  const date = new Date(dateStr).getTime();
  const diff = now - date;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

export function NotificationDropdown({ onClose, onUnreadCountChange }: NotificationDropdownProps) {
  const router = useRouter();
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchNotifications = useCallback(async () => {
    if (!activeTeamId) return;
    try {
      const data = await api.getNotificationLog(activeTeamId, 20, 0);
      setItems(data.items);
      onUnreadCountChange?.(data.unread_count);
    } catch (err) {
      console.error("Failed to fetch notifications:", err);
    } finally {
      setLoading(false);
    }
  }, [activeTeamId, onUnreadCountChange]);

  useEffect(() => { fetchNotifications(); }, [fetchNotifications]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const handleItemClick = async (item: NotificationItem) => {
    if (!item.read) {
      try {
        await api.markNotificationsRead([item.id]);
        setItems((prev) => prev.map((n) => n.id === item.id ? { ...n, read: true } : n));
        // Optimistic badge decrement — avoids the parent's old refresh-
        // on-close GET. Server already updated by markNotificationsRead.
        onUnreadCountChange?.(
          items.reduce((c, n) => c + (!n.read && n.id !== item.id ? 1 : 0), 0)
        );
      } catch {
        // non-critical
      }
    }
    if (item.link) {
      router.push(item.link);
    }
    onClose();
  };

  const handleMarkAllRead = async () => {
    try {
      await api.markNotificationsRead(undefined, true);
      setItems((prev) => prev.map((n) => ({ ...n, read: true })));
      onUnreadCountChange?.(0);
    } catch {
      // non-critical
    }
  };

  return (
    <div
      ref={dropdownRef}
      className="absolute right-0 top-full mt-1 w-80 max-h-[420px] bg-memoir-surface border border-memoir-border rounded-card shadow-elevated z-50 flex flex-col"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-memoir-border">
        <h3 className="text-sm font-semibold text-memoir-text">Notifications</h3>
        <button
          onClick={handleMarkAllRead}
          className="text-xs text-memoir-indigo hover:text-memoir-indigo/80 font-medium transition-colors"
        >
          Mark all read
        </button>
      </div>

      {/* Items */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="px-4 py-8 text-center">
            <p className="text-xs text-memoir-text-muted">Loading...</p>
          </div>
        ) : items.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <p className="text-xs text-memoir-text-muted">No notifications yet</p>
          </div>
        ) : (
          items.map((item) => {
            const iconPath = EVENT_ICONS[item.event_type] || EVENT_ICONS.transcription_complete;
            return (
              <button
                key={item.id}
                onClick={() => handleItemClick(item)}
                className={`w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-memoir-bg transition-colors border-b border-memoir-border/50 last:border-b-0 ${
                  !item.read ? "bg-memoir-indigo/[0.03]" : ""
                }`}
              >
                {/* Icon */}
                <div className="flex-shrink-0 mt-0.5 w-8 h-8 rounded-full bg-memoir-indigo/10 flex items-center justify-center">
                  <svg className="w-4 h-4 text-memoir-indigo" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d={iconPath} />
                  </svg>
                </div>
                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className={`text-sm truncate ${!item.read ? "font-medium text-memoir-text" : "text-memoir-text-secondary"}`}>
                      {item.title}
                    </p>
                    {!item.read && (
                      <span className="flex-shrink-0 w-1.5 h-1.5 bg-memoir-indigo rounded-full" />
                    )}
                  </div>
                  {item.body && (
                    <p className="text-xs text-memoir-text-muted mt-0.5 line-clamp-2">{item.body}</p>
                  )}
                  <p className="text-[10px] text-memoir-text-muted mt-1">{timeAgo(item.created_at)}</p>
                </div>
              </button>
            );
          })
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-memoir-border px-4 py-2.5">
        <button
          onClick={() => { router.push("/settings/notifications"); onClose(); }}
          className="w-full text-xs text-memoir-indigo hover:text-memoir-indigo/80 font-medium text-center transition-colors"
        >
          Notification settings
        </button>
      </div>
    </div>
  );
}
