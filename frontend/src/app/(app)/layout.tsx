"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { AuthGuard } from "@/components/auth-guard";
import { Sidebar } from "@/components/sidebar";
import { TopBar } from "@/components/top-bar";
import { CalendarHealthBanner } from "@/components/calendar-health-banner";
import { SearchModal } from "@/components/search-modal";
import { SetupBar } from "@/components/setup-bar";
import { useSetupSteps } from "@/hooks/use-setup-steps";
import { useSetupBar } from "@/hooks/use-setup-bar";
import { api } from "@/lib/api";

/**
 * App chrome layout (revamp v2).
 *
 * Structure:
 *   <flex row>
 *     <Sidebar>  (full-height, contains logo + nav + user footer)
 *     <flex col> (content area)
 *       <TopBar>  (only over content, NOT spanning sidebar)
 *       <SetupBar>  (optional, below topbar)
 *       <main>  (overflow-auto; v2 pages manage their own padding,
 *                v1 pages get a wrapper with px-6 py-8)
 *     </flex col>
 *   </flex row>
 *
 * V2 pages (HomeV2, MeetingsV2) render their own internal flex layout
 * (sub-panels, right-side calendar/peek panels) and need to fill the
 * full available area — so `fullBleedRoutes` opts them out of the
 * default px-6 py-8 wrapper.
 */
const FULL_BLEED_ROUTES = ["/home", "/meetings", "/events"];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const user = useAuthStore((s) => s.user);
  const teamSyncRef = useRef(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [calendarConnected, setCalendarConnected] = useState(false);
  const [crmConnected, setCrmConnected] = useState(false);
  const [slackConnected, setSlackConnected] = useState(false);
  const calendarFetchRef = useRef(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [extensionInstalled, setExtensionInstalled] = useState(false);

  // Detect Chrome extension via DOM attribute set by content script
  useEffect(() => {
    const check = () => document.documentElement.hasAttribute("data-memoir-ext");
    setExtensionInstalled(check());
    const timer = setTimeout(() => setExtensionInstalled(check()), 1000);
    return () => clearTimeout(timer);
  }, []);

  const { dismissed: barDismissed, dismiss: dismissBar, expand: expandBar } = useSetupBar();
  const steps = useSetupSteps(calendarConnected, crmConnected, extensionInstalled, slackConnected);
  const completedCount = steps.filter((s) => s.done).length;
  const allDone = completedCount === steps.length;

  useEffect(() => {
    if (teamSyncRef.current || !user) return;
    teamSyncRef.current = true;
    api.getTeams().then((freshTeams) => {
      const teamInfos = freshTeams.map((t) => ({ id: t.id, name: t.name, slug: t.slug }));
      useAuthStore.getState().setTeams(teamInfos);
      if (teamInfos.length > 0) {
        const validIds = new Set(teamInfos.map((t) => t.id));
        const current = useAuthStore.getState().activeTeamId;
        if (!current || !validIds.has(current)) {
          useAuthStore.setState({ activeTeamId: teamInfos[0].id });
        }
      }
    }).catch(() => {});
  }, [user]);

  useEffect(() => {
    if (calendarFetchRef.current || !user) return;
    calendarFetchRef.current = true;
    Promise.any([
      api.getCalendarStatus("google"),
      api.getCalendarStatus("outlook"),
    ])
      .then((conn) => {
        if (conn && !conn.token_invalid) setCalendarConnected(true);
      })
      .catch(() => {});
    api.getCrmConnections()
      .then((conns) => { if (conns.length > 0) setCrmConnected(true); })
      .catch(() => {});
    api.getSlackStatus()
      .then(() => setSlackConnected(true))
      .catch(() => {});
  }, [user]);

  // Cmd+K / Ctrl+K shortcut to open search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  const handleToggleSidebar = () => {
    if (window.innerWidth < 1024) {
      setMobileOpen((v) => !v);
    } else {
      setSidebarCollapsed((v) => !v);
    }
  };
  const closeMobile = () => setMobileOpen(false);

  const isHomePage = pathname === "/home";
  const showSetupBar = !allDone && !barDismissed && !isHomePage;
  // Show the compact Setup pill on every page when onboarding is incomplete
  // (used to be home-only / dismissed-only — user wanted consistency).
  const setupPill = !allDone
    ? { count: completedCount, total: steps.length, onExpand: expandBar }
    : null;

  const isFullBleed = FULL_BLEED_ROUTES.some(
    (r) => pathname === r || pathname.startsWith(r + "/"),
  );

  return (
    <AuthGuard>
      <div className="flex h-screen overflow-hidden bg-surface-soft">
        {/* Mobile overlay backdrop */}
        {mobileOpen && (
          <div
            className="fixed inset-0 z-30 bg-black/30 lg:hidden"
            onClick={closeMobile}
          />
        )}

        {/* Sidebar — desktop: static sibling, mobile: fixed slide-in */}
        <div
          className={`
            fixed inset-y-0 left-0 z-40 transition-transform duration-200
            lg:static lg:translate-x-0
            ${mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"}
          `}
        >
          <Sidebar collapsed={sidebarCollapsed} onNavigate={closeMobile} />
        </div>

        {/* Content column — topbar + setup bar + main */}
        <div className="flex flex-1 flex-col overflow-hidden">
          <TopBar
            onToggleSidebar={handleToggleSidebar}
            onSearchClick={() => setSearchOpen(true)}
            setupPill={setupPill}
          />
          <CalendarHealthBanner />
          {showSetupBar && (
            <SetupBar steps={steps} onDismiss={dismissBar} />
          )}
          <main className="flex-1 overflow-auto bg-surface-soft">
            {isFullBleed ? children : <div className="px-6 py-8">{children}</div>}
          </main>
        </div>

        <SearchModal open={searchOpen} onOpenChange={setSearchOpen} />
      </div>
    </AuthGuard>
  );
}
