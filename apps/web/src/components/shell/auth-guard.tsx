"use client";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";

export function useRequireAuth() {
  const { user, loaded, load } = useAuth();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (!loaded) load();
  }, [loaded]);
  useEffect(() => {
    if (loaded && !user) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [loaded, user]);
  return { user, ready: loaded && !!user };
}

export function FullPageLoader() {
  return (
    <div className="flex h-screen items-center justify-center">
      <span className="h-6 w-6 animate-spin rounded-full border-2 border-brand border-t-transparent" />
    </div>
  );
}
