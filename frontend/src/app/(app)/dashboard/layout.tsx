"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { useAuthStore } from "@/store/auth";

const TABS = [
  { label: "Overview", href: "/dashboard/overview" },
  { label: "Cost", href: "/dashboard/cost" },
  { label: "Customers", href: "/dashboard/customers" },
  { label: "Processing", href: "/dashboard/processing" },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (user && !user.is_super_admin) {
      router.replace("/home");
    }
  }, [user, router]);

  if (!user?.is_super_admin) return null;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="border-b border-zinc-200 bg-white px-6 pt-5 pb-0">
        <h1 className="text-xl font-bold text-zinc-900 mb-1">Admin Dashboard</h1>
        <p className="text-sm text-zinc-500 mb-4">Cross-customer analytics and health monitoring.</p>

        {/* Tab nav */}
        <nav className="flex gap-1">
          {TABS.map((tab) => {
            const active = pathname === tab.href || pathname.startsWith(tab.href + "/");
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  active
                    ? "border-indigo-600 text-indigo-600"
                    : "border-transparent text-zinc-500 hover:text-zinc-900"
                }`}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        {children}
      </div>
    </div>
  );
}
