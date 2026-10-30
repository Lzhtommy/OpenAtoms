import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-line bg-card shadow-card", className)} {...p} />;
}
export function Badge({
  className,
  tone = "neutral",
  ...p
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: "neutral" | "brand" | "positive" | "danger" | "warning" }) {
  const tones = {
    neutral: "bg-hover text-fg-2",
    brand: "bg-brand-soft text-brand",
    positive: "bg-positive-soft text-positive",
    danger: "bg-danger-soft text-danger",
    warning: "bg-warning-soft text-warning",
  };
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", tones[tone], className)}
      {...p}
    />
  );
}
export function Spinner({ className }: { className?: string }) {
  return <span className={cn("inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent", className)} />;
}
export function Empty({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("flex flex-col items-center justify-center gap-2 py-12 text-sm text-muted", className)}>{children}</div>;
}
