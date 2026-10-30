"use client";
import { Logo } from "@/components/shell/logo";
import { LangToggle, ThemeToggle } from "@/components/shell/prefs";
import { AGENTS } from "@/lib/agents";

export function AuthCard({ title, subtitle, children }: { title: string; subtitle?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-14 items-center justify-between px-6">
        <Logo />
        <div className="flex gap-1">
          <LangToggle />
          <ThemeToggle />
        </div>
      </header>
      <div className="flex flex-1 items-center justify-center px-4 pb-16">
        <div className="w-full max-w-[400px] rounded-xl border border-line bg-card p-7 shadow-card">
          <div className="mb-4 flex -space-x-2">
            {AGENTS.slice(0, 5).map((a) => (
              <img key={a.name} src={a.avatar} alt={a.name} className="h-8 w-8 rounded-full bg-bg-2 ring-2 ring-card" />
            ))}
          </div>
          <h1 className="font-serif text-2xl">{title}</h1>
          {subtitle && <p className="mt-1 text-[13px] text-muted">{subtitle}</p>}
          <div className="mt-5">{children}</div>
        </div>
      </div>
    </div>
  );
}
