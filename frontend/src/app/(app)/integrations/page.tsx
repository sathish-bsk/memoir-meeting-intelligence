"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

// ─── Reusable Connected Card ────────────────────────────────────────

function ConnectedCard({
  name,
  description,
  icon,
  onDisconnect,
  disconnectPending,
  details,
  actions,
  tokenInvalid,
  onReconnect,
  reconnectPending,
}: {
  name: string;
  description: string;
  icon: React.ReactNode;
  onDisconnect?: () => void;
  disconnectPending?: boolean;
  details?: React.ReactNode;
  actions?: React.ReactNode;
  /** When true, the provider's stored OAuth refresh token is no longer
   *  accepted. Card shows a "Reconnect required" banner + a Reconnect
   *  button that re-runs the OAuth flow. */
  tokenInvalid?: boolean;
  onReconnect?: () => void;
  reconnectPending?: boolean;
}) {
  return (
    <div
      className={`rounded-card border p-5 ${
        tokenInvalid
          ? "border-amber-300 bg-amber-50/40"
          : "border-memoir-border bg-memoir-surface"
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          {icon}
          <div>
            <h3 className="text-sm font-semibold text-memoir-text">{name}</h3>
            <p className="text-xs text-memoir-text-muted mt-0.5">{description}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {tokenInvalid ? (
            <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-amber-100 text-amber-800 border border-amber-300">
              Reconnect required
            </span>
          ) : (
            <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
              Connected
            </span>
          )}
        </div>
      </div>

      {tokenInvalid && (
        <div className="mt-3 rounded-button bg-amber-100/70 border border-amber-200 px-3 py-2 text-xs text-amber-900">
          Your {name} connection has expired. We can&apos;t sync new events until you re-authenticate.
        </div>
      )}

      {details && (
        <div className="mt-4 pt-4 border-t border-memoir-border">
          {details}
        </div>
      )}

      <div className="mt-4 flex items-center gap-2">
        {tokenInvalid && onReconnect && (
          <button
            onClick={onReconnect}
            disabled={reconnectPending}
            className="px-3 py-1.5 text-xs font-bold text-white bg-amber-600 hover:bg-amber-700 rounded-button transition-colors disabled:opacity-50"
          >
            {reconnectPending ? "Opening Google..." : "Reconnect"}
          </button>
        )}
        {!tokenInvalid && actions}
        {onDisconnect && (
          <button
            onClick={onDisconnect}
            disabled={disconnectPending}
            className="px-3 py-1.5 text-xs font-medium text-memoir-error border border-red-200 rounded-button hover:bg-red-50 transition-colors disabled:opacity-50"
          >
            {disconnectPending ? "Disconnecting..." : "Disconnect"}
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Reusable Available Card (grid) ────────────────────────────────

function AvailableCard({
  name,
  description,
  icon,
  comingSoon,
  onConnect,
  connectPending,
  loading,
  connectError,
  footnote,
}: {
  name: string;
  description: string;
  icon: React.ReactNode;
  comingSoon?: boolean;
  onConnect?: () => void;
  connectPending?: boolean;
  loading?: boolean;
  connectError?: string | null;
  footnote?: string;
}) {
  return (
    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5 flex flex-col">
      <div className="flex items-center gap-3 mb-3">
        {icon}
        <div>
          <h3 className="text-sm font-semibold text-memoir-text">{name}</h3>
          <p className="text-xs text-memoir-text-muted mt-0.5">{description}</p>
        </div>
      </div>
      <div className="mt-auto pt-2">
        {comingSoon ? (
          <span className="inline-block text-xs font-medium px-3 py-1.5 rounded-button bg-memoir-bg text-memoir-text-muted border border-memoir-border">
            Coming soon
          </span>
        ) : (
          <button
            onClick={onConnect}
            disabled={connectPending || loading}
            className="px-3 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90 transition-colors disabled:opacity-50"
          >
            {connectPending ? "Redirecting..." : `Connect ${name}`}
          </button>
        )}
        {connectError && (
          <p className="text-xs text-memoir-error mt-1.5">{connectError}</p>
        )}
        {footnote && (
          <p className="text-[10px] text-memoir-text-muted mt-1.5">{footnote}</p>
        )}
      </div>
    </div>
  );
}

// ─── Icons ─────────────────────────────────────────────────────────

function BrandBadge({ children }: { children: React.ReactNode }) {
  return (
    <div className="w-10 h-10 bg-white border border-memoir-border rounded-lg flex items-center justify-center flex-shrink-0 p-1.5">
      {children}
    </div>
  );
}

function CalendarIcon() {
  return (
    <BrandBadge>
      <svg className="w-full h-full" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
        <path fill="#fff" d="M195.368 60.632H60.632v134.736h134.736z" />
        <path fill="#ea4335" d="M195.368 256L256 195.368l-30.316-5.172l-30.316 5.172l-5.533 27.73z" />
        <path fill="#188038" d="M0 195.368v40.421C0 246.956 9.044 256 20.21 256h40.422l6.225-30.316l-6.225-30.316l-33.033-5.172z" />
        <path fill="#1967d2" d="M256 60.632V20.21C256 9.044 246.956 0 235.79 0h-40.422q-5.532 22.554-5.533 33.196q0 10.641 5.533 27.436q20.115 5.76 30.316 5.76T256 60.631" />
        <path fill="#fbbc04" d="M256 60.632h-60.632v134.736H256z" />
        <path fill="#34a853" d="M195.368 195.368H60.632V256h134.736z" />
        <path fill="#4285f4" d="M195.368 0H20.211C9.044 0 0 9.044 0 20.21v175.158h60.632V60.632h134.736z" />
        <path fill="#4285f4" d="M88.27 165.154c-5.036-3.402-8.523-8.37-10.426-14.94l11.689-4.816q1.59 6.063 5.558 9.398c2.627 2.223 5.827 3.318 9.566 3.318q5.734 0 9.852-3.487c2.746-2.324 4.127-5.288 4.127-8.875q0-5.508-4.345-8.994c-2.897-2.324-6.535-3.486-10.88-3.486h-6.754v-11.57h6.063q5.608 0 9.448-3.033c2.56-2.02 3.84-4.783 3.84-8.303c0-3.132-1.145-5.625-3.435-7.494c-2.29-1.87-5.188-2.813-8.708-2.813c-3.436 0-6.164.91-8.185 2.745a16.1 16.1 0 0 0-4.413 6.754l-11.57-4.817c1.532-4.345 4.345-8.185 8.471-11.503s9.398-4.985 15.798-4.985c4.733 0 8.994.91 12.767 2.745c3.772 1.836 6.736 4.379 8.875 7.613c2.14 3.25 3.2 6.888 3.2 10.93c0 4.126-.993 7.613-2.98 10.476s-4.43 5.052-7.327 6.585v.69a22.25 22.25 0 0 1 9.398 7.327c2.442 3.284 3.672 7.208 3.672 11.79c0 4.58-1.163 8.673-3.487 12.26c-2.324 3.588-5.54 6.417-9.617 8.472c-4.092 2.055-8.69 3.1-13.793 3.1c-5.912.016-11.369-1.685-16.405-5.087m71.797-58.005l-12.833 9.28l-6.417-9.734l23.023-16.607h8.825v78.333h-12.598z" />
      </svg>
    </BrandBadge>
  );
}

function OutlookIcon() {
  return (
    <BrandBadge>
      <svg className="w-full h-full" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
        <path fill="#0072c6" d="M19.484 7.937v5.477l1.916 1.205a.5.5 0 0 0 .21 0l8.238-5.554a1.174 1.174 0 0 0-.959-1.128Z" />
        <path fill="#0072c6" d="m19.484 15.457l1.747 1.2a.52.52 0 0 0 .543 0c-.3.181 8.073-5.378 8.073-5.378v10.066a1.408 1.408 0 0 1-1.49 1.555h-8.874zm-9.044-2.525a1.61 1.61 0 0 0-1.42.838a4.13 4.13 0 0 0-.526 2.218A4.05 4.05 0 0 0 9.02 18.2a1.6 1.6 0 0 0 2.771.022a4 4 0 0 0 .515-2.2a4.37 4.37 0 0 0-.5-2.281a1.54 1.54 0 0 0-1.366-.809" />
        <path fill="#0072c6" d="M2.153 5.155v21.427L18.453 30V2Zm10.908 14.336a3.23 3.23 0 0 1-2.7 1.361a3.19 3.19 0 0 1-2.64-1.318A5.46 5.46 0 0 1 6.706 16.1a5.87 5.87 0 0 1 1.036-3.616a3.27 3.27 0 0 1 2.744-1.384a3.12 3.12 0 0 1 2.61 1.321a5.64 5.64 0 0 1 1 3.484a5.76 5.76 0 0 1-1.035 3.586" />
      </svg>
    </BrandBadge>
  );
}

function ZoomIcon() {
  return (
    <BrandBadge>
      <svg className="w-full h-full" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="zoom-grad" x1="23.666%" x2="76.334%" y1="95.612%" y2="4.388%">
            <stop offset="0%" stopColor="#0845bf" />
            <stop offset="19.11%" stopColor="#0950de" />
            <stop offset="38.23%" stopColor="#0b59f6" />
            <stop offset="50%" stopColor="#0b5cff" />
            <stop offset="67.32%" stopColor="#0e5efe" />
            <stop offset="77.74%" stopColor="#1665fc" />
            <stop offset="86.33%" stopColor="#246ff9" />
            <stop offset="93.88%" stopColor="#387ff4" />
            <stop offset="100%" stopColor="#4f90ee" />
          </linearGradient>
        </defs>
        <path fill="url(#zoom-grad)" d="M256 128c0 13.568-1.024 27.136-3.328 40.192c-6.912 43.264-41.216 77.568-84.48 84.48C155.136 254.976 141.568 256 128 256s-27.136-1.024-40.192-3.328c-43.264-6.912-77.568-41.216-84.48-84.48C1.024 155.136 0 141.568 0 128s1.024-27.136 3.328-40.192c6.912-43.264 41.216-77.568 84.48-84.48C100.864 1.024 114.432 0 128 0s27.136 1.024 40.192 3.328c43.264 6.912 77.568 41.216 84.48 84.48C254.976 100.864 256 114.432 256 128" />
        <path fill="#fff" d="M204.032 207.872H75.008c-8.448 0-16.64-4.608-20.48-12.032c-4.608-8.704-2.816-19.2 4.096-26.112l89.856-89.856H83.968c-17.664 0-32-14.336-32-32h118.784c8.448 0 16.64 4.608 20.48 12.032c4.608 8.704 2.816 19.2-4.096 26.112l-89.6 90.112h74.496c17.664 0 32 14.08 32 31.744" />
      </svg>
    </BrandBadge>
  );
}

function SlackIcon() {
  return (
    <BrandBadge>
      <svg className="w-full h-full" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
        <path fill="#e01e5a" d="M53.841 161.32c0 14.832-11.987 26.82-26.819 26.82S.203 176.152.203 161.32c0-14.831 11.987-26.818 26.82-26.818H53.84zm13.41 0c0-14.831 11.987-26.818 26.819-26.818s26.819 11.987 26.819 26.819v67.047c0 14.832-11.987 26.82-26.82 26.82c-14.83 0-26.818-11.988-26.818-26.82z" />
        <path fill="#36c5f0" d="M94.07 53.638c-14.832 0-26.82-11.987-26.82-26.819S79.239 0 94.07 0s26.819 11.987 26.819 26.819v26.82zm0 13.613c14.832 0 26.819 11.987 26.819 26.819s-11.987 26.819-26.82 26.819H26.82C11.987 120.889 0 108.902 0 94.069c0-14.83 11.987-26.818 26.819-26.818z" />
        <path fill="#2eb67d" d="M201.55 94.07c0-14.832 11.987-26.82 26.818-26.82s26.82 11.988 26.82 26.82s-11.988 26.819-26.82 26.819H201.55zm-13.41 0c0 14.832-11.988 26.819-26.82 26.819c-14.831 0-26.818-11.987-26.818-26.82V26.82C134.502 11.987 146.489 0 161.32 0s26.819 11.987 26.819 26.819z" />
        <path fill="#ecb22e" d="M161.32 201.55c14.832 0 26.82 11.987 26.82 26.818s-11.988 26.82-26.82 26.82c-14.831 0-26.818-11.988-26.818-26.82V201.55zm0-13.41c-14.831 0-26.818-11.988-26.818-26.82c0-14.831 11.987-26.818 26.819-26.818h67.25c14.832 0 26.82 11.987 26.82 26.819s-11.988 26.819-26.82 26.819z" />
      </svg>
    </BrandBadge>
  );
}

function HubSpotIcon() {
  return (
    <BrandBadge>
      <svg className="w-full h-full" viewBox="0 0 24 24" fill="#ff7a59" xmlns="http://www.w3.org/2000/svg">
        <path d="M18.164 7.93V5.084a2.2 2.2 0 0 0 1.267-1.978v-.067A2.2 2.2 0 0 0 17.238.845h-.067a2.2 2.2 0 0 0-2.193 2.193v.067a2.2 2.2 0 0 0 1.252 1.973l.013.006v2.852a6.2 6.2 0 0 0-2.969 1.31l.012-.01l-7.828-6.095A2.497 2.497 0 1 0 4.3 4.656l-.012.006l7.697 5.991a6.2 6.2 0 0 0-1.038 3.446a6.2 6.2 0 0 0 1.147 3.607l-.013-.02l-2.342 2.343a2 2 0 0 0-.58-.095h-.002a2.033 2.033 0 1 0 2.033 2.033a2 2 0 0 0-.1-.595l.005.014l2.317-2.317a6.247 6.247 0 1 0 4.782-11.134l-.036-.005zm-.964 9.378a3.206 3.206 0 1 1 3.215-3.207v.002a3.206 3.206 0 0 1-3.207 3.207z" />
      </svg>
    </BrandBadge>
  );
}

function ZohoCrmIcon() {
  return (
    <BrandBadge>
      <svg className="w-full h-full" viewBox="0 0 24 24" fill="#c8202f" xmlns="http://www.w3.org/2000/svg">
        <path d="M8.66 6.897a1.3 1.3 0 0 0-1.205.765l-.642 1.44l-.062-.385A1.29 1.29 0 0 0 5.27 7.648l-4.185.678A1.29 1.29 0 0 0 .016 9.807l.678 4.18a1.29 1.29 0 0 0 1.27 1.087c.074 0 .143-.01.216-.017l4.18-.678c.436-.07.784-.351.96-.723l2.933 1.307a1.3 1.3 0 0 0 .988.026c.321-.12.575-.365.716-.678l.28-.629l.038.276a1.297 1.297 0 0 0 1.455 1.103l3.712-.501a1.29 1.29 0 0 0 1.03.514h4.236c.713 0 1.29-.58 1.291-1.291V9.545c0-.712-.58-1.291-1.291-1.291h-4.236q-.118 0-.23.022a1.3 1.3 0 0 0-.275-.288a1.28 1.28 0 0 0-.958-.253l-4.197.571q-.233.033-.432.14L9.159 7.01a1.3 1.3 0 0 0-.499-.113m-.025.705c.077 0 .159.013.24.052l2.971 1.324c-.128.238-.18.508-.142.782l.357 2.596h.002l-.745 1.672a.59.59 0 0 1-.777.296l-3.107-1.385l-.004-.041l-.41-2.526L8.1 7.95a.59.59 0 0 1 .536-.348zm-3.159.733c.125 0 .245.039.343.112c.13.09.21.227.237.382l.234 1.446l-.56 1.259a1.27 1.27 0 0 0-.026.987c.12.322.364.575.678.717l.295.131a.59.59 0 0 1-.428.314l-4.185.678a.59.59 0 0 1-.674-.485l-.678-4.18a.59.59 0 0 1 .485-.674l4.185-.678c.03-.004.064-.01.094-.01zm11.705.09a.6.6 0 0 1 .415.173a1.3 1.3 0 0 0-.416.947v4.237q.001.05.005.097l-3.55.482a.586.586 0 0 1-.66-.502l-.191-1.403l.899-2.017a1.29 1.29 0 0 0-.333-1.5l3.754-.51q.038-.005.077-.004m1.3.532h4.227c.326 0 .588.266.588.588v4.237a.59.59 0 0 1-.588.588h-4.237a.6.6 0 0 1-.12-.013c.47-.246.758-.765.684-1.318zm-5.988.309l.254.113c.296.133.43.48.296.777l-.432.97l-.207-1.465a.58.58 0 0 1 .09-.395zm5.39.538l.453 3.325a.583.583 0 0 1-.453.65zM6.496 11.545l.17 1.052a.59.59 0 0 1-.293-.776zm3.985 4.344a.59.59 0 0 0-.612.603c0 .358.244.61.601.61a.58.58 0 0 0 .607-.608c0-.35-.242-.605-.596-.605m5.545 0a.59.59 0 0 0-.612.603c0 .358.245.61.602.61a.58.58 0 0 0 .606-.608c0-.35-.24-.605-.596-.605" />
      </svg>
    </BrandBadge>
  );
}

function TeamsIcon() {
  return (
    <BrandBadge>
      <svg className="w-full h-full" viewBox="0 0 256 239" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="teams-grad" x1="17.372%" x2="82.628%" y1="-6.51%" y2="106.51%">
            <stop offset="0%" stopColor="#5a62c3" />
            <stop offset="50%" stopColor="#4d55bd" />
            <stop offset="100%" stopColor="#3940ab" />
          </linearGradient>
        </defs>
        <path fill="#5059c9" d="M178.563 89.302h66.125c6.248 0 11.312 5.065 11.312 11.312v60.231c0 22.96-18.613 41.574-41.573 41.574h-.197c-22.96.003-41.576-18.607-41.579-41.568V95.215a5.91 5.91 0 0 1 5.912-5.913" />
        <circle cx="223.256" cy="50.605" r="26.791" fill="#5059c9" />
        <circle cx="139.907" cy="38.698" r="38.698" fill="#7b83eb" />
        <path fill="#7b83eb" d="M191.506 89.302H82.355c-6.173.153-11.056 5.276-10.913 11.449v68.697c-.862 37.044 28.445 67.785 65.488 68.692c37.043-.907 66.35-31.648 65.489-68.692v-68.697c.143-6.173-4.74-11.296-10.913-11.449" />
        <path d="M142.884 89.302v96.268a10.96 10.96 0 0 1-6.787 10.062c-1.3.55-2.697.833-4.108.833H76.68c-.774-1.965-1.488-3.93-2.084-5.953a72.5 72.5 0 0 1-3.155-21.076v-68.703c-.143-6.163 4.732-11.278 10.895-11.43z" opacity=".1" />
        <path fill="url(#teams-grad)" d="M10.913 53.581h109.15c6.028 0 10.914 4.886 10.914 10.913v109.151c0 6.027-4.886 10.913-10.913 10.913H10.913C4.886 184.558 0 179.672 0 173.645V64.495C0 58.466 4.886 53.58 10.913 53.58" />
        <path fill="#fff" d="M94.208 95.125h-21.82v59.416H58.487V95.125H36.769V83.599h57.439z" />
      </svg>
    </BrandBadge>
  );
}

// ─── Main Content ──────────────────────────────────────────────────

function IntegrationsContent() {
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [callbackError, setCallbackError] = useState<string | null>(null);
  const callbackHandled = useRef(false);

  // ─── Calendar queries ─────────────────────────────────────────────

  const {
    data: googleConnection,
    isLoading: googleLoading,
    error: googleStatusError,
  } = useQuery({
    queryKey: ["calendar-status", "google"],
    queryFn: () => api.getCalendarStatus("google"),
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return count < 2;
    },
  });

  const {
    data: outlookConnection,
    isLoading: outlookLoading,
    error: outlookStatusError,
  } = useQuery({
    queryKey: ["calendar-status", "outlook"],
    queryFn: () => api.getCalendarStatus("outlook"),
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return count < 2;
    },
  });

  const googleConnected = !!googleConnection && !(googleStatusError instanceof ApiError && googleStatusError.status === 404);
  const outlookConnected = !!outlookConnection && !(outlookStatusError instanceof ApiError && outlookStatusError.status === 404);

  // ─── Slack queries ────────────────────────────────────────────────

  const {
    data: slackConnection,
    isLoading: slackLoading,
    error: slackStatusError,
  } = useQuery({
    queryKey: ["slack-status"],
    queryFn: () => api.getSlackStatus(),
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return count < 2;
    },
  });

  const slackConnected = !!slackConnection && !(slackStatusError instanceof ApiError && slackStatusError.status === 404);

  // Slack channels query kept for potential future use but not shown on integrations page
  // Channel selection is managed in Settings > Notifications

  // ─── Zoom queries ───────────────────────────────────────────────

  const {
    data: zoomConnection,
    isLoading: zoomLoading,
    error: zoomStatusError,
  } = useQuery({
    queryKey: ["zoom-status"],
    queryFn: () => api.getZoomStatus(),
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return count < 2;
    },
  });

  const zoomConnected = !!zoomConnection && !(zoomStatusError instanceof ApiError && zoomStatusError.status === 404);

  // ─── Teams Cloud queries ─────────────────────────────────────

  const {
    data: teamsConnection,
    isLoading: teamsLoading,
    error: teamsStatusError,
  } = useQuery({
    queryKey: ["teams-cloud-status"],
    queryFn: () => api.getTeamsCloudStatus(),
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return count < 2;
    },
  });

  const teamsConnected = !!teamsConnection && !(teamsStatusError instanceof ApiError && teamsStatusError.status === 404);

  // ─── CRM queries ──────────────────────────────────────────────────

  const {
    data: hubspotConnection,
    isLoading: hubspotLoading,
    error: hubspotStatusError,
  } = useQuery({
    queryKey: ["crm-status", "hubspot"],
    queryFn: () => api.getCrmStatus("hubspot"),
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return count < 2;
    },
  });

  const hubspotConnected = !!hubspotConnection && !(hubspotStatusError instanceof ApiError && hubspotStatusError.status === 404);

  const {
    data: zohoConnection,
    isLoading: zohoLoading,
    error: zohoStatusError,
  } = useQuery({
    queryKey: ["crm-status", "zoho"],
    queryFn: () => api.getCrmStatus("zoho"),
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return count < 2;
    },
  });

  const zohoConnected = !!zohoConnection && !(zohoStatusError instanceof ApiError && zohoStatusError.status === 404);

  // ─── All mutations ────────────────────────────────────────────────

  const googleConnectMutation = useMutation({
    mutationFn: () => api.connectGoogleCalendar(),
    onSuccess: (data) => { window.location.href = data.auth_url; },
  });

  const googleDisconnectMutation = useMutation({
    mutationFn: () => api.disconnectCalendar("google"),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["calendar-status", "google"] }); },
  });

  const googleSyncMutation = useMutation({
    mutationFn: () => api.syncCalendar("google"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-status", "google"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-events"] });
    },
    onError: () => {
      // 401 with {error:"token_invalid"} means the server marked the
      // connection token_invalid=true. Refetch status so the card flips
      // to the "Reconnect required" treatment automatically.
      queryClient.invalidateQueries({ queryKey: ["calendar-status", "google"] });
    },
  });

  const outlookConnectMutation = useMutation({
    mutationFn: () => api.connectOutlookCalendar(),
    onSuccess: (data) => { window.location.href = data.auth_url; },
  });

  const outlookDisconnectMutation = useMutation({
    mutationFn: () => api.disconnectCalendar("outlook"),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["calendar-status", "outlook"] }); },
  });

  const outlookSyncMutation = useMutation({
    mutationFn: () => api.syncCalendar("outlook"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-status", "outlook"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-events"] });
    },
    onError: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-status", "outlook"] });
    },
  });

  const googleAutoRecordMutation = useMutation({
    mutationFn: (enabled: boolean) => api.updateCalendarAutoRecord(googleConnection?.id || "", enabled),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["calendar-status", "google"] }); },
  });

  const outlookAutoRecordMutation = useMutation({
    mutationFn: (enabled: boolean) => api.updateCalendarAutoRecord(outlookConnection?.id || "", enabled),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["calendar-status", "outlook"] }); },
  });

  const zoomConnectMutation = useMutation({
    mutationFn: () => api.connectZoom(),
    onSuccess: (data) => { window.location.href = data.auth_url; },
  });

  const zoomDisconnectMutation = useMutation({
    mutationFn: () => api.disconnectZoom(),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["zoom-status"] }); },
  });

  const zoomSettingsMutation = useMutation({
    mutationFn: (settings: { auto_import?: boolean; recording_preference?: string }) =>
      api.updateZoomSettings(settings),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["zoom-status"] }); },
  });

  const teamsConnectMutation = useMutation({
    mutationFn: () => api.connectTeamsCloud(),
    onSuccess: (data) => { window.location.href = data.auth_url; },
  });

  const teamsDisconnectMutation = useMutation({
    mutationFn: () => api.disconnectTeamsCloud(),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["teams-cloud-status"] }); },
  });

  const teamsSettingsMutation = useMutation({
    mutationFn: (settings: { auto_import?: boolean; recording_preference?: string }) =>
      api.updateTeamsCloudSettings(settings),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["teams-cloud-status"] }); },
  });

  const [teamsAdminConsentPending, setTeamsAdminConsentPending] = useState(false);
  const handleTeamsAdminConsent = async () => {
    setTeamsAdminConsentPending(true);
    try {
      const data = await api.getTeamsAdminConsentUrl();
      window.open(data.admin_consent_url, "_blank", "width=600,height=700");
    } catch {
      // silent — user will see the connection state
    } finally {
      setTeamsAdminConsentPending(false);
    }
  };

  const slackConnectMutation = useMutation({
    mutationFn: () => api.connectSlack(),
    onSuccess: (data) => { window.location.href = data.auth_url; },
  });

  const slackDisconnectMutation = useMutation({
    mutationFn: () => api.disconnectSlack(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["slack-status"] });
      queryClient.invalidateQueries({ queryKey: ["slack-channels"] });
    },
  });

  // Slack settings mutation removed — channel/notification config moved to Settings > Notifications

  // ─── CRM mutations ────────────────────────────────────────────────

  const hubspotConnectMutation = useMutation({
    mutationFn: () => api.getCrmAuthUrl("hubspot"),
    onSuccess: (data) => { window.location.href = data.auth_url; },
  });

  const hubspotDisconnectMutation = useMutation({
    mutationFn: () => api.disconnectCrm("hubspot"),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["crm-status", "hubspot"] }); },
  });

  const hubspotSettingsMutation = useMutation({
    mutationFn: (settings: { auto_push_enabled?: boolean }) => api.updateCrmSettings("hubspot", settings),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["crm-status", "hubspot"] }); },
  });

  const zohoSettingsMutation = useMutation({
    mutationFn: (settings: { auto_push_enabled?: boolean }) => api.updateCrmSettings("zoho", settings),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["crm-status", "zoho"] }); },
  });

  const zohoConnectMutation = useMutation({
    mutationFn: (region: string = "us") => api.getCrmAuthUrl("zoho", region),
    onSuccess: (data) => { window.location.href = data.auth_url; },
  });

  const zohoDisconnectMutation = useMutation({
    mutationFn: () => api.disconnectCrm("zoho"),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["crm-status", "zoho"] }); },
  });

  // CRM regeneration — wired up but not yet exposed in UI
  // const [regenerating, setRegenerating] = useState(false);
  // const [regenerateResult, setRegenerateResult] = useState<{ total_meetings: number; providers: string[] } | null>(null);
  // const handleRegenerateCrmData = async () => {
  //   setRegenerating(true);
  //   setRegenerateResult(null);
  //   try {
  //     const result = await api.regenerateCrmData();
  //     setRegenerateResult({ total_meetings: result.total_meetings, providers: result.providers });
  //   } catch { /* silent */ }
  //   finally { setRegenerating(false); }
  // };

  // ─── OAuth callback handler ──────────────────────────────────────

  const handleCallback = useCallback(async (code: string, state: string) => {
    const parts = state.split(":");
    const provider = parts.length >= 2 ? parts[1] : "google";

    try {
      if (provider === "zoom") {
        await api.handleZoomCallback(code, state);
        queryClient.invalidateQueries({ queryKey: ["zoom-status"] });
      } else if (provider === "teams") {
        await api.handleTeamsCloudCallback(code, state);
        queryClient.invalidateQueries({ queryKey: ["teams-cloud-status"] });
      } else if (provider === "slack") {
        await api.handleSlackCallback(code, state);
        queryClient.invalidateQueries({ queryKey: ["slack-status"] });
        queryClient.invalidateQueries({ queryKey: ["slack-channels"] });
      } else if (provider === "hubspot") {
        await api.handleCrmCallback("hubspot", code, state);
        queryClient.invalidateQueries({ queryKey: ["crm-status", "hubspot"] });
      } else if (provider === "zoho") {
        // Zoho passes location & accounts-server in callback URL to identify the DC
        const zohoLocation = searchParams.get("location") || "us";
        const zohoAccountsServer = searchParams.get("accounts-server") || "";
        await api.handleCrmCallback("zoho", code, state, zohoLocation, zohoAccountsServer);
        queryClient.invalidateQueries({ queryKey: ["crm-status", "zoho"] });
      } else if (provider === "outlook") {
        await api.handleOutlookCallback(code, state);
        queryClient.invalidateQueries({ queryKey: ["calendar-status", "outlook"] });
      } else {
        await api.handleGoogleCallback(code, state);
        queryClient.invalidateQueries({ queryKey: ["calendar-status", "google"] });
      }
      window.history.replaceState({}, "", "/integrations");
    } catch (err) {
      const providerLabel = provider === "zoom" ? "Zoom" : provider === "slack" ? "Slack" : provider === "outlook" ? "Outlook" : provider === "hubspot" ? "HubSpot" : provider === "zoho" ? "Zoho CRM" : "Google";
      setCallbackError(
        err instanceof ApiError ? err.message : `Failed to connect ${providerLabel}`
      );
    }
  }, [queryClient]);

  useEffect(() => {
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const adminConsent = searchParams.get("admin_consent");
    const tenantId = searchParams.get("tenant");

    if (adminConsent === "True" && tenantId && !callbackHandled.current) {
      // MS admin consent redirect — complete the Teams subscription setup
      callbackHandled.current = true;
      api.completeTeamsAdminConsent(tenantId)
        .then(() => {
          queryClient.invalidateQueries({ queryKey: ["teams-cloud-status"] });
          window.history.replaceState({}, "", "/integrations");
        })
        .catch(() => { /* silent — user can retry */ });
      return;
    }

    if (code && state && !callbackHandled.current) {
      callbackHandled.current = true;
      handleCallback(code, state);
    }
  }, [searchParams, handleCallback, queryClient]);

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  };


  // ─── Build connected / available lists ───────────────────────────

  const connectedIntegrations: React.ReactNode[] = [];
  const availableIntegrations: React.ReactNode[] = [];

  // Google Calendar
  if (googleConnected && googleConnection) {
    connectedIntegrations.push(
      <ConnectedCard
        key="google"
        name="Google Calendar"
        description="Sync upcoming meetings and schedule recording bots"
        icon={<CalendarIcon />}
        tokenInvalid={googleConnection.token_invalid}
        onReconnect={() => googleConnectMutation.mutate()}
        reconnectPending={googleConnectMutation.isPending}
        onDisconnect={() => {
          if (confirm("Disconnect Google Calendar? All synced events will be removed.")) {
            googleDisconnectMutation.mutate();
          }
        }}
        disconnectPending={googleDisconnectMutation.isPending}
        details={
          <div className="space-y-3">
            <div className="flex items-center gap-4 text-xs text-memoir-text-muted">
              <span>
                <span className="text-memoir-text-muted">Account:</span>{" "}
                <span className="text-memoir-text font-medium">{googleConnection.provider_email}</span>
              </span>
              {googleConnection.last_synced_at && (
                <span>
                  <span className="text-memoir-text-muted">Last synced:</span>{" "}
                  {formatDate(googleConnection.last_synced_at)}
                </span>
              )}
            </div>
            <label className="flex items-center gap-2 text-xs text-memoir-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={googleConnection.auto_record}
                onChange={(e) => { googleAutoRecordMutation.mutate(e.target.checked); }}
                className="rounded border-memoir-border text-memoir-indigo focus:ring-memoir-indigo/20"
              />
              Automatically record meetings
            </label>
            {googleConnection.auto_record && (
              <p className="text-[11px] text-memoir-text-muted ml-5">
                Bot will automatically join your calendar meetings when they start
              </p>
            )}
          </div>
        }
        actions={
          <>
            <button
              onClick={() => googleSyncMutation.mutate()}
              disabled={googleSyncMutation.isPending}
              className="px-3 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90 transition-colors disabled:opacity-50"
            >
              {googleSyncMutation.isPending ? "Syncing..." : "Sync Now"}
            </button>
            {googleSyncMutation.isSuccess && googleSyncMutation.data && (
              <span className="text-xs text-emerald-600 self-center">
                {googleSyncMutation.data.synced_count} events synced
              </span>
            )}
          </>
        }
      />
    );
  } else {
    availableIntegrations.push(
      <AvailableCard
        key="google"
        name="Google Calendar"
        description="Sync meetings for auto-recording"
        icon={<CalendarIcon />}
        onConnect={() => googleConnectMutation.mutate()}
        connectPending={googleConnectMutation.isPending}
        loading={googleLoading}
        connectError={googleConnectMutation.isError ? (googleConnectMutation.error instanceof ApiError ? googleConnectMutation.error.message : "Failed to connect") : null}
      />
    );
  }

  // Outlook Calendar
  if (outlookConnected && outlookConnection) {
    connectedIntegrations.push(
      <ConnectedCard
        key="outlook"
        name="Outlook Calendar"
        description="Microsoft Outlook / Office 365 calendar integration"
        icon={<OutlookIcon />}
        tokenInvalid={outlookConnection.token_invalid}
        onReconnect={() => outlookConnectMutation.mutate()}
        reconnectPending={outlookConnectMutation.isPending}
        onDisconnect={() => {
          if (confirm("Disconnect Outlook Calendar? All synced events will be removed.")) {
            outlookDisconnectMutation.mutate();
          }
        }}
        disconnectPending={outlookDisconnectMutation.isPending}
        details={
          <div className="space-y-3">
            <div className="flex items-center gap-4 text-xs text-memoir-text-muted">
              <span>
                <span className="text-memoir-text-muted">Account:</span>{" "}
                <span className="text-memoir-text font-medium">{outlookConnection.provider_email}</span>
              </span>
              {outlookConnection.last_synced_at && (
                <span>
                  <span className="text-memoir-text-muted">Last synced:</span>{" "}
                  {formatDate(outlookConnection.last_synced_at)}
                </span>
              )}
            </div>
            <label className="flex items-center gap-2 text-xs text-memoir-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={outlookConnection.auto_record}
                onChange={(e) => { outlookAutoRecordMutation.mutate(e.target.checked); }}
                className="rounded border-memoir-border text-memoir-indigo focus:ring-memoir-indigo/20"
              />
              Automatically record meetings
            </label>
            {outlookConnection.auto_record && (
              <p className="text-[11px] text-memoir-text-muted ml-5">
                Bot will automatically join your calendar meetings when they start
              </p>
            )}
          </div>
        }
        actions={
          <>
            <button
              onClick={() => outlookSyncMutation.mutate()}
              disabled={outlookSyncMutation.isPending}
              className="px-3 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90 transition-colors disabled:opacity-50"
            >
              {outlookSyncMutation.isPending ? "Syncing..." : "Sync Now"}
            </button>
            {outlookSyncMutation.isSuccess && outlookSyncMutation.data && (
              <span className="text-xs text-emerald-600 self-center">
                {outlookSyncMutation.data.synced_count} events synced
              </span>
            )}
          </>
        }
      />
    );
  } else {
    availableIntegrations.push(
      <AvailableCard
        key="outlook"
        name="Outlook Calendar"
        description="Office 365 calendar sync"
        icon={<OutlookIcon />}
        onConnect={() => outlookConnectMutation.mutate()}
        connectPending={outlookConnectMutation.isPending}
        loading={outlookLoading}
        connectError={outlookConnectMutation.isError ? (outlookConnectMutation.error instanceof ApiError ? outlookConnectMutation.error.message : "Failed to connect") : null}
      />
    );
  }

  // Zoom
  if (zoomConnected && zoomConnection) {
    connectedIntegrations.push(
      <ConnectedCard
        key="zoom"
        name="Zoom Cloud Recording"
        description="Automatically import Zoom cloud recordings for analysis"
        icon={<ZoomIcon />}
        onDisconnect={() => {
          if (confirm("Disconnect Zoom? Cloud recording imports will stop.")) {
            zoomDisconnectMutation.mutate();
          }
        }}
        disconnectPending={zoomDisconnectMutation.isPending}
        details={
          <div className="space-y-4">
            <div className="flex items-center gap-4 text-xs text-memoir-text-muted">
              <span>
                <span className="text-memoir-text-muted">Account:</span>{" "}
                <span className="text-memoir-text font-medium">{zoomConnection.zoom_email}</span>
              </span>
            </div>

            <label className="flex items-center gap-2 text-xs text-memoir-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={zoomConnection.auto_import}
                onChange={(e) => { zoomSettingsMutation.mutate({ auto_import: e.target.checked }); }}
                className="rounded border-memoir-border text-memoir-indigo focus:ring-memoir-indigo/20"
              />
              Auto-import cloud recordings
            </label>

            <div>
              <p className="text-xs font-medium text-memoir-text mb-2">Recording preference</p>
              <div className="space-y-2">
                {[
                  { value: "bot", label: "Bot only", description: "Memoir bot joins your Zoom meetings" },
                  { value: "auto", label: "Auto (Recommended)", description: "Bot joins + Zoom cloud recording saved as additional version" },
                  { value: "zoom_cloud", label: "Zoom Cloud only", description: "No bot joins \u2014 relies on Zoom native recording" },
                ].map((option) => (
                  <label
                    key={option.value}
                    className="flex items-start gap-2 text-xs cursor-pointer p-2 rounded-lg border border-memoir-border hover:bg-memoir-bg transition-colors"
                  >
                    <input
                      type="radio"
                      name="recording_preference"
                      value={option.value}
                      checked={zoomConnection.recording_preference === option.value}
                      onChange={() => { zoomSettingsMutation.mutate({ recording_preference: option.value }); }}
                      className="mt-0.5 border-memoir-border text-memoir-indigo focus:ring-memoir-indigo/20"
                    />
                    <div>
                      <span className="text-memoir-text font-medium">{option.label}</span>
                      <p className="text-memoir-text-muted mt-0.5">{option.description}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {zoomConnection.cloud_recording_enabled === false && (
              <div className="p-2.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
                Cloud recording is not enabled on your Zoom account. The &quot;Zoom Cloud only&quot; mode
                will not work. Please enable cloud recording in your Zoom settings or use
                &quot;Bot only&quot; / &quot;Auto&quot; mode.
              </div>
            )}
          </div>
        }
      />
    );
  } else {
    availableIntegrations.push(
      <AvailableCard
        key="zoom"
        name="Zoom"
        description="Import cloud recordings"
        icon={<ZoomIcon />}
        onConnect={() => zoomConnectMutation.mutate()}
        connectPending={zoomConnectMutation.isPending}
        loading={zoomLoading}
        connectError={zoomConnectMutation.isError ? (zoomConnectMutation.error instanceof ApiError ? zoomConnectMutation.error.message : "Failed to connect") : null}
        footnote="Requires Zoom Pro or higher with cloud recording enabled."
      />
    );
  }

  // Microsoft Teams Cloud Recording
  if (teamsConnected && teamsConnection) {
    connectedIntegrations.push(
      <ConnectedCard
        key="teams-cloud"
        name="Microsoft Teams Cloud Recording"
        description="Automatically import Teams meeting recordings for analysis"
        icon={<TeamsIcon />}
        onDisconnect={() => {
          if (confirm("Disconnect Teams? Cloud recording imports will stop.")) {
            teamsDisconnectMutation.mutate();
          }
        }}
        disconnectPending={teamsDisconnectMutation.isPending}
        details={
          <div className="space-y-4">
            <div className="flex items-center gap-4 text-xs text-memoir-text-muted">
              <span>
                <span className="text-memoir-text-muted">Account:</span>{" "}
                <span className="text-memoir-text font-medium">{teamsConnection.provider_email}</span>
              </span>
            </div>

            {!teamsConnection.admin_consent_granted && (
              <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg">
                <p className="text-xs text-amber-700 font-medium mb-1">Admin consent required</p>
                <p className="text-xs text-amber-600 mb-2">
                  A Microsoft admin must grant permission for Memoir to receive call records.
                </p>
                <button
                  onClick={handleTeamsAdminConsent}
                  disabled={teamsAdminConsentPending}
                  className="px-3 py-1.5 text-xs font-medium bg-amber-600 text-white rounded-button hover:bg-amber-700 transition-colors disabled:opacity-50"
                >
                  {teamsAdminConsentPending ? "Opening..." : "Grant Admin Consent →"}
                </button>
              </div>
            )}

            {teamsConnection.admin_consent_granted && teamsConnection.subscription_id && (
              <div className="flex items-center gap-1.5 text-xs text-emerald-600">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                Subscription active — recordings will auto-import
              </div>
            )}

            <label className="flex items-center gap-2 text-xs text-memoir-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={teamsConnection.auto_import}
                onChange={(e) => { teamsSettingsMutation.mutate({ auto_import: e.target.checked }); }}
                className="rounded border-memoir-border text-memoir-indigo focus:ring-memoir-indigo/20"
              />
              Auto-import cloud recordings
            </label>

            <div>
              <p className="text-xs font-medium text-memoir-text mb-2">Recording preference</p>
              <div className="space-y-2">
                {[
                  { value: "bot", label: "Bot only", description: "Memoir bot joins your Teams meetings" },
                  { value: "auto", label: "Auto (Recommended)", description: "Bot joins + Teams cloud recording saved as additional version" },
                  { value: "teams_cloud", label: "Teams Cloud only", description: "No bot joins — relies on Teams native recording" },
                ].map((option) => (
                  <label
                    key={option.value}
                    className="flex items-start gap-2 text-xs cursor-pointer p-2 rounded-lg border border-memoir-border hover:bg-memoir-bg transition-colors"
                  >
                    <input
                      type="radio"
                      name="teams_recording_preference"
                      value={option.value}
                      checked={teamsConnection.recording_preference === option.value}
                      onChange={() => { teamsSettingsMutation.mutate({ recording_preference: option.value }); }}
                      className="mt-0.5 border-memoir-border text-memoir-indigo focus:ring-memoir-indigo/20"
                    />
                    <div>
                      <span className="text-memoir-text font-medium">{option.label}</span>
                      <p className="text-memoir-text-muted mt-0.5">{option.description}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          </div>
        }
      />
    );
  } else {
    availableIntegrations.push(
      <AvailableCard
        key="teams-cloud"
        name="Microsoft Teams"
        description="Import cloud recordings from Teams meetings"
        icon={<TeamsIcon />}
        onConnect={() => teamsConnectMutation.mutate()}
        connectPending={teamsConnectMutation.isPending}
        loading={teamsLoading}
        connectError={teamsConnectMutation.isError ? (teamsConnectMutation.error instanceof ApiError ? teamsConnectMutation.error.message : "Failed to connect") : null}
        footnote="Requires Microsoft admin consent for CallRecords.Read.All permission."
      />
    );
  }

  // Slack
  if (slackConnected && slackConnection) {
    connectedIntegrations.push(
      <ConnectedCard
        key="slack"
        name="Slack"
        description="Send call insights and notifications to your Slack workspace"
        icon={<SlackIcon />}
        onDisconnect={() => {
          if (confirm("Disconnect Slack? Notifications will stop being sent.")) {
            slackDisconnectMutation.mutate();
          }
        }}
        disconnectPending={slackDisconnectMutation.isPending}
        details={
          <div className="flex items-center gap-4 text-xs text-memoir-text-muted">
            <span>
              <span className="text-memoir-text-muted">Workspace:</span>{" "}
              <span className="text-memoir-text font-medium">{slackConnection.workspace_name}</span>
            </span>
          </div>
        }
      />
    );
  } else {
    availableIntegrations.push(
      <AvailableCard
        key="slack"
        name="Slack"
        description="Get call insights in Slack"
        icon={<SlackIcon />}
        onConnect={() => slackConnectMutation.mutate()}
        connectPending={slackConnectMutation.isPending}
        loading={slackLoading}
        connectError={slackConnectMutation.isError ? (slackConnectMutation.error instanceof ApiError ? slackConnectMutation.error.message : "Failed to connect") : null}
      />
    );
  }

  // HubSpot CRM
  if (hubspotConnected && hubspotConnection) {
    connectedIntegrations.push(
      <ConnectedCard
        key="hubspot"
        name="HubSpot CRM"
        description="Auto-push call insights to HubSpot contacts and deals"
        icon={<HubSpotIcon />}
        onDisconnect={() => {
          if (confirm("Disconnect HubSpot? Auto-push will stop for all team members.")) {
            hubspotDisconnectMutation.mutate();
          }
        }}
        disconnectPending={hubspotDisconnectMutation.isPending}
        details={
          <div className="space-y-3">
            <div className="flex items-center gap-4 text-xs text-memoir-text-muted">
              <span>
                <span className="text-memoir-text-muted">Account:</span>{" "}
                <span className="text-memoir-text font-medium">{hubspotConnection.account_name || hubspotConnection.account_id}</span>
              </span>
              {hubspotConnection.last_sync_at && (
                <span>
                  <span className="text-memoir-text-muted">Last push:</span>{" "}
                  {formatDate(hubspotConnection.last_sync_at)}
                </span>
              )}
            </div>
            <label className="flex items-center gap-2 text-xs text-memoir-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={hubspotConnection.auto_push_enabled}
                onChange={(e) => { hubspotSettingsMutation.mutate({ auto_push_enabled: e.target.checked }); }}
                className="rounded border-memoir-border text-memoir-indigo focus:ring-memoir-indigo/20"
              />
              Auto-push after each call
            </label>
            {hubspotConnection.connection_error && (
              <div className="p-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
                {hubspotConnection.connection_error}
              </div>
            )}
          </div>
        }
        actions={
          <a
            href="/integrations/hubspot/field-mapping"
            className="px-3 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90 transition-colors"
          >
            Field Mapping
          </a>
        }
      />
    );
  } else {
    availableIntegrations.push(
      <AvailableCard
        key="hubspot"
        name="HubSpot CRM"
        description="Auto-push call insights to HubSpot"
        icon={<HubSpotIcon />}
        onConnect={() => hubspotConnectMutation.mutate()}
        connectPending={hubspotConnectMutation.isPending}
        loading={hubspotLoading}
        connectError={hubspotConnectMutation.isError ? (hubspotConnectMutation.error instanceof ApiError ? hubspotConnectMutation.error.message : "Failed to connect") : null}
      />
    );
  }

  // Zoho CRM
  if (zohoConnected && zohoConnection) {
    connectedIntegrations.push(
      <ConnectedCard
        key="zoho"
        name="Zoho CRM"
        description="Sync call data with Zoho CRM contacts, deals, and call records"
        icon={<ZohoCrmIcon />}
        onDisconnect={() => {
          if (confirm("Disconnect Zoho CRM? Auto-push will stop for all team members.")) {
            zohoDisconnectMutation.mutate();
          }
        }}
        disconnectPending={zohoDisconnectMutation.isPending}
        details={
          <div className="space-y-3">
            <div className="flex items-center gap-4 text-xs text-memoir-text-muted">
              <span>
                <span className="text-memoir-text-muted">Organization:</span>{" "}
                <span className="text-memoir-text font-medium">{zohoConnection.account_name || zohoConnection.account_id}</span>
              </span>
              {zohoConnection.last_sync_at && (
                <span>
                  <span className="text-memoir-text-muted">Last push:</span>{" "}
                  {formatDate(zohoConnection.last_sync_at)}
                </span>
              )}
            </div>
            <label className="flex items-center gap-2 text-xs text-memoir-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={zohoConnection.auto_push_enabled}
                onChange={(e) => { zohoSettingsMutation.mutate({ auto_push_enabled: e.target.checked }); }}
                className="rounded border-memoir-border text-memoir-indigo focus:ring-memoir-indigo/20"
              />
              Auto-push after each call
            </label>
            {zohoConnection.connection_error && (
              <div className="p-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
                {zohoConnection.connection_error}
              </div>
            )}
          </div>
        }
        actions={
          <a
            href="/integrations/zoho/field-mapping"
            className="px-3 py-1.5 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90 transition-colors"
          >
            Field Mapping
          </a>
        }
      />
    );
  } else {
    availableIntegrations.push(
      <AvailableCard
        key="zoho"
        name="Zoho CRM"
        description="Sync call data with Zoho CRM"
        icon={<ZohoCrmIcon />}
        onConnect={() => zohoConnectMutation.mutate("us")}
        connectPending={zohoConnectMutation.isPending}
        loading={zohoLoading}
        connectError={zohoConnectMutation.isError ? (zohoConnectMutation.error instanceof ApiError ? zohoConnectMutation.error.message : "Failed to connect") : null}
      />
    );
  }

  // ─── Render ──────────────────────────────────────────────────────

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-xl font-bold text-memoir-text mb-2 font-display">Integrations</h1>
      <p className="text-sm text-memoir-text-muted mb-6">
        Connect your tools to automatically record, analyze, and share meeting insights.
      </p>

      {callbackError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {callbackError}
        </div>
      )}

      {/* ─── Connected Section ────────────────────────────────────── */}
      {connectedIntegrations.length > 0 && (
        <div className="mb-8">
          <h2 className="text-xs font-semibold text-memoir-text-muted uppercase tracking-wider mb-3">Connected</h2>
          <div className="space-y-4">
            {connectedIntegrations}
          </div>

          {/* Batch Regenerate CRM Data — commented out until backend actually runs extraction
          {(hubspotConnected || zohoConnected) && (
            <div className="mt-4 p-4 bg-memoir-bg border border-memoir-border rounded-card">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-medium text-memoir-text">Regenerate CRM Data</h3>
                  <p className="text-xs text-memoir-text-muted mt-0.5">
                    Extract CRM fields for existing calls that don&apos;t have CRM data yet.
                  </p>
                </div>
                <button
                  onClick={handleRegenerateCrmData}
                  disabled={regenerating}
                  className="px-4 py-2 text-xs font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
                >
                  {regenerating ? "Processing..." : "Regenerate"}
                </button>
              </div>
              {regenerateResult && (
                <p className="text-xs text-emerald-600 mt-2">
                  {regenerateResult.total_meetings > 0
                    ? `Queued ${regenerateResult.total_meetings} call${regenerateResult.total_meetings > 1 ? "s" : ""} for CRM data extraction (${regenerateResult.providers.join(", ")})`
                    : "All calls already have CRM data extracted."}
                </p>
              )}
            </div>
          )}
          */}
        </div>
      )}

      {/* ─── Available Section ────────────────────────────────────── */}
      {availableIntegrations.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-memoir-text-muted uppercase tracking-wider mb-3">Available</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {availableIntegrations}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Page Wrapper ──────────────────────────────────────────────────

export default function IntegrationsPage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-4xl mx-auto">
          <h1 className="text-xl font-bold text-memoir-text mb-2 font-display">Integrations</h1>
          <p className="text-sm text-memoir-text-muted mb-6">
            Connect your tools to automatically record, analyze, and share meeting insights.
          </p>
          <div className="space-y-4">
            <div className="bg-memoir-surface border border-memoir-border rounded-card p-5 animate-pulse">
              <div className="h-4 bg-memoir-bg rounded w-1/3 mb-3" />
              <div className="h-3 bg-memoir-bg rounded w-1/2" />
            </div>
          </div>
        </div>
      }
    >
      <IntegrationsContent />
    </Suspense>
  );
}
