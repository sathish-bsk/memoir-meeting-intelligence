"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";

export default function Home() {
  const router = useRouter();
  const [hydrated, setHydrated] = useState(false);
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  useEffect(() => {
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    if (!isAuthenticated || !user) {
      router.replace("/login");
    } else if (!user.onboarding_completed) {
      router.replace("/onboarding");
    } else {
      router.replace("/home");
    }
  }, [hydrated, isAuthenticated, user, router]);

  return (
    <div className="min-h-screen bg-zinc-50 flex items-center justify-center">
      <div className="w-5 h-5 border-2 border-zinc-300 border-t-zinc-600 rounded-full animate-spin" />
    </div>
  );
}
