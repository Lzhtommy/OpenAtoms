"use client";
import { ArrowDown, ArrowUp, ListOrdered, Microscope, MousePointerClick, Pause, Play, Paperclip, Pencil, Send, Square, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { BoostPicker } from "@/components/chat/boost-picker";
import { useGeneralConfig } from "@/components/home/prompt-box";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/tooltip";
import { AGENTS } from "@/lib/agents";
import { api, errMsg } from "@/lib/api";
import { chatActions } from "@/lib/chat-actions";
import { deltaToText } from "@/lib/delta";
import { useLang, useT } from "@/lib/i18n";
import type { Delta, DeltaOp, ModelInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { useVisualEdit } from "@/store/visual-edit";

const MENTION_RE = new RegExp(`@(${AGENTS.map((a) => a.name).join("|")})\\b`, "g");

export function textToDelta(text: string): Delta {
  const ops: DeltaOp[] = [];
  let last = 0;
  for (const m of text.matchAll(MENTION_RE)) {
    if (m.index! > last) ops.push({ insert: text.slice(last, m.index) });
    const a = AGENTS.find((x) => x.name === m[1])!;
    ops.push({ insert: { mentiontrigger: { id: a.role, char: "@", value: a.name } } });
    last = m.index! + m[0].length;
  }
  if (last < text.length) ops.push({ insert: text.slice(last) });
  return ops;
}

interface Uploaded {
  name: string;
  path: string;
  url: string;
  mime: string;
}

export function Composer() {
  const t = useT();
  const lang = useLang((s) => s.lang);
  const chatId = useChat((s) => s.chatId)!;
  const chat = useChat((s) => s.chat);
  const isRunning = useChat((s) => s.isRunning);
  const attached = useVisualEdit((s) => (s.chatId === chatId ? s.attached : null));
  const [text, setText] = useState("");
  const [files, setFiles] = useState<Uploaded[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [mention, setMention] = useState<{ q: string; idx: number } | null>(null);
  const [mIdx, setMIdx] = useState(0);
  const [boostModels, setBoostModels] = useState<string[] | null>(null);
  const [research, setResearch] = useState(false);
  const cfg = useGeneralConfig();
  const models: ModelInfo[] = cfg
    ? (cfg.models?.length ? cfg.models : [cfg.default_model, ...cfg.adv_models].map((id): ModelInfo => ({ id, name: id, provider: "" })))
    : [];
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  }, [text]);

  useEffect(() => {
    // external "fill composer" requests (e.g. Fix with AI)
    const onFill = (e: Event) => {
      const d = (e as CustomEvent).detail as string;
      setText(d);
      taRef.current?.focus();
    };
    const onFocus = () => taRef.current?.focus();
    window.addEventListener("oa:composer-fill", onFill);
    window.addEventListener("oa:composer-focus", onFocus);
    return () => {
      window.removeEventListener("oa:composer-fill", onFill);
      window.removeEventListener("oa:composer-focus", onFocus);
    };
  }, []);

  const mentionable = chat?.config.agent_mode === "lite" ? AGENTS.filter((a) => a.name === "Alex") : AGENTS;
  const candidates = mention ? mentionable.filter((a) => a.name.toLowerCase().startsWith(mention.q.toLowerCase())) : [];

  const onChange = (v: string, caret: number) => {
    setText(v);
    const before = v.slice(0, caret);
    const m = /@(\w*)$/.exec(before);
    if (m) {
      setMention({ q: m[1], idx: caret - m[0].length });
      setMIdx(0);
    } else setMention(null);
  };

  const pickMention = (name: string) => {
    if (!mention) return;
    const caret = taRef.current?.selectionStart ?? text.length;
    const next = text.slice(0, mention.idx) + "@" + name + " " + text.slice(caret);
    setText(next);
    setMention(null);
    requestAnimationFrame(() => taRef.current?.focus());
  };

  const upload = async (list: FileList | null) => {
    if (!list?.length) return;
    setUploading(true);
    try {
      for (const f of Array.from(list)) {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("dir", "uploads");
        const r = await api.upload<{ path: string; url: string }>(`/chats/${chatId}/files/upload`, fd);
        setFiles((x) => [...x, { name: f.name, path: r.path, url: r.url, mime: f.type }]);
      }
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setUploading(false);
    }
  };

  const send = async () => {
    if (!text.trim() || sending) return;
    setSending(true);
    const content: Delta = [
      ...files.map((f) => ({ insert: { attachment: { name: f.name, url: f.url, mime: f.mime } } }) as DeltaOp),
      ...textToDelta(text.trim()),
    ];
    const r = await chatActions.send(chatId, {
      content,
      type: "message",
      metadata: {
        attachments: files.length ? files : undefined,
        selection: attached ?? undefined,
        enable_dr: research || undefined,
      },
      ...(boostModels && !isRunning ? { use_boost: true, boost_models: boostModels } : {}),
    });
    setSending(false);
    if (r) {
      setText("");
      setFiles([]);
      setBoostModels(null);
      useVisualEdit.getState().set({ attached: null });
    }
  };

  return (
    <div className="px-3 pb-3">
      <QueuePanel />
      <div className="relative rounded-xl border border-line bg-card p-2.5 shadow-card focus-within:border-line-strong">
        {mention && candidates.length > 0 && (
          <div className="absolute bottom-full left-2 mb-2 w-56 rounded-l border border-line bg-card p-1 shadow-pop">
            {candidates.map((a, i) => (
              <button
                key={a.name}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pickMention(a.name);
                }}
                className={cn("flex w-full cursor-pointer items-center gap-2 rounded-m px-2 py-1.5 text-left text-[13px]", i === mIdx && "bg-hover")}
              >
                <img src={a.avatar} alt="" className="h-5 w-5 rounded-full" />
                <span className="font-medium">{a.name}</span>
                <span className="text-[11px]" style={{ color: a.color }}>
                  {lang === "zh" ? a.skillZh : a.skill}
                </span>
              </button>
            ))}
          </div>
        )}
        {attached && (
          <div className="mb-1.5 flex">
            <span
              className="inline-flex max-w-full items-center gap-1 rounded-full bg-brand-soft px-2 py-0.5 text-[12px] text-brand"
              title={`${attached.loc ?? ""} ${attached.selector}`}
            >
              <MousePointerClick className="h-3 w-3 shrink-0" />
              <span className="font-mono">&lt;{attached.tag}&gt;</span>
              <span className="truncate">{attached.text || attached.loc}</span>
              <button aria-label="remove selection" onClick={() => useVisualEdit.getState().set({ attached: null })}>
                <X className="h-3 w-3" />
              </button>
            </span>
          </div>
        )}
        {files.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1.5">
            {files.map((f, i) => (
              <span key={i} className="inline-flex items-center gap-1 rounded-full bg-hover px-2 py-0.5 text-[12px]">
                <Paperclip className="h-3 w-3" />
                {f.name}
                <button onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={taRef}
          value={text}
          rows={2}
          onChange={(e) => onChange(e.target.value, e.target.selectionStart)}
          onKeyDown={(e) => {
            if (mention && candidates.length) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setMIdx((mIdx + 1) % candidates.length);
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setMIdx((mIdx - 1 + candidates.length) % candidates.length);
                return;
              }
              if (e.key === "Enter" || e.key === "Tab") {
                e.preventDefault();
                pickMention(candidates[mIdx].name);
                return;
              }
              if (e.key === "Escape") {
                setMention(null);
                return;
              }
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={isRunning ? (lang === "zh" ? "运行中，新消息将进入队列…" : "Running — new messages will be queued…") : lang === "zh" ? "输入消息，@ 召唤智能体" : "Message, @ to mention an agent"}
          className="w-full resize-none bg-transparent px-1 text-[14px] leading-relaxed outline-none placeholder:text-subtle"
        />
        <div className="flex items-center gap-1.5">
          <Tip label={t("attach")}>
            <Button variant="ghost" size="icon-sm" onClick={() => fileRef.current?.click()} loading={uploading}>
              {!uploading && <Paperclip className="h-4 w-4" />}
            </Button>
          </Tip>
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => (upload(e.target.files), (e.target.value = ""))} />
          <span className="text-[11px] text-subtle">{chat?.config.agent_mode === "lite" ? t("lite") : t("full")}</span>
          {models.length > 0 && !chat?.parent_chat_id && (
            <BoostPicker models={models} value={boostModels} onChange={setBoostModels} defaultModel={chat?.llm.default_model} />
          )}
          <Tip label={t("deepResearchDesc")}>
            <button
              type="button"
              onClick={() => setResearch(!research)}
              aria-pressed={research}
              className={cn(
                "flex cursor-pointer items-center gap-1 rounded-full px-2 py-1 text-[12px] text-muted hover:bg-hover",
                research && "bg-brand-soft text-brand",
              )}
            >
              <Microscope className="h-3.5 w-3.5" />
              {t("research")}
            </button>
          </Tip>
          <div className="flex-1" />
          {isRunning && (
            <Tip label={t("stop")}>
              <Button variant="outline" size="icon-sm" onClick={() => chatActions.stop(chatId)}>
                <Square className="h-3 w-3 fill-current" />
              </Button>
            </Tip>
          )}
          <Button size="icon-sm" onClick={send} disabled={!text.trim()} loading={sending} aria-label="send">
            {!sending && (isRunning ? <ListOrdered className="h-3.5 w-3.5" /> : <Send className="h-3.5 w-3.5" />)}
          </Button>
        </div>
      </div>
    </div>
  );
}

function QueuePanel() {
  const t = useT();
  const chatId = useChat((s) => s.chatId)!;
  const queue = useChat((s) => s.queue);
  const [editing, setEditing] = useState<number | null>(null);
  const [text, setText] = useState("");
  const items = queue.items.filter((i) => i.status === "queued").sort((a, b) => a.position - b.position);
  if (!items.length) return null;
  const move = (idx: number, dir: -1 | 1) => {
    const ids = items.map((i) => i.id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    chatActions.queue.reorder(chatId, ids);
  };
  return (
    <div className="mb-2 rounded-l border border-line bg-card-2 p-2 shadow-card">
      <div className="mb-1 flex items-center gap-2 px-1 text-[12px] text-muted">
        <ListOrdered className="h-3.5 w-3.5" />
        {t("queue")} ({items.length})
        {queue.paused && <span className="rounded-full bg-warning-soft px-1.5 text-[10px] text-warning">paused</span>}
        <div className="flex-1" />
        <button className="flex cursor-pointer items-center gap-1 hover:text-fg" onClick={() => chatActions.queue.pause(chatId, !queue.paused)}>
          {queue.paused ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />}
          {queue.paused ? t("resume") : t("pause")}
        </button>
      </div>
      <div className="flex max-h-40 flex-col gap-1 overflow-y-auto">
        {items.map((it, i) => (
          <div key={it.id} className="group flex items-center gap-1.5 rounded-m bg-card px-2 py-1 text-[12px]">
            <span className="text-subtle">{i + 1}</span>
            {editing === it.id ? (
              <input
                autoFocus
                className="flex-1 bg-transparent outline-none"
                value={text}
                onChange={(e) => setText(e.target.value)}
                onBlur={() => setEditing(null)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    chatActions.queue.update(chatId, it.id, textToDelta(text));
                    setEditing(null);
                  }
                }}
              />
            ) : (
              <span className="min-w-0 flex-1 truncate">{deltaToText(it.content)}</span>
            )}
            <div className="flex items-center opacity-60 group-hover:opacity-100">
              <QBtn label="Up" onClick={() => move(i, -1)}>
                <ArrowUp className="h-3 w-3" />
              </QBtn>
              <QBtn label="Down" onClick={() => move(i, 1)}>
                <ArrowDown className="h-3 w-3" />
              </QBtn>
              <QBtn
                label={t("edit")}
                onClick={() => {
                  setText(deltaToText(it.content));
                  setEditing(it.id);
                }}
              >
                <Pencil className="h-3 w-3" />
              </QBtn>
              <QBtn label={t("sendNow")} onClick={() => chatActions.queue.sendNow(chatId, it.id)}>
                <Send className="h-3 w-3" />
              </QBtn>
              <QBtn label={t("delete")} onClick={() => chatActions.queue.remove(chatId, it.id)}>
                <Trash2 className="h-3 w-3" />
              </QBtn>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function QBtn({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <Tip label={label}>
      <button onClick={onClick} className="cursor-pointer rounded p-1 text-muted hover:bg-hover hover:text-fg">
        {children}
      </button>
    </Tip>
  );
}
