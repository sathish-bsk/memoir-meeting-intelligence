"use client";

import { Suspense, useEffect } from "react";
import { useRouter } from "next/navigation";

function RedirectContent() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/permissions");
  }, [router]);
  return null;
}

export default function PermissionsRedirect() {
  return (
    <Suspense fallback={null}>
      <RedirectContent />
    </Suspense>
  );
}
