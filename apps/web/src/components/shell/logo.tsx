import Link from "next/link";
import { cn } from "@/lib/utils";

export function LogoMark({ size = 22, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" className={className} aria-hidden>
      <circle cx="16" cy="16" r="15" fill="var(--inverse)" />
      <ellipse cx="16" cy="16" rx="11" ry="4.2" fill="none" stroke="var(--inverse-fg)" strokeWidth="1.6" />
      <ellipse cx="16" cy="16" rx="11" ry="4.2" fill="none" stroke="var(--inverse-fg)" strokeWidth="1.6" transform="rotate(60 16 16)" />
      <ellipse cx="16" cy="16" rx="11" ry="4.2" fill="none" stroke="var(--inverse-fg)" strokeWidth="1.6" transform="rotate(-60 16 16)" />
      <circle cx="16" cy="16" r="2.4" fill="var(--brand)" />
    </svg>
  );
}

export function Logo({ className, href = "/" }: { className?: string; href?: string }) {
  return (
    <Link href={href} className={cn("flex items-center gap-2 font-semibold tracking-tight", className)}>
      <LogoMark />
      <span className="text-[15px]">OpenAtoms</span>
    </Link>
  );
}
