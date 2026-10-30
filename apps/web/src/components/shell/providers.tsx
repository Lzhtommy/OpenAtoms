"use client";
import { useEffect } from "react";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { initLang } from "@/lib/i18n";
import { initSentry } from "@/lib/sentry";
import { initTheme, useTheme } from "@/lib/theme";

export function Providers({ children }: { children: React.ReactNode }) {
  const dark = useTheme((s) => s.dark);
  useEffect(() => {
    initTheme();
    initLang();
    void initSentry();
  }, []);
  return (
    <TooltipProvider>
      {children}
      <Toaster position="top-center" theme={dark ? "dark" : "light"} richColors closeButton />
    </TooltipProvider>
  );
}
