"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

type Preferences = Record<string, Record<string, boolean>>;

interface DigestSettings {
  id: string;
  job_type: string;
  frequency: string;
  preferred_time: string | null;
  preferred_day: number | null;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
}

interface SlackConfig {
  connected: boolean;
  workspace_name: string | null;
  channel_id: string | null;
  channel_name: string | null;
}

interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
}

const EVENT_LABELS: Record<string, { label: string; description: string }> = {
  transcription_complete: { label: "Call processed", description: "When a call transcript and AI analysis are ready" },
  participant_summary: { label: "Meeting participant summaries", description: "Receive email summaries for meetings you participated in" },
  intelligence_digest: { label: "Intelligence digest", description: "Periodic summary of intelligence insights" },
  faq_digest: { label: "FAQ digest", description: "Periodic summary of frequently asked questions" },
};

const FREQUENCY_OPTIONS = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "biweekly", label: "Biweekly" },
  { value: "monthly", label: "Monthly" },
];

const DAY_OPTIONS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
];

export default function NotificationsSettingsPage() {
  const activeTeamId = useAuthStore((s) => s.activeTeamId);

  const [preferences, setPreferences] = useState<Preferences>({});
  const [intellDigest, setIntellDigest] = useState<DigestSettings | null>(null);
  const [faqDigest, setFaqDigest] = useState<DigestSettings | null>(null);
  const [slackConfig, setSlackConfig] = useState<SlackConfig | null>(null);
  const [slackChannels, setSlackChannels] = useState<SlackChannel[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState<string | null>(null);
  const [sendingTest, setSendingTest] = useState(false);
  const [testResult, setTestResult] = useState<"success" | "error" | null>(null);

  const fetchData = useCallback(async () => {
    if (!activeTeamId) return;
    setLoading(true);
    try {
      const [prefs, intell, faq, slack] = await Promise.all([
        api.getNotificationPreferences(activeTeamId),
        api.getNotificationDigestSettings(activeTeamId, "intelligence_digest"),
        api.getNotificationDigestSettings(activeTeamId, "faq_digest"),
        api.getSlackNotificationConfig(activeTeamId),
      ]);
      setPreferences(prefs);
      setIntellDigest(intell as unknown as DigestSettings);
      setFaqDigest(faq as unknown as DigestSettings);
      setSlackConfig(slack);

      // Load channels if Slack is connected
      if (slack.connected) {
        try {
          const { channels } = await api.getSlackChannels();
          setSlackChannels(channels);
        } catch {
          // Slack channels fetch failed — non-critical
        }
      }
    } catch (err) {
      console.error("Failed to load notification settings:", err);
    } finally {
      setLoading(false);
    }
  }, [activeTeamId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const togglePreference = async (eventType: string, channel: string, enabled: boolean) => {
    if (!activeTeamId) return;
    // Optimistic update
    setPreferences((prev) => ({
      ...prev,
      [eventType]: { ...prev[eventType], [channel]: enabled },
    }));
    try {
      await api.updateNotificationPreference(activeTeamId, eventType, channel, enabled);
    } catch {
      // Revert
      setPreferences((prev) => ({
        ...prev,
        [eventType]: { ...prev[eventType], [channel]: !enabled },
      }));
    }
  };

  const updateDigest = async (
    jobType: string,
    data: { frequency?: string; preferred_day?: number; enabled?: boolean },
  ) => {
    if (!activeTeamId) return;
    try {
      const updated = await api.updateNotificationDigestSettings(activeTeamId, jobType, data) as unknown as DigestSettings;
      if (jobType === "intelligence_digest") setIntellDigest(updated);
      else setFaqDigest(updated);
    } catch (err) {
      console.error("Failed to update digest settings:", err);
    }
  };

  const handleGenerate = async (jobType: string) => {
    if (!activeTeamId) return;
    setGenerating(jobType);
    try {
      await api.generateNotificationDigest(activeTeamId, jobType);
    } catch (err) {
      console.error("Failed to generate digest:", err);
    } finally {
      setGenerating(null);
    }
  };

  const handleSendTestMessage = async () => {
    if (!activeTeamId) return;
    setSendingTest(true);
    setTestResult(null);
    try {
      await api.sendSlackTestMessage(activeTeamId);
      setTestResult("success");
    } catch {
      setTestResult("error");
    } finally {
      setSendingTest(false);
      setTimeout(() => setTestResult(null), 4000);
    }
  };

  const handleSlackChannelChange = async (channelId: string) => {
    if (!activeTeamId) return;
    const ch = slackChannels.find((c) => c.id === channelId);
    if (!ch) return;
    try {
      await api.updateSlackNotificationConfig(activeTeamId, channelId, ch.name);
      setSlackConfig((prev) => prev ? { ...prev, channel_id: channelId, channel_name: ch.name } : prev);
    } catch (err) {
      console.error("Failed to update Slack config:", err);
    }
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-memoir-text font-display">Notifications</h1>
            <p className="text-sm text-memoir-text-muted mt-1">Configure how and when Memoir notifies you.</p>
          </div>
        </div>
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
          <p className="text-sm text-memoir-text-muted">Loading settings...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-memoir-text font-display">Notifications</h1>
          <p className="text-sm text-memoir-text-muted mt-1">Configure how and when Memoir notifies you.</p>
        </div>
        <Link
          href="/settings/profile"
          className="text-sm text-memoir-indigo hover:text-memoir-indigo/80 font-medium transition-colors"
        >
          &larr; Back to profile
        </Link>
      </div>

      {/* Section 1: Event Notifications */}
      <section className="bg-memoir-surface border border-memoir-border rounded-card overflow-hidden">
        <div className="px-5 py-4 border-b border-memoir-border">
          <h2 className="text-sm font-semibold text-memoir-text">Event Notifications</h2>
          <p className="text-xs text-memoir-text-muted mt-0.5">Choose which events send you notifications.</p>
        </div>
        <div className="divide-y divide-memoir-border">
          {Object.entries(EVENT_LABELS).map(([eventType, { label, description }]) => (
            <div key={eventType} className="flex items-center justify-between px-5 py-3.5">
              <div className="flex-1 min-w-0 mr-4">
                <p className="text-sm font-medium text-memoir-text">{label}</p>
                <p className="text-xs text-memoir-text-muted">{description}</p>
              </div>
              <div className="flex items-center gap-6">
                {/* Email toggle */}
                <label className="flex items-center gap-2 cursor-pointer">
                  <span className="text-xs text-memoir-text-muted">Email</span>
                  <button
                    onClick={() => togglePreference(eventType, "email", !preferences[eventType]?.email)}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      preferences[eventType]?.email ? "bg-memoir-indigo" : "bg-memoir-border"
                    }`}
                  >
                    <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                      preferences[eventType]?.email ? "translate-x-[18px]" : "translate-x-[3px]"
                    }`} />
                  </button>
                </label>
                {/* Slack toggle */}
                <label className="flex items-center gap-2 cursor-pointer">
                  <span className="text-xs text-memoir-text-muted">Slack</span>
                  <button
                    onClick={() => togglePreference(eventType, "slack", !preferences[eventType]?.slack)}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      preferences[eventType]?.slack ? "bg-memoir-indigo" : "bg-memoir-border"
                    }`}
                    disabled={!slackConfig?.connected}
                    title={!slackConfig?.connected ? "Connect Slack first" : undefined}
                  >
                    <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                      preferences[eventType]?.slack ? "translate-x-[18px]" : "translate-x-[3px]"
                    }`} />
                  </button>
                </label>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Section 2: Digest Schedule */}
      <section>
        <h2 className="text-sm font-semibold text-memoir-text mb-3">Digest Schedule</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Intelligence Digest */}
          <DigestCard
            title="Intelligence Digest"
            settings={intellDigest}
            generating={generating === "intelligence_digest"}
            onUpdate={(data) => updateDigest("intelligence_digest", data)}
            onGenerate={() => handleGenerate("intelligence_digest")}
          />
          {/* FAQ Digest */}
          <DigestCard
            title="FAQ Digest"
            settings={faqDigest}
            generating={generating === "faq_digest"}
            onUpdate={(data) => updateDigest("faq_digest", data)}
            onGenerate={() => handleGenerate("faq_digest")}
          />
        </div>
      </section>

      {/* Section 3: Slack Configuration */}
      <section className="bg-memoir-surface border border-memoir-border rounded-card">
        <div className="px-5 py-4 border-b border-memoir-border">
          <h2 className="text-sm font-semibold text-memoir-text">Slack</h2>
          <p className="text-xs text-memoir-text-muted mt-0.5">Configure Slack notifications channel.</p>
        </div>
        <div className="px-5 py-4">
          {!slackConfig?.connected ? (
            <div className="flex items-center justify-between">
              <p className="text-sm text-memoir-text-muted">Slack is not connected.</p>
              <Link
                href="/integrations"
                className="text-sm font-medium text-memoir-indigo hover:text-memoir-indigo/80 transition-colors"
              >
                Connect Slack &rarr;
              </Link>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-memoir-text">
                Connected to <span className="font-medium">{slackConfig.workspace_name}</span>
              </p>
              <div className="flex items-center gap-3">
                <label className="text-sm text-memoir-text-muted whitespace-nowrap">Channel:</label>
                <select
                  value={slackConfig.channel_id || ""}
                  onChange={(e) => handleSlackChannelChange(e.target.value)}
                  className="flex-1 text-sm border border-memoir-border rounded-button px-3 py-1.5 bg-memoir-surface text-memoir-text focus:outline-none focus:ring-1 focus:ring-memoir-indigo"
                >
                  <option value="">Select a channel...</option>
                  {slackChannels.map((ch) => (
                    <option key={ch.id} value={ch.id}>
                      {ch.is_private ? "🔒 " : "#"}{ch.name}
                    </option>
                  ))}
                </select>
              </div>
              {slackConfig.channel_name && (
                <div className="flex items-center gap-3">
                  <p className="text-xs text-memoir-text-muted">
                    Notifications will be posted to <span className="font-medium">#{slackConfig.channel_name}</span>
                  </p>
                  <button
                    onClick={handleSendTestMessage}
                    disabled={sendingTest}
                    className="text-xs text-memoir-indigo hover:underline disabled:opacity-50"
                  >
                    {sendingTest ? "Sending..." : "Send Test Message"}
                  </button>
                  {testResult === "success" && (
                    <span className="text-xs text-green-600">Sent!</span>
                  )}
                  {testResult === "error" && (
                    <span className="text-xs text-memoir-error">Failed to send</span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}


// ─── Digest Card Component ──────────────────────────────────────

function DigestCard({
  title,
  settings,
  generating,
  onUpdate,
  onGenerate,
}: {
  title: string;
  settings: DigestSettings | null;
  generating: boolean;
  onUpdate: (data: { frequency?: string; preferred_day?: number; enabled?: boolean }) => void;
  onGenerate: () => void;
}) {
  if (!settings) return null;

  return (
    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-memoir-text">{title}</h3>
        <button
          onClick={() => onUpdate({ enabled: !settings.enabled })}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            settings.enabled ? "bg-memoir-indigo" : "bg-memoir-border"
          }`}
        >
          <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
            settings.enabled ? "translate-x-[18px]" : "translate-x-[3px]"
          }`} />
        </button>
      </div>

      <div className="space-y-3">
        {/* Frequency */}
        <div className="flex items-center gap-3">
          <label className="text-xs text-memoir-text-muted w-20">Frequency</label>
          <select
            value={settings.frequency}
            onChange={(e) => onUpdate({ frequency: e.target.value })}
            disabled={!settings.enabled}
            className="flex-1 text-sm border border-memoir-border rounded-button px-2.5 py-1.5 bg-memoir-surface text-memoir-text focus:outline-none focus:ring-1 focus:ring-memoir-indigo disabled:opacity-50"
          >
            {FREQUENCY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {/* Day (for weekly/biweekly) */}
        {(settings.frequency === "weekly" || settings.frequency === "biweekly") && (
          <div className="flex items-center gap-3">
            <label className="text-xs text-memoir-text-muted w-20">Day</label>
            <select
              value={settings.preferred_day ?? 0}
              onChange={(e) => onUpdate({ preferred_day: Number(e.target.value) })}
              disabled={!settings.enabled}
              className="flex-1 text-sm border border-memoir-border rounded-button px-2.5 py-1.5 bg-memoir-surface text-memoir-text focus:outline-none focus:ring-1 focus:ring-memoir-indigo disabled:opacity-50"
            >
              {DAY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
        )}

        {/* Status info */}
        <div className="pt-2 border-t border-memoir-border space-y-1">
          {settings.next_run_at && (
            <p className="text-xs text-memoir-text-muted">
              Next run: {new Date(settings.next_run_at).toLocaleString()}
            </p>
          )}
          {settings.last_run_at && (
            <p className="text-xs text-memoir-text-muted">
              Last run: {new Date(settings.last_run_at).toLocaleString()}
              {settings.last_status && (
                <span className={`ml-1 ${settings.last_status === "success" ? "text-green-600" : "text-memoir-error"}`}>
                  ({settings.last_status})
                </span>
              )}
            </p>
          )}
        </div>

        {/* Generate now */}
        <button
          onClick={onGenerate}
          disabled={generating}
          className="w-full text-sm font-medium text-memoir-indigo border border-memoir-indigo/30 rounded-button px-3 py-1.5 hover:bg-memoir-indigo/5 transition-colors disabled:opacity-50"
        >
          {generating ? "Generating..." : "Generate Now"}
        </button>
      </div>
    </div>
  );
}
