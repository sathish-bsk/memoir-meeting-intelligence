"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { CrmProperty /* CrmStage — commented out (Step 24) */ } from "@/lib/types";

const MEMOIR_FIELDS = [
  { key: "pain_points", label: "Pain Points", locked: false },
  { key: "next_steps", label: "Next Steps", locked: false },
  { key: "budget_range", label: "Budget Range", locked: false },
  { key: "decision_makers", label: "Decision Maker", locked: false },
  { key: "competitors_mentioned", label: "Competitors", locked: false },
  { key: "summary", label: "Call Summary", locked: true, hint: "Always pushed as Note/Call record" },
];

/* MEMOIR_STAGES — commented out, not yet fully implemented (Step 24)
const MEMOIR_STAGES = [
  "awareness",
  "interest",
  "consideration",
  "intent",
  "evaluation",
  "purchase",
];
*/

export default function FieldMappingPage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const provider = params.provider as string;
  const providerLabel = provider === "hubspot" ? "HubSpot" : "Zoho CRM";

  const [fieldMapping, setFieldMapping] = useState<Record<string, string>>({});
  /* Stage mapping commented out — not yet fully implemented (Step 24)
  const [stageMapping, setStageMapping] = useState<Record<string, string>>({});
  const [stageMappingLoaded, setStageMappingLoaded] = useState(false);
  */
  const [fieldMappingLoaded, setFieldMappingLoaded] = useState(false);

  // Fetch CRM connection status
  const { data: connection, isLoading: connectionLoading } = useQuery({
    queryKey: ["crm-status", provider],
    queryFn: () => api.getCrmStatus(provider),
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return count < 2;
    },
  });

  // Fetch CRM properties (contact fields)
  const { data: contactProperties } = useQuery({
    queryKey: ["crm-properties", provider, "contacts"],
    queryFn: () => api.getCrmProperties(provider, "contacts"),
    enabled: !!connection,
  });

  // Fetch CRM deal properties
  const { data: dealProperties } = useQuery({
    queryKey: ["crm-properties", provider, "deals"],
    queryFn: () => api.getCrmProperties(provider, "deals"),
    enabled: !!connection,
  });

  /* CRM pipeline stages query — commented out, not yet fully implemented (Step 24)
  const { data: crmStages } = useQuery({
    queryKey: ["crm-stages", provider],
    queryFn: () => api.getCrmStages(provider),
    enabled: !!connection,
  });
  */

  // Fetch current field mapping
  useQuery({
    queryKey: ["crm-field-mapping", provider],
    queryFn: () => api.getCrmFieldMapping(provider),
    enabled: !!connection && !fieldMappingLoaded,
    onSuccess: (data: Record<string, string>) => {
      setFieldMapping(data);
      setFieldMappingLoaded(true);
    },
  } as never);

  /* Stage mapping query commented out — not yet fully implemented (Step 24)
  useQuery({
    queryKey: ["crm-stage-mapping", provider],
    queryFn: () => api.getCrmStageMapping(provider),
    enabled: !!connection && !stageMappingLoaded,
    onSuccess: (data: Record<string, string>) => {
      setStageMapping(data);
      setStageMappingLoaded(true);
    },
  } as never);
  */

  const saveFieldMappingMutation = useMutation({
    mutationFn: () => api.saveCrmFieldMapping(provider, fieldMapping),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["crm-field-mapping", provider] }),
  });

  /* Stage mapping mutation commented out — not yet fully implemented (Step 24)
  const saveStageMappingMutation = useMutation({
    mutationFn: () => api.saveCrmStageMapping(provider, stageMapping),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["crm-stage-mapping", provider] }),
  });
  */

  // All CRM properties combined
  const allProperties: CrmProperty[] = [
    ...(contactProperties || []).map(p => ({ ...p, group: p.group || "Contact" })),
    ...(dealProperties || []).map(p => ({ ...p, group: p.group || "Deal", name: `deal_${p.name}` })),
  ];

  if (connectionLoading) {
    return (
      <div className="max-w-3xl mx-auto">
        <div className="animate-pulse space-y-4">
          <div className="h-6 bg-memoir-bg rounded w-1/3" />
          <div className="h-4 bg-memoir-bg rounded w-1/2" />
          <div className="h-64 bg-memoir-bg rounded" />
        </div>
      </div>
    );
  }

  if (!connection) {
    return (
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-bold text-memoir-text mb-2 font-display">{providerLabel} Field Mapping</h1>
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-6 text-center">
          <p className="text-sm text-memoir-text-muted mb-4">{providerLabel} is not connected.</p>
          <button
            onClick={() => router.push("/integrations")}
            className="px-4 py-2 text-sm font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90"
          >
            Go to Integrations
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <button onClick={() => router.push("/integrations")} className="text-memoir-text-muted hover:text-memoir-text transition-colors">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
        </button>
        <div>
          <h1 className="text-xl font-bold text-memoir-text font-display">{providerLabel} Field Mapping</h1>
          <p className="text-sm text-memoir-text-muted">Map Memoir data fields to {providerLabel} CRM properties</p>
        </div>
      </div>

      {/* Field Mapping Table */}
      <div className="bg-memoir-surface border border-memoir-border rounded-card p-5 mb-6">
        <h2 className="text-sm font-semibold text-memoir-text mb-4">Field Mapping</h2>
        <p className="text-xs text-memoir-text-muted mb-4">
          Choose which {providerLabel} field each Memoir data point should be written to.
        </p>

        <div className="space-y-3">
          {MEMOIR_FIELDS.map((field) => (
            <div key={field.key} className="flex items-center gap-3">
              <div className="w-40 flex-shrink-0">
                <span className="text-sm text-memoir-text">{field.label}</span>
                {field.locked && (
                  <span className="ml-1.5 text-[10px] text-memoir-text-muted">({field.hint})</span>
                )}
              </div>
              <div className="flex-1">
                {field.locked ? (
                  <div className="px-3 py-1.5 text-xs text-memoir-text-muted bg-memoir-bg border border-memoir-border rounded-button">
                    Pre-configured
                  </div>
                ) : (
                  <select
                    value={fieldMapping[field.key] || ""}
                    onChange={(e) => setFieldMapping({ ...fieldMapping, [field.key]: e.target.value })}
                    className="w-full px-3 py-1.5 text-xs border border-memoir-border rounded-button bg-memoir-surface text-memoir-text focus:ring-memoir-indigo/20 focus:border-memoir-indigo"
                  >
                    <option value="">Not mapped</option>
                    {allProperties.map((p) => (
                      <option key={p.name} value={p.name}>
                        {p.label} ({p.group})
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4 flex justify-end">
          <button
            onClick={() => saveFieldMappingMutation.mutate()}
            disabled={saveFieldMappingMutation.isPending}
            className="px-4 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90 transition-colors disabled:opacity-50"
          >
            {saveFieldMappingMutation.isPending ? "Saving..." : saveFieldMappingMutation.isSuccess ? "Saved" : "Save Field Mapping"}
          </button>
        </div>
      </div>

      {/* Stage Mapping Table — commented out, not yet fully implemented (Step 24)
      <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
        <h2 className="text-sm font-semibold text-memoir-text mb-4">Deal Stage Mapping</h2>
        <p className="text-xs text-memoir-text-muted mb-4">
          Map Memoir&apos;s deal stages to your {providerLabel} pipeline stages.
        </p>

        <div className="space-y-3">
          {MEMOIR_STAGES.map((stage) => (
            <div key={stage} className="flex items-center gap-3">
              <div className="w-40 flex-shrink-0">
                <span className="text-sm text-memoir-text capitalize">{stage}</span>
              </div>
              <div className="flex-1">
                <select
                  value={stageMapping[stage] || ""}
                  onChange={(e) => setStageMapping({ ...stageMapping, [stage]: e.target.value })}
                  className="w-full px-3 py-1.5 text-xs border border-memoir-border rounded-button bg-memoir-surface text-memoir-text focus:ring-memoir-indigo/20 focus:border-memoir-indigo"
                >
                  <option value="">Use default</option>
                  {(crmStages || []).map((s: CrmStage) => (
                    <option key={s.id} value={s.id}>
                      {s.label} ({s.pipeline})
                    </option>
                  ))}
                </select>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4 flex justify-end">
          <button
            onClick={() => saveStageMappingMutation.mutate()}
            disabled={saveStageMappingMutation.isPending}
            className="px-4 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90 transition-colors disabled:opacity-50"
          >
            {saveStageMappingMutation.isPending ? "Saving..." : saveStageMappingMutation.isSuccess ? "Saved" : "Save Stage Mapping"}
          </button>
        </div>
      </div>
      */}
    </div>
  );
}
