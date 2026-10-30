"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Logo } from "@/components/shell/logo";
import { LangToggle, ThemeToggle } from "@/components/shell/prefs";
import { EXAMPLES, PromptBox } from "@/components/home/prompt-box";
import { Button } from "@/components/ui/button";
import { AGENTS } from "@/lib/agents";
import { tokenStore } from "@/lib/api";
import { useLang, useT } from "@/lib/i18n";

export default function Landing() {
  const t = useT();
  const lang = useLang((s) => s.lang);
  const router = useRouter();
  useEffect(() => {
    if (tokenStore.get()) router.replace("/dashboard");
  }, []);
  const go = (text: string) => {
    sessionStorage.setItem("oa_pending_prompt", text);
    router.push(tokenStore.get() ? "/dashboard" : "/register?next=/dashboard");
  };
  return (
    <div className="min-h-screen">
      <div className="bg-gradient-to-r from-[#e6edff] via-[#eef2ff] to-[#e9f0ff] py-2 text-center text-[13px] text-fg-2 dark:from-[#1a2140] dark:via-[#1b1f33] dark:to-[#1a2140]">
        OpenAtoms — open-source multi-agent app builder.{" "}
        <Link href="/register" className="ml-1 rounded-full bg-brand px-2.5 py-0.5 text-white">
          {t("getStarted")}
        </Link>
      </div>
      <header className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Logo />
        <nav className="flex items-center gap-1">
          <Link href="/discover" className="rounded-full px-3 py-1.5 text-[13px] text-fg-2 hover:bg-hover">
            {t("discover")}
          </Link>
          <Link href="/pricing" className="rounded-full px-3 py-1.5 text-[13px] text-fg-2 hover:bg-hover">
            {t("pricing")}
          </Link>
          <LangToggle />
          <ThemeToggle />
          <Link href="/login">
            <Button variant="ghost" size="sm">
              {t("login")}
            </Button>
          </Link>
          <Link href="/register">
            <Button size="sm">{t("register")}</Button>
          </Link>
        </nav>
      </header>
      <section className="mx-auto max-w-3xl px-6 pt-20 pb-16 text-center">
        <div className="mb-6 flex justify-center -space-x-3">
          {AGENTS.slice(0, 5).map((a) => (
            <img key={a.name} src={a.avatar} alt={a.name} title={`${a.name} · ${a.role}`} className="h-12 w-12 rounded-full bg-card ring-4 ring-bg" />
          ))}
        </div>
        <h1 className="font-serif text-[44px] leading-tight tracking-tight md:text-[56px]">{t("heroTitle")}</h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] text-muted">{t("heroSub")}</p>
        <div className="mt-10 text-left">
          <PromptBox showControls={false} onSubmit={(p) => go(p.text)} />
        </div>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {EXAMPLES.map((e) => (
            <button key={e.en} onClick={() => go(e.prompt)} className="rounded-full border border-line-strong bg-card px-3 py-1 text-[12px] text-fg-2 hover:bg-hover">
              {lang === "zh" ? e.zh : e.en}
            </button>
          ))}
        </div>
      </section>
      <section className="mx-auto grid max-w-5xl gap-4 px-6 pb-24 md:grid-cols-5">
        {AGENTS.slice(0, 5).map((a) => (
          <div key={a.name} className="rounded-xl border border-line bg-card p-4 text-center shadow-card">
            <img src={a.avatar} alt={a.name} className="mx-auto h-16 w-16 rounded-full bg-bg-2" />
            <div className="mt-2 font-medium">{a.name}</div>
            <div className="text-[12px]" style={{ color: a.color }}>
              {lang === "zh" ? a.skillZh : a.skill}
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}
