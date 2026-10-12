"use client";

import { Suspense, useEffect } from "react";
import { useRouter } from "next/navigation";

function RedirectContent() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/billing");
  }, [router]);
  return null;
}

export default function BillingRedirect() {
  return (
    <Suspense fallback={null}>
      <RedirectContent />
    </Suspense>
  );
}
