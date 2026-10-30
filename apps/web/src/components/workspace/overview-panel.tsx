"use client";
import { ExternalLink, Globe, History, Rocket } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { useGeneralConfig } from "@/components/home/prompt-box";
import { api, errMsg } from "@/lib/api";
import { chatActions } from "@/lib/chat-actions";
import { useT } from "@/lib/i18n";
import type { Chat } from "@/lib/types";
import { fmtDate, timeAgo } from "@/lib/utils";
import { useChat } from "@/store/chat";

export function OverviewPanel({ readOnly }: { readOnly?: boolean }) {
  const t = useT();
  const chat = useChat((s) => s.chat);
  const versions = useChat((s) => s.versions);
  const deployment = useChat((s) => s.deployment);
  const timelines = useChat((s) => s.timelines);
  const messages = useChat((s) => s.messages);
  const mergeChat = useChat((s) => s.mergeChat);
  const cfg = useGeneralConfig();
  if (!chat) return null;

  const patch = async (body: Partial<Chat> & Record<string, unknown>) => {
    try {
      const c = await api.patch<Chat>(`/chats/${chat.chat_id}`, body);
      mergeChat(c);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const models = cfg ? [cfg.default_model, ...cfg.adv_models].filter((m, i, a) => a.indexOf(m) === i) : [chat.llm.default_model];

  return (
    <div className="h-full overflow-y-auto p-5">
      <h2 className="font-serif text-[22px]">{chat.title}</h2>
      <div className="mt-1 text-[12px] text-muted">
        Created {fmtDate(chat.created_at)} · updated {timeAgo(chat.updated_at)}
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ["Messages", messages.length],
          ["Tool steps", Object.keys(timelines).length],
          ["Versions", versions.length],
          ["Likes", chat.stats.likes],
        ].map(([k, v]) => (
          <div key={k as string} className="rounded-l border border-line bg-card p-3">
            <div className="text-[11px] text-muted">{k}</div>
            <div className="mt-1 text-[20px] font-semibold">{v}</div>
          </div>
        ))}
      </div>

      <div className="mt-5 rounded-l border border-line bg-card p-4">
        <div className="mb-3 text-[13px] font-medium">Settings</div>
        <div className="flex flex-col gap-3 text-[13px]">
          <Row label="Agent mode">
            <Select
              value={chat.config.agent_mode}
              onValueChange={(v) => !readOnly && patch({ config: { agent_mode: v as "lite" | "full" } } as any)}
              options={[
                { value: "lite", label: t("lite"), desc: t("liteDesc") },
                { value: "full", label: t("full"), desc: t("fullDesc") },
              ]}
            />
          </Row>
          <Row label={t("model")}>
            <Select value={chat.llm.default_model} onValueChange={(v) => !readOnly && patch({ llm: { default_model: v } })} options={models.map((m) => ({ value: m, label: m }))} />
          </Row>
          <Row label={t("accessMode")}>
            <Select
              value={chat.access_mode}
              onValueChange={(v) => !readOnly && patch({ access_mode: v as Chat["access_mode"] })}
              options={[
                { value: "private", label: t("private") },
                { value: "secret", label: t("secret") },
                { value: "public", label: t("public") },
              ]}
            />
          </Row>
          <Row label="Preview">
            {chat.uris.dev ? (
              <a href={chat.uris.dev} target="_blank" rel="noreferrer" className="flex items-center gap-1 truncate text-brand hover:underline">
                {chat.uris.dev} <ExternalLink className="h-3 w-3" />
              </a>
            ) : (
              "—"
            )}
          </Row>
        </div>
      </div>

      <div className="mt-5 rounded-l border border-line bg-card p-4">
        <div className="mb-3 flex items-center gap-2 text-[13px] font-medium">
          <Rocket className="h-4 w-4" /> Deployment
        </div>
        {deployment ? (
          <div className="flex items-center gap-2 text-[13px]">
            <Badge tone={deployment.status === "success" ? "positive" : deployment.status === "failed" ? "danger" : "warning"}>{deployment.status}</Badge>
            <span>{deployment.is_always_latest ? "latest" : deployment.version}</span>
            {deployment.url && (
              <a href={deployment.url} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-1 truncate text-brand hover:underline">
                <Globe className="h-3 w-3" />
                {deployment.url}
              </a>
            )}
          </div>
        ) : (
          <div className="text-[13px] text-muted">Not published yet</div>
        )}
      </div>

      <div className="mt-5 rounded-l border border-line bg-card p-4">
        <div className="mb-3 flex items-center gap-2 text-[13px] font-medium">
          <History className="h-4 w-4" /> {t("versions")}
        </div>
        <div className="flex flex-col gap-2">
          {versions.length === 0 && <div className="text-[13px] text-muted">No versions yet</div>}
          {versions.map((v) => (
            <div key={v.version} className="flex items-center gap-2 text-[13px]">
              <span className="w-10 font-medium">{v.version}</span>
              <Badge tone={v.status === "ready" ? "positive" : v.status === "failed" ? "danger" : "warning"}>{v.status}</Badge>
              <span className="min-w-0 flex-1 truncate text-muted">{v.summary}</span>
              {v.link && (
                <a href={v.link} target="_blank" rel="noreferrer">
                  <ExternalLink className="h-3.5 w-3.5 text-muted hover:text-fg" />
                </a>
              )}
              {!readOnly && (
                <Button size="xs" variant="ghost" onClick={() => chatActions.restore(chat.chat_id, v.version)}>
                  Restore
                </Button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 text-muted">{label}</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
