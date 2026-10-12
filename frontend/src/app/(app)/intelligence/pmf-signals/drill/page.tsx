"use client";

import { Suspense, useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type {
  FeatureRequestAggregation,
  ObjectionAggregation,
  CompetitorAggregation,
} from "@/lib/types";
import DeepDiveLayout, {
  type DeepDiveItem,
} from "../../components/deep-dive-layout";

// ---------------------------------------------------------------------------
// Signal-type config
// ---------------------------------------------------------------------------

const SIGNAL_CONFIG: Record<
  string,
  {
    title: string;
    pageType: string;
    emptyMessage: string;
  }
> = {
  feature_demand: {
    title: "Feature Demand",
    pageType: "feature_requests",
    emptyMessage: "No feature requests detected yet.",
  },
  objections: {
    title: "Objection Frequency",
    pageType: "objections",
    emptyMessage: "No objections detected in your calls yet.",
  },
  competitors: {
    title: "Competitor Mentions",
    pageType: "competitors",
    emptyMessage: "No competitors mentioned in your calls yet.",
  },
};

// ---------------------------------------------------------------------------
// renderExtra per signal type
// ---------------------------------------------------------------------------

function FeatureDemandExtra({ item }: { item: DeepDiveItem }) {
  const breakdown = item.extra?.urgency_breakdown as
    | Record<string, number>
    | undefined;
  if (!breakdown || Object.keys(breakdown).length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(breakdown).map(([k, v]) => (
        <span
          key={k}
          className={`text-xs px-2 py-0.5 rounded-full ${
            k === "high" || k === "critical"
              ? "bg-red-50 text-red-600"
              : k === "medium"
                ? "bg-amber-50 text-amber-600"
                : "bg-memoir-bg text-memoir-text-muted"
          }`}
        >
          {k}: {v}
        </span>
      ))}
    </div>
  );
}

function ObjectionsExtra({ item }: { item: DeepDiveItem }) {
  const category = item.extra?.category as string | undefined;
  const resolutionRate = item.extra?.resolution_rate as number | undefined;
  if (!category && resolutionRate == null) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {category && (
        <span className="text-xs px-2 py-0.5 rounded-full bg-memoir-bg text-memoir-text-muted">
          {category}
        </span>
      )}
      {resolutionRate != null && (
        <span
          className={`text-xs px-2 py-0.5 rounded-full ${
            resolutionRate >= 0.7
              ? "bg-emerald-50 text-emerald-600"
              : resolutionRate >= 0.4
                ? "bg-amber-50 text-amber-600"
                : "bg-red-50 text-red-500"
          }`}
        >
          {Math.round(resolutionRate * 100)}% resolved
        </span>
      )}
    </div>
  );
}

function CompetitorsExtra({ item }: { item: DeepDiveItem }) {
  const sentiment = item.extra?.sentiment_breakdown as
    | Record<string, number>
    | undefined;
  const likes = (item.extra?.likes as string[]) || [];
  const dislikes = (item.extra?.dislikes as string[]) || [];

  return (
    <div className="space-y-3">
      {sentiment && Object.keys(sentiment).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(sentiment).map(([k, v]) => (
            <span
              key={k}
              className={`text-xs px-2 py-0.5 rounded-full ${
                k === "positive"
                  ? "bg-emerald-50 text-emerald-600"
                  : k === "negative"
                    ? "bg-red-50 text-red-500"
                    : "bg-memoir-bg text-memoir-text-muted"
              }`}
            >
              {k}: {v}
            </span>
          ))}
        </div>
      )}

      {(likes.length > 0 || dislikes.length > 0) && (
        <div className="grid grid-cols-1 gap-2">
          {likes.filter(Boolean).length > 0 && (
            <div>
              <p className="text-xs font-medium text-emerald-600 mb-1">
                What they like
              </p>
              {likes
                .filter(Boolean)
                .slice(0, 3)
                .map((l, li) => (
                  <p key={li} className="text-xs text-memoir-text-secondary">
                    + {l}
                  </p>
                ))}
            </div>
          )}
          {dislikes.filter(Boolean).length > 0 && (
            <div>
              <p className="text-xs font-medium text-red-500 mb-1">
                What they dislike
              </p>
              {dislikes
                .filter(Boolean)
                .slice(0, 3)
                .map((d, di) => (
                  <p key={di} className="text-xs text-memoir-text-secondary">
                    - {d}
                  </p>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main drill page content (inside Suspense)
// ---------------------------------------------------------------------------

function DrillContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);

  const signalType = searchParams.get("type") || "";
  const teamId = searchParams.get("team") || activeTeamId || "";

  const config = SIGNAL_CONFIG[signalType];

  const [items, setItems] = useState<DeepDiveItem[]>([]);
  const [totalCalls, setTotalCalls] = useState(0);
  const [loading, setLoading] = useState(true);
  const fetchedRef = useRef(false);

  // Role guard — members redirect to /intelligence
  useEffect(() => {
    if (user?.team_role === "member") {
      router.replace("/intelligence");
    }
  }, [user, router]);

  // Redirect if unknown signal type
  useEffect(() => {
    if (!config) {
      router.replace("/intelligence/pmf-signals");
    }
  }, [config, router]);

  useEffect(() => {
    if (!teamId || !config) return;
    fetchedRef.current = false;
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId, signalType]);

  async function loadData() {
    if (!teamId || !config || fetchedRef.current) return;
    fetchedRef.current = true;
    setLoading(true);
    try {
      const [rawData, overview] = await Promise.all([
        fetchSignalData(signalType, teamId),
        api.getIntelligenceOverview(teamId, 365),
      ]);
      setTotalCalls(overview.call_count);
      setItems(rawData);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  // Don't render for members
  if (user?.team_role === "member") return null;

  // Unknown signal type — will redirect
  if (!config) return null;

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto space-y-4">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="bg-memoir-surface border border-memoir-border rounded-card p-6 animate-pulse"
          >
            <div className="h-4 bg-memoir-bg rounded w-1/3 mb-3" />
            <div className="h-3 bg-memoir-bg rounded w-2/3" />
          </div>
        ))}
      </div>
    );
  }

  const renderExtra = (item: DeepDiveItem) => {
    switch (signalType) {
      case "feature_demand":
        return <FeatureDemandExtra item={item} />;
      case "objections":
        return <ObjectionsExtra item={item} />;
      case "competitors":
        return <CompetitorsExtra item={item} />;
      default:
        return null;
    }
  };

  return (
    <DeepDiveLayout
      title={config.title}
      backLabel="PMF Signals"
      backLink="/intelligence/pmf-signals"
      pageType={config.pageType}
      teamId={teamId || undefined}
      items={items}
      totalCalls={totalCalls}
      emptyMessage={config.emptyMessage}
      renderExtra={renderExtra}
    />
  );
}

// ---------------------------------------------------------------------------
// Data fetching per signal type
// ---------------------------------------------------------------------------

async function fetchSignalData(
  signalType: string,
  teamId: string,
): Promise<DeepDiveItem[]> {
  switch (signalType) {
    case "feature_demand": {
      const data = await api.getFeatureRequests(teamId, 365);
      return data.map((fr: FeatureRequestAggregation) => ({
        label: fr.feature,
        count: fr.request_count,
        mentions: fr.mentions,
        call_ids: fr.call_ids,
        extra: { urgency_breakdown: fr.urgency_breakdown },
      }));
    }
    case "objections": {
      const data = await api.getObjections(teamId, 365);
      return data.map((obj: ObjectionAggregation) => ({
        label: obj.objection,
        count: obj.count,
        mentions: obj.mentions,
        call_ids: obj.call_ids,
        extra: { category: obj.category, resolution_rate: obj.resolution_rate },
      }));
    }
    case "competitors": {
      const data = await api.getCompetitors(teamId, 365);
      return data.map((c: CompetitorAggregation) => ({
        label: c.competitor,
        count: c.mention_count,
        mentions: c.mentions,
        call_ids: c.call_ids,
        extra: {
          sentiment_breakdown: c.sentiment_breakdown,
          likes: c.likes,
          dislikes: c.dislikes,
        },
      }));
    }
    default:
      return [];
  }
}

// ---------------------------------------------------------------------------
// Page export with Suspense wrapper (required for useSearchParams)
// ---------------------------------------------------------------------------

export default function PMFSignalsDrillPage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-4xl mx-auto space-y-4">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-memoir-surface border border-memoir-border rounded-card p-6 animate-pulse"
            >
              <div className="h-4 bg-memoir-bg rounded w-1/3 mb-3" />
              <div className="h-3 bg-memoir-bg rounded w-2/3" />
            </div>
          ))}
        </div>
      }
    >
      <DrillContent />
    </Suspense>
  );
}
