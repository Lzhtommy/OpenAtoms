"use client";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Logo } from "@/components/shell/logo";
import { LangToggle, ThemeToggle } from "@/components/shell/prefs";
import { EXAMPLES, PromptBox } from "@/components/home/prompt-box";
import { ProjectCard } from "@/components/home/project-card";
import { Button } from "@/components/ui/button";
import { AGENTS } from "@/lib/agents";
import { api, tokenStore } from "@/lib/api";
import { useLang, useT } from "@/lib/i18n";
import type { Page } from "@/lib/types";

const DUTIES: Record<string, { zh: string; en: string }> = {
  Mike: {
    zh: "拆解目标，调度团队，把控交付",
    en: "Breaks down the goal and coordinates the team",
  },
  Emma: {
    zh: "梳理需求，写出清晰的产品文档",
    en: "Turns your idea into a clear product spec",
  },
  Bob: {
    zh: "设计架构、数据模型与接口",
    en: "Designs the architecture, data model and APIs",
  },
  Alex: {
    zh: "编写、调试并部署全栈代码",
    en: "Writes, debugs and deploys full-stack code",
  },
  David: {
    zh: "分析数据，产出图表与洞察",
    en: "Analyzes data and produces charts and insights",
  },
  Iris: {
    zh: "跨网页深度调研并整理报告",
    en: "Researches the web and writes cited reports",
  },
  Sarah: {
    zh: "优化页面结构与搜索排名",
    en: "Tunes page structure for search ranking",
  },
  Adrian: {
    zh: "策划广告投放与增长素材",
    en: "Plans ad campaigns and growth creatives",
  },
};

interface ShowcaseItem {
  chat_id: string;
  title: string;
  cover: string | null;
  views: number;
  author: { name: string; avatar: string | null } | string | null;
}

export default function Landing() {
  const t = useT();
  const lang = useLang((s) => s.lang);
  const zh = lang === "zh";
  const router = useRouter();
  const [showcase, setShowcase] = useState<ShowcaseItem[]>([]);
  useEffect(() => {
    if (tokenStore.get()) router.replace("/dashboard");
    api
      .get<Page<ShowcaseItem>>("/discover", {
        sort: "hot",
        cur_page: 1,
        page_num: 6,
      })
      .then((d) => setShowcase(d.items))
      .catch(() => {});
  }, []);
  const go = (text: string) => {
    sessionStorage.setItem("oa_pending_prompt", text);
    router.push(tokenStore.get() ? "/dashboard" : "/register?next=/dashboard");
  };

  return (
    <div className="relative min-h-[100dvh] overflow-x-clip bg-bg">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[640px] bg-[radial-gradient(60%_60%_at_50%_0%,var(--brand-soft-2),transparent_70%)]"
      />
      <header className="relative">
        <div className="mx-auto flex h-16 max-w-[1200px] items-center justify-between px-4 md:px-8">
          <Logo />
          <nav className="flex items-center gap-1">
            <Link
              href="/discover"
              className="hidden h-8 items-center rounded-full px-3 text-[14px] text-fg-2 transition-colors hover:text-fg sm:flex"
            >
              {t("discover")}
            </Link>
            <Link
              href="/pricing"
              className="hidden h-8 items-center rounded-full px-3 text-[14px] text-fg-2 transition-colors hover:text-fg sm:flex"
            >
              {t("pricing")}
            </Link>
            <LangToggle />
            <ThemeToggle />
            <Link href="/login" className="ml-1">
              <Button variant="ghost">{t("login")}</Button>
            </Link>
            <Link href="/register">
              <Button>{t("getStarted")}</Button>
            </Link>
          </nav>
        </div>
      </header>

      {/* Hero: the prompt box is the product, so it sits center stage like atoms.dev */}
      <section className="relative">
        <div className="mx-auto max-w-3xl px-4 pt-14 pb-20 text-center md:pt-20">
          <div className="rise inline-flex items-center gap-2.5 rounded-full border border-line bg-card/80 py-1 pr-3.5 pl-1 text-[13px] text-fg-2">
            <span className="flex -space-x-1.5">
              {AGENTS.slice(0, 5).map((a) => (
                <img
                  key={a.name}
                  src={a.avatar}
                  alt=""
                  className="h-6 w-6 rounded-full bg-bg-2 ring-2 ring-card"
                />
              ))}
            </span>
            {t("landingBadge")}
          </div>
          <h1
            className="font-display rise mx-auto mt-6 max-w-[14em] text-[40px] leading-[1.08] md:text-[60px]"
            style={{ ["--d" as string]: "60ms" }}
          >
            {t("heroTitle")}
          </h1>
          <p
            className="rise mx-auto mt-5 max-w-xl text-[16px] leading-relaxed text-muted"
            style={{ ["--d" as string]: "120ms" }}
          >
            {t("heroSub")}
          </p>
          <div
            className="rise mt-10 text-left"
            style={{ ["--d" as string]: "180ms" }}
          >
            <PromptBox showControls={false} onSubmit={(p) => go(p.text)} />
          </div>
          <div
            className="rise mt-4 flex flex-wrap justify-center gap-2"
            style={{ ["--d" as string]: "240ms" }}
          >
            {EXAMPLES.map((e) => (
              <button
                key={e.en}
                onClick={() => go(e.prompt)}
                className="h-8 cursor-pointer rounded-full border border-line-strong bg-card/70 px-3.5 text-[12px] text-fg-2 transition-colors hover:bg-card hover:text-fg active:scale-[0.98]"
              >
                {zh ? e.zh : e.en}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Team */}
      <section className="mx-auto max-w-[1200px] px-4 md:px-8">
        <div className="grid gap-10 rounded-2xl border border-line bg-card p-6 md:p-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16">
          <div className="flex flex-col">
            <h2 className="font-display text-[28px] leading-tight text-balance md:text-[34px]">
              {t("landingTeamTitle")}
            </h2>
            <p className="mt-4 max-w-[34ch] text-[15px] leading-relaxed text-balance text-muted">
              {t("landingTeamSub")}
            </p>
            <div className="mt-8 hidden flex-1 items-end lg:flex">
              <div className="flex -space-x-4">
                {AGENTS.slice(0, 5).map((a, i) => (
                  <img
                    key={a.name}
                    src={a.avatar}
                    alt=""
                    className="h-20 w-20 rounded-full bg-bg-2 ring-4 ring-card transition-transform duration-300 hover:-translate-y-1"
                    style={{ zIndex: 5 - i }}
                  />
                ))}
              </div>
            </div>
          </div>
          <div className="grid gap-x-8 sm:grid-cols-2">
            {AGENTS.map((a) => (
              <div
                key={a.name}
                className="flex items-start gap-3.5 border-t border-line py-4"
              >
                <img
                  src={a.avatar}
                  alt={a.name}
                  className="h-11 w-11 shrink-0 rounded-full bg-bg-2"
                />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[15px] font-semibold">{a.name}</span>
                    <span
                      className="rounded-full px-2 py-0.5 text-[11px] font-medium text-fg-2"
                      style={{
                        background: `color-mix(in srgb, ${a.color} 18%, transparent)`,
                      }}
                    >
                      {zh ? a.skillZh : a.skill}
                    </span>
                  </div>
                  <p className="mt-1 text-[13px] leading-relaxed text-muted">
                    {zh ? DUTIES[a.name]?.zh : DUTIES[a.name]?.en}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Showcase: only rendered once the community feed can fill a row */}
      {showcase.length >= 3 && (
        <section className="mx-auto mt-20 max-w-[1200px] px-4 md:px-8">
          <div className="mb-6 flex items-end justify-between gap-4">
            <h2 className="font-display text-[28px] leading-tight md:text-[36px]">
              {t("landingShowcaseTitle")}
            </h2>
            <Link
              href="/discover"
              className="flex shrink-0 items-center gap-0.5 text-[14px] font-medium text-fg-2 hover:text-fg"
            >
              {t("viewAll")} <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {showcase.map((it) => (
              <ProjectCard
                key={it.chat_id}
                href={`/share/${it.chat_id}`}
                title={it.title}
                cover={it.cover}
                author={
                  typeof it.author === "string"
                    ? { name: it.author }
                    : it.author
                }
                views={it.views}
              />
            ))}
          </div>
        </section>
      )}

      {/* Closing CTA */}
      <section className="mx-auto mt-20 max-w-[1200px] px-4 md:px-8">
        <div className="relative overflow-hidden rounded-2xl border border-transparent bg-[#111112] px-6 py-14 text-center text-white md:py-20 dark:border-line dark:bg-card">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 bg-[radial-gradient(50%_80%_at_50%_120%,color-mix(in_srgb,var(--brand)_45%,transparent),transparent_70%)]"
          />
          <h2 className="font-display relative mx-auto max-w-[18em] text-[28px] leading-tight md:text-[40px]">
            {t("landingCtaTitle")}
          </h2>
          <p className="relative mx-auto mt-4 max-w-lg text-[15px] leading-relaxed text-balance opacity-70">
            {t("landingCtaSub")}
          </p>
          <Link href="/register" className="relative mt-8 inline-block">
            <Button
              size="lg"
              className="bg-white text-[#111112] hover:opacity-90 active:scale-[0.98]"
            >
              {t("getStarted")} <ArrowRight className="h-4 w-4" />
            </Button>
          </Link>
        </div>
      </section>

      <footer className="mx-auto mt-16 flex max-w-[1200px] flex-col items-center justify-between gap-4 px-4 pb-10 text-[13px] text-muted sm:flex-row md:px-8">
        <Logo />
        <div className="flex items-center gap-5">
          <Link href="/discover" className="hover:text-fg">
            {t("discover")}
          </Link>
          <Link href="/pricing" className="hover:text-fg">
            {t("pricing")}
          </Link>
          <Link href="/login" className="hover:text-fg">
            {t("login")}
          </Link>
        </div>
      </footer>
    </div>
  );
}
