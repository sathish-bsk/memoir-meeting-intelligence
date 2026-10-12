"use client";

import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";

interface SidebarProps {
  collapsed?: boolean;
  onNavigate?: () => void;
}

export function Sidebar({ collapsed = false, onNavigate }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const teams = useAuthStore((s) => s.teams);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const isSuperAdmin = user?.is_super_admin === true;

  const isFounder = user?.team_role === "owner" || user?.team_role === "admin";
  const effectiveScopes: string[] = (user as unknown as Record<string, unknown>)?.effective_scopes as string[] || [];
  const hasIntelligence = isFounder || effectiveScopes.includes("intelligence:read");
  const hasTeam = isFounder || effectiveScopes.includes("team:read");

  const mainItems = [
    { key: "home", label: "Home", href: "/home", icon: HomeIcon },
    { key: "calls", label: "Meetings", href: "/meetings", icon: PhoneIcon },
    ...(hasIntelligence ? [{ key: "intelligence", label: "Intelligence", href: "/intelligence", icon: BrainIcon }] : []),
    ...(hasTeam ? [{ key: "team", label: "Team", href: "/team", icon: UsersIcon }] : []),
  ];
  const activeTeam = teams.find((t) => t.id === activeTeamId);

  const allItems = [...mainItems];

  // Build tool items based on role
  const toolItems = [
    { key: "integrations", label: "Integrations", href: "/integrations", icon: LinkIcon },
    ...(isFounder
      ? [
          { key: "billing", label: "Billing", href: "/billing", icon: BillingIcon },
          { key: "permissions", label: "Permissions", href: "/permissions", icon: ShieldIcon },
        ]
      : []),
    ...(isSuperAdmin
      ? [
          { key: "dashboard", label: "Dashboard", href: "/dashboard", icon: ChartIcon },
          { key: "super-admin", label: "Super Admin", href: "/super-admin", icon: ShieldIcon },
        ]
      : []),
  ];

  const isActive = (href: string) => {
    if (href === "/home") return pathname === "/home";
    if (href === "/integrations") return pathname.startsWith("/integrations");
    if (href === "/billing") return pathname === "/billing";
    if (href === "/permissions") return pathname === "/permissions";
    if (href === "/dashboard") return pathname.startsWith("/dashboard");
    if (href === "/super-admin") return pathname.startsWith("/super-admin");
    return pathname.startsWith(href);
  };

  return (
    <aside
      className={`h-full flex flex-col border-r bg-white border-surface-border transition-all duration-200 ${
        collapsed ? "w-sidebar-collapsed" : "w-sidebar"
      }`}
    >
      {/* Logo header — restored the original /logo-icon.svg + /logo.svg
          assets (DM-Serif BrandLogo was reverted per design preference). */}
      <div className={`flex items-center ${collapsed ? "justify-center px-2 py-4" : "px-5 py-4 gap-1.5"}`}>
        <button
          onClick={() => { router.push("/home"); onNavigate?.(); }}
          aria-label="Go to home"
          className="flex items-center gap-1.5"
        >
          <Image src="/logo-icon.svg" alt="" width={28} height={28} className="rounded-lg" priority />
          {!collapsed && (
            <Image src="/logo.svg" alt="Memoir" width={100} height={24} priority />
          )}
        </button>
      </div>

      {/* Main nav */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        <p className={`text-[11px] font-medium text-memoir-text-muted uppercase tracking-wider px-3 mb-2 ${collapsed ? "hidden" : ""}`}>
          Main
        </p>
        {allItems.map((item) => {
          const active = isActive(item.href);
          return (
            <button
              key={item.key}
              onClick={() => { router.push(item.href); onNavigate?.(); }}
              className={`flex items-center gap-3 w-full rounded-button transition-all duration-150 ${
                collapsed ? "justify-center px-0 py-2.5" : "px-3 py-2.5"
              } ${
                active
                  ? "bg-brand/10 text-brand border-l-[3px] border-brand"
                  : "text-memoir-text-secondary hover:bg-memoir-border hover:text-memoir-text"
              }`}
              title={collapsed ? item.label : undefined}
            >
              <item.icon className="w-5 h-5 flex-shrink-0" />
              {!collapsed && <span className="text-sm font-medium">{item.label}</span>}
            </button>
          );
        })}

        <div className={`pt-4 ${collapsed ? "hidden" : ""}`}>
          <p className="text-[11px] font-medium text-memoir-text-muted uppercase tracking-wider px-3 mb-2">
            Tools
          </p>
        </div>
        {toolItems.map((item) => {
          const active = isActive(item.href);
          return (
            <button
              key={item.key}
              onClick={() => { router.push(item.href); onNavigate?.(); }}
              className={`flex items-center gap-3 w-full rounded-button transition-all duration-150 ${
                collapsed ? "justify-center px-0 py-2.5" : "px-3 py-2.5"
              } ${
                active
                  ? "bg-brand/10 text-brand border-l-[3px] border-brand"
                  : "text-memoir-text-secondary hover:bg-memoir-border hover:text-memoir-text"
              }`}
              title={collapsed ? item.label : undefined}
            >
              <item.icon className="w-5 h-5 flex-shrink-0" />
              {!collapsed && <span className="text-sm font-medium">{item.label}</span>}
            </button>
          );
        })}
      </nav>

      {/* User info at bottom */}
      <div className={`border-t border-sidebar-border px-3 py-3 ${collapsed ? "px-2" : ""}`}>
        <div className={`flex items-center gap-3 ${collapsed ? "justify-center" : ""}`}>
          <div className="w-8 h-8 rounded-full bg-brand/10 flex items-center justify-center flex-shrink-0">
            <span className="text-xs font-semibold text-brand">
              {user?.name?.[0]?.toUpperCase() || "?"}
            </span>
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="text-sm font-medium text-memoir-text truncate">{user?.name || "User"}</p>
              <p className="text-xs text-memoir-text-muted truncate">{activeTeam?.name || ""}</p>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

// ─── Icons (inline SVG, no external deps) ───────────────────

function HomeIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="m2.25 12 8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25" />
    </svg>
  );
}

function PhoneIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 6.75c0 8.284 6.716 15 15 15h2.25a2.25 2.25 0 0 0 2.25-2.25v-1.372c0-.516-.351-.966-.852-1.091l-4.423-1.106c-.44-.11-.902.055-1.173.417l-.97 1.293c-.282.376-.769.542-1.21.38a12.035 12.035 0 0 1-7.143-7.143c-.162-.441.004-.928.38-1.21l1.293-.97c.363-.271.527-.734.417-1.173L6.963 3.102a1.125 1.125 0 0 0-1.091-.852H4.5A2.25 2.25 0 0 0 2.25 4.5v2.25Z" />
    </svg>
  );
}

function BrainIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 0 0-2.455 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z" />
    </svg>
  );
}

function UsersIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
    </svg>
  );
}

function LinkIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" />
    </svg>
  );
}

function BillingIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3h15a2.25 2.25 0 0 0 2.25-2.25V6.75A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25v10.5A2.25 2.25 0 0 0 4.5 19.5Z" />
    </svg>
  );
}

function ShieldIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
    </svg>
  );
}

function ChartIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
    </svg>
  );
}
