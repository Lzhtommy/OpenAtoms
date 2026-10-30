"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { EXAMPLES, PromptBox, type PromptSubmit } from "@/components/home/prompt-box";
import { createProject } from "@/lib/projects";
import { AppShell, useRecentChats } from "@/components/shell/app-shell";
import { errMsg } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLang, useT } from "@/lib/i18n";
import { timeAgo } from "@/lib/utils";

function DashboardInner() {
  const t = useT();
  const lang = useLang((s) => s.lang);
  const router = useRouter();
  const user = useAuth((s) => s.user);
  const [loading, setLoading] = useState(false);
  const [initial, setInitial] = useState("");
  const { chats } = useRecentChats(8);

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
    <div className="mx-auto max-w-3xl px-6 pt-[12vh] pb-16">
      <h1 className="text-center font-serif text-[34px] leading-tight tracking-tight md:text-[40px]">
        {t("whatToCreate", { name: user?.name || user?.email?.split("@")[0] || "" })}
      </h1>
      <div className="mt-8">
        <PromptBox onSubmit={submit} loading={loading} initial={initial} autoFocus />
      </div>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {EXAMPLES.map((e) => (
          <button key={e.en} onClick={() => setInitial(e.prompt)} className="rounded-full border border-line-strong bg-card px-3 py-1 text-[12px] text-fg-2 hover:bg-hover">
            {lang === "zh" ? e.zh : e.en}
          </button>
        ))}
      </div>
      {chats.length > 0 && (
        <div className="mt-14">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-[13px] font-medium text-muted">{t("recent")}</h2>
            <Link href="/my-projects" className="text-[12px] text-brand hover:underline">
              {t("allProjects")} →
            </Link>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {chats.map((c) => (
              <Link key={c.chat_id} href={`/chat/${c.chat_id}`} className="rounded-l border border-line bg-card p-3 shadow-card hover:border-line-strong">
                <div className="truncate text-[14px] font-medium">{c.title}</div>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-muted">
                  <span className="capitalize">{c.config.agent_mode}</span>·<span>{c.version?.version ?? "draft"}</span>·<span>{timeAgo(c.updated_at, lang)}</span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  return (
    <AppShell>
      <DashboardInner />
    </AppShell>
  );
}
