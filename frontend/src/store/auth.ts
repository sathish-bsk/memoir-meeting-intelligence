import { create } from "zustand";
import { persist } from "zustand/middleware";

// Relative URL — same as api.ts. Next.js rewrites handle proxying in dev.
const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

function parseJwtPayload(token: string): Record<string, unknown> {
  try {
    const base64Url = token.split(".")[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join(""),
    );
    return JSON.parse(jsonPayload);
  } catch {
    return {};
  }
}

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  team_id: string;
  team_role: string;
  plan: string;
  onboarding_completed: boolean;
  is_super_admin: boolean;
}

interface TeamInfo {
  id: string;
  name: string;
  slug: string;
}

interface AuthState {
  user: AuthUser | null;
  accessToken: string | null;
  activeTeamId: string | null;
  teams: TeamInfo[];
  isAuthenticated: boolean;

  setAccessToken: (token: string) => void;
  setTeams: (teams: TeamInfo[]) => void;
  updateUser: (updates: Partial<AuthUser>) => void;
  switchTeam: (teamId: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<boolean>;
}

let refreshTimer: ReturnType<typeof setTimeout> | null = null;

// In-flight refresh promise — serializes concurrent refresh() callers so
// they all await the SAME network request. Without this, when the access
// token expires and N parallel API calls 401 simultaneously, each fires
// its own /auth/refresh with the same (about-to-be-rotated) cookie. The
// auth_service treats the second request's cookie as a reused-after-revoke
// token, marks it as theft, and revokes the entire token family — which
// is exactly the "frequent logout while actively using the app" symptom.
let inflightRefresh: Promise<boolean> | null = null;

function scheduleProactiveRefresh(token: string, refreshFn: () => Promise<boolean>) {
  if (refreshTimer) clearTimeout(refreshTimer);
  const payload = parseJwtPayload(token);
  const exp = payload.exp as number | undefined;
  if (!exp) return;
  const now = Math.floor(Date.now() / 1000);
  const ttl = exp - now;
  // Refresh at 80% of token lifetime (e.g., 12 min for 15-min token)
  const refreshIn = Math.max(60, Math.floor(ttl * 0.8)) * 1000;
  refreshTimer = setTimeout(() => {
    refreshFn().catch(() => {});
  }, refreshIn);
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      accessToken: null,
      activeTeamId: null,
      teams: [],
      isAuthenticated: false,

      setAccessToken: (token: string) => {
        const payload = parseJwtPayload(token);
        const user: AuthUser = {
          id: (payload.sub as string) || "",
          email: (payload.email as string) || "",
          name: (payload.name as string) || "",
          team_id: (payload.team_id as string) || "",
          team_role: (payload.team_role as string) || "",
          plan: (payload.plan as string) || "free",
          onboarding_completed: Boolean(payload.onboarding_completed),
          is_super_admin: Boolean(payload.is_super_admin),
        };
        set({
          accessToken: token,
          user,
          activeTeamId: user.team_id || get().activeTeamId,
          isAuthenticated: true,
        });
        scheduleProactiveRefresh(token, get().refresh);
      },

      setTeams: (teams) => set({ teams }),

      updateUser: (updates) => {
        const current = get().user;
        if (!current) return;
        set({ user: { ...current, ...updates } });
      },

      switchTeam: async (teamId: string) => {
        const { accessToken } = get();
        if (!accessToken) return;
        try {
          const res = await fetch(`${API_URL}/auth/switch-team`, {
            method: "POST",
            credentials: "include",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${accessToken}`,
            },
            body: JSON.stringify({ team_id: teamId }),
          });
          if (!res.ok) return;
          const data = await res.json();
          get().setAccessToken(data.access_token);
        } catch {
          // Switch failed — keep current team
        }
      },

      logout: () => {
        if (refreshTimer) clearTimeout(refreshTimer);
        refreshTimer = null;
        set({
          user: null,
          accessToken: null,
          activeTeamId: null,
          teams: [],
          isAuthenticated: false,
        });
      },

      refresh: async () => {
        // Coalesce concurrent callers onto a single in-flight request so
        // we never send the same refresh cookie twice (which trips the
        // auth_service's family-reuse detection and logs the user out).
        if (inflightRefresh) return inflightRefresh;
        const promise = (async () => {
          try {
            const res = await fetch(`${API_URL}/auth/refresh`, {
              method: "POST",
              credentials: "include",
              headers: { "Content-Type": "application/json" },
            });
            if (!res.ok) return false;
            const data = await res.json();
            get().setAccessToken(data.access_token);
            return true;
          } catch {
            return false;
          } finally {
            inflightRefresh = null;
          }
        })();
        inflightRefresh = promise;
        return promise;
      },
    }),
    {
      name: "memoir-auth",
      partialize: (state) => ({
        accessToken: state.accessToken,
        activeTeamId: state.activeTeamId,
        user: state.user,
        teams: state.teams,
        isAuthenticated: state.isAuthenticated,
      }),
      // Re-arm the proactive refresh timer on page reload. Without this,
      // a tab left open after reload had no timer and only refreshed
      // reactively on a 401 — adding latency and increasing the chance
      // of a refresh storm racing under load.
      onRehydrateStorage: () => (state) => {
        if (state?.accessToken) {
          scheduleProactiveRefresh(state.accessToken, () => state.refresh());
        }
      },
    },
  ),
);
