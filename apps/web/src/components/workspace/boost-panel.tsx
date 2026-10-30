"use client";
import { Check, ExternalLink, Loader2, Star, Trophy, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { boostActions } from "@/lib/chat-actions";
import { useLang } from "@/lib/i18n";
import type { BoostItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { PanelBar, PanelEmpty } from "./common";

const STATUS_TONE: Record<BoostItem["status"], string> = {
  queued: "bg-hover text-muted",
  running: "bg-brand-soft text-brand",
  success: "bg-positive-soft text-positive",
  failed: "bg-danger-soft text-danger",
  cancelled: "bg-hover text-muted",
};

/** Live preview of one candidate (its own child project): authenticate with that project's preview ticket. */
function CandidatePreview({ item }: { item: BoostItem }) {
  const [src, setSrc] = useState<string | null>(null);
  const ready = item.status === "success" || item.status === "running";
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    api
      .post<{ ticket: string; url: string }>(`/chats/${item.chat_id}/preview-ticket`)
      .then((d) => alive && setSrc(d.url))
      .catch(() => alive && setSrc(item.preview_url));
    return () => {
      alive = false;
    };
    // re-authenticate once the candidate finishes (its dev server restarts with the final code)
  }, [item.chat_id, ready, item.status === "success"]);
  if (!ready || !src) {
    return (
      <div className="flex h-full items-center justify-center text-[12px] text-muted">
        {item.status === "queued" ? <Loader2 className="h-4 w-4 animate-spin" /> : item.error || item.status}
      </div>
    );
  }
  return <iframe key={src} src={src} title={`candidate ${item.index + 1}`} className="h-full w-full bg-white" />;
}

/** Opens a candidate in a new tab through its own preview ticket (private projects need the preview cookie). */
async function openCandidate(it: BoostItem) {
  const w = window.open("about:blank", "_blank");
  try {
    const d = await api.post<{ ticket: string; url: string }>(`/chats/${it.chat_id}/preview-ticket`);
    if (w) w.location.href = d.url;
  } catch {
    if (w && it.preview_url) w.location.href = it.preview_url;
  }
}

export function BoostPanel() {
  const chatId = useChat((s) => s.chatId)!;
  const boost = useChat((s) => s.boost);
  const zh = useLang((s) => s.lang) === "zh";
  const [busy, setBusy] = useState<number | "abort" | "discard" | null>(null);
  if (!boost) return <PanelEmpty icon={Trophy} text={zh ? "还没有竞赛。在输入框打开「竞赛模式」后发送需求。" : "No boost yet. Turn on Boost in the composer and send a prompt."} />;

  const done = boost.status === "merged" || boost.status === "aborted";
  const pick = async (i: number) => {
    setBusy(i);
    await boostActions.select(chatId, i);
    setBusy(null);
  };

  return (
    <div className="flex h-full flex-col">
      <PanelBar>
        <Trophy className="h-3.5 w-3.5 text-muted" />
        <span className="font-medium">{zh ? "竞赛模式" : "Boost"}</span>
        <span className="text-muted">
          {boost.items.length} {zh ? "个候选" : "candidates"} · {boost.status}
        </span>
        <div className="flex-1" />
        {boost.status === "running" && (
          <Button size="xs" variant="outline" loading={busy === "abort"}
            onClick={async () => (setBusy("abort"), await boostActions.abort(chatId), setBusy(null))}>
            {zh ? "停止" : "Stop"}
          </Button>
        )}
        {boost.status !== "merging" && (
          <Button size="xs" variant="ghost" loading={busy === "discard"}
            onClick={async () => (setBusy("discard"), await boostActions.discard(chatId), setBusy(null))}>
            <X className="h-3.5 w-3.5" /> {zh ? "丢弃" : "Discard"}
          </Button>
        )}
      </PanelBar>
      {boost.judge_reason && !done && (
        <div className="flex items-center gap-1.5 border-b border-line bg-brand-soft px-3 py-1.5 text-[12px] text-brand">
          <Star className="h-3.5 w-3.5 fill-current" />
          {zh ? "推荐：" : "Recommended: "}
          {boost.judge_reason}
        </div>
      )}
      {boost.status === "merged" && boost.selected !== null && (
        <div className="flex items-center gap-1.5 border-b border-line bg-positive-soft px-3 py-1.5 text-[12px] text-positive">
          <Check className="h-3.5 w-3.5" />
          {zh ? `已合并候选 #${boost.selected + 1}` : `Merged candidate #${boost.selected + 1}`}
        </div>
      )}
      <div
        className="grid min-h-0 flex-1 gap-2 overflow-auto p-2"
        style={{ gridTemplateColumns: `repeat(${Math.min(boost.items.length, 4)}, minmax(260px, 1fr))` }}
      >
        {boost.items.map((it) => {
          const recommended = boost.recommended === it.index;
          const selected = boost.selected === it.index;
          return (
            <div
              key={it.index}
              className={cn(
                "flex min-h-[420px] flex-col overflow-hidden rounded-l border border-line bg-card",
                recommended && "border-brand",
                selected && "ring-2 ring-positive",
              )}
            >
              <div className="flex items-center gap-1.5 border-b border-line px-2.5 py-1.5 text-[12px]">
                <span className="font-semibold">#{it.index + 1}</span>
                <span className="truncate text-muted">{it.model}</span>
                {recommended && <Star className="h-3.5 w-3.5 fill-current text-brand" aria-label="recommended" />}
                <div className="flex-1" />
                <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", STATUS_TONE[it.status])}>
                  {it.status === "running" && <Loader2 className="mr-0.5 inline h-2.5 w-2.5 animate-spin" />}
                  {it.status}
                </span>
              </div>
              <div className="min-h-0 flex-1 bg-bg-2">
                <CandidatePreview item={it} />
              </div>
              <div className="flex flex-col gap-1.5 border-t border-line p-2.5">
                <div className="line-clamp-3 min-h-[3em] text-[12px] text-fg-2">{it.summary ||
                    (it.status === "failed" ? (zh ? "未完成" : "Did not finish") : it.status === "success" ? "" : zh ? "构建中…" : "Building…")}</div>
                <div className="flex flex-wrap gap-1 text-[10px]">
                  <span className={cn("rounded-full px-1.5 py-0.5", it.build_ok ? "bg-positive-soft text-positive" : "bg-hover text-muted")}>
                    build {it.build_ok === null ? "—" : it.build_ok ? "✓" : "✗"}
                  </span>
                  <span className={cn("rounded-full px-1.5 py-0.5", it.checkui_ok ? "bg-positive-soft text-positive" : "bg-hover text-muted")}>
                    preview {it.checkui_ok === null ? "—" : it.checkui_ok ? "✓" : "✗"}
                  </span>
                  {typeof it.steps === "number" && <span className="rounded-full bg-hover px-1.5 py-0.5 text-muted">{it.steps} steps</span>}
                  {it.version && <span className="rounded-full bg-hover px-1.5 py-0.5 text-muted">{it.version}</span>}
                </div>
                <div className="flex items-center gap-1.5">
                  <Button
                    size="xs"
                    className="flex-1"
                    disabled={done || boost.status === "merging" || it.status !== "success"}
                    loading={busy === it.index}
                    onClick={() => pick(it.index)}
                  >
                    {selected ? <Check className="h-3.5 w-3.5" /> : <Trophy className="h-3.5 w-3.5" />}
                    {selected ? (zh ? "已选择" : "Selected") : zh ? "选择并合并" : "Pick & merge"}
                  </Button>
                  {it.preview_url && (
                    <button
                      onClick={() => openCandidate(it)}
                      className="cursor-pointer rounded-m p-1.5 text-muted hover:bg-hover hover:text-fg"
                      aria-label="open preview"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
