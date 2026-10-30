"use client";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  EXAMPLES,
  PromptBox,
  type PromptSubmit,
} from "@/components/home/prompt-box";
import {
  ProjectCard,
  ProjectCardSkeleton,
} from "@/components/home/project-card";
import { Empty } from "@/components/ui/card";
import { createProject } from "@/lib/projects";
import { AppShell, useRecentChats } from "@/components/shell/app-shell";
import { api, errMsg } from "@/lib/api";
import type { Page } from "@/lib/types";
import { useAuth } from "@/lib/auth";
import { useLang, useT } from "@/lib/i18n";
import { cn, timeAgo } from "@/lib/utils";

function DashboardInner() {
  const t = useT();
  const lang = useLang((s) => s.lang);
  const router = useRouter();
  const user = useAuth((s) => s.user);
  const [loading, setLoading] = useState(false);
  const [initial, setInitial] = useState("");

  useEffect(() => {
    const p = sessionStorage.getItem("oa_pending_prompt");
    if (p) {
      setInitial(p);
      sessionStorage.removeItem("oa_pending_prompt");
    }
  }, []);

  const submit = async (p: PromptSubmit) => {
    setLoading(true);
    try {
      const chat = await createProject(p);
      router.push(`/chat/${chat.chat_id}`);
    } catch (e) {
      toast.error(errMsg(e));
      setLoading(false);
    }
  };

  return (
    <div className="px-4 pb-10 md:px-8">
      <section className="mx-auto flex min-h-[64dvh] max-w-[820px] flex-col justify-center pt-16 pb-12">
        <h1 className="font-display rise text-center text-[30px] leading-tight md:text-[38px]">
          {t("whatToCreate", {
            name: user?.name || user?.email?.split("@")[0] || "",
          })}
        </h1>
        <div className="rise mt-8" style={{ ["--d" as string]: "80ms" }}>
          <PromptBox
            onSubmit={submit}
            loading={loading}
            initial={initial}
            autoFocus
          />
        </div>
        <div
          className="rise mt-4 flex flex-wrap justify-center gap-2"
          style={{ ["--d" as string]: "160ms" }}
        >
          {EXAMPLES.map((e) => (
            <button
              key={e.en}
              onClick={() => setInitial(e.prompt)}
              className="h-8 cursor-pointer rounded-full border border-line-strong bg-card/70 px-3.5 text-[12px] text-fg-2 transition-colors hover:border-[color-mix(in_srgb,var(--fg)_22%,transparent)] hover:bg-card hover:text-fg active:scale-[0.98]"
            >
              {lang === "zh" ? e.zh : e.en}
            </button>
          ))}
        </div>
      </section>
      <Gallery />
    </div>
  );
}

interface DiscoverItem {
  chat_id: string;
  title: string;
  cover: string | null;
  views: number;
  author: { name: string; avatar: string | null } | string | null;
}

function Gallery() {
  const t = useT();
  const lang = useLang((s) => s.lang);
  const [tab, setTab] = useState<"discover" | "mine">("discover");
  const [items, setItems] = useState<DiscoverItem[] | null>(null);
  const { chats, loaded } = useRecentChats(6);
  useEffect(() => {
    api
      .get<Page<DiscoverItem>>("/discover", {
        sort: "hot",
        cur_page: 1,
        page_num: 6,
      })
      .then((d) => setItems(d.items))
      .catch(() => setItems([]));
  }, []);
  // Fall through to "mine" when the community feed is empty so the panel never opens blank.
  useEffect(() => {
    if (items && items.length === 0 && chats.length > 0) setTab("mine");
  }, [items, chats.length]);

  const tabs = [
    { key: "discover" as const, label: t("discover"), href: "/discover" },
    { key: "mine" as const, label: t("myProjects"), href: "/my-projects" },
  ];
  const current = tabs.find((x) => x.key === tab)!;
  const loading = tab === "discover" ? items === null : !loaded;
  const empty =
    tab === "discover" ? items?.length === 0 : loaded && chats.length === 0;

  return (
    <section
      className="rise mx-auto max-w-[1200px] rounded-2xl border border-line bg-card p-4 md:p-8"
      style={{ ["--d" as string]: "240ms" }}
    >
      <div className="mb-6 flex items-center gap-1">
        {tabs.map((x) => (
          <button
            key={x.key}
            onClick={() => setTab(x.key)}
            className={cn(
              "h-9 cursor-pointer rounded-full px-4 text-[14px] text-muted transition-colors hover:text-fg",
              tab === x.key && "bg-hover font-medium text-fg",
            )}
          >
            {x.label}
          </button>
        ))}
        <div className="flex-1" />
        <Link
          href={current.href}
          className="flex items-center gap-0.5 text-[14px] font-medium text-fg-2 hover:text-fg"
        >
          {t("viewAll")} <ChevronRight className="h-4 w-4" />
        </Link>
      </div>
      {loading ? (
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <ProjectCardSkeleton key={i} />
          ))}
        </div>
      ) : empty ? (
        <Empty className="py-16">
          {tab === "discover" ? t("discoverEmpty") : t("empty")}
        </Empty>
      ) : (
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {tab === "discover"
            ? items!.map((it) => (
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
              ))
            : chats.map((c) => (
                <ProjectCard
                  key={c.chat_id}
                  href={`/chat/${c.chat_id}`}
                  title={c.title || "Untitled"}
                  cover={c.version?.cover}
                  meta={
                    <span className="truncate">
                      {c.version?.version ?? "draft"} ·{" "}
                      {timeAgo(c.updated_at, lang)}
                    </span>
                  }
                />
              ))}
        </div>
      )}
    </section>
  );
}

export default function Dashboard() {
  return (
    <AppShell>
      <DashboardInner />
    </AppShell>
  );
}
