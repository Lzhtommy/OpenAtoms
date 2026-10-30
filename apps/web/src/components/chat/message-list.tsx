"use client";
import { Brain, ChevronDown, ChevronLeft, ChevronRight, Copy, Pencil, RotateCcw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar, UserAvatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Tip } from "@/components/ui/tooltip";
import { agentFor } from "@/lib/agents";
import { useAuth } from "@/lib/auth";
import { chatActions } from "@/lib/chat-actions";
import { deltaToText, segments } from "@/lib/delta";
import { useLang, useT } from "@/lib/i18n";
import type { Message } from "@/lib/types";
import { cn, copyText, fmtDate, fmtTime, parseTs } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { AlertCard, BoostCard, PlanReadyCard, QuestionsCard, TaskCard, VersionCard } from "./cards";
import { DeltaView } from "./delta-view";
import { Markdown } from "./markdown";
import { ToolCard } from "./tool-card";

type Group = { key: string; role: string; name: string; isUser: boolean; messages: Message[]; day: string };

function groupMessages(ms: Message[]): Group[] {
  const groups: Group[] = [];
  for (const m of ms) {
    if (m.type === "emoji" && m.refer_id) continue;
    const isUser = m.role === "User";
    const day = new Date(parseTs(m.created_at)).toDateString();
    const last = groups[groups.length - 1];
    if (last && !isUser && !last.isUser && last.role === m.role && last.day === day) last.messages.push(m);
    else groups.push({ key: `g${m.id}`, role: m.role, name: m.name, isUser, messages: [m], day });
  }
  return groups;
}

export function MessageList({ readOnly }: { readOnly?: boolean }) {
  const messages = useChat((s) => s.messages);
  const isRunning = useChat((s) => s.isRunning);
  const groups = useMemo(() => groupMessages(messages), [messages]);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const lastRunning = isRunning && messages.length > 0 && messages[messages.length - 1].role === "User";

  return (
    <div ref={ref} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-6">
      {groups.map((g, i) => (
        <div key={g.key}>
          {(i === 0 || groups[i - 1].day !== g.day) && (
            <div className="my-4 flex items-center gap-3 text-[11px] text-subtle">
              <div className="h-px flex-1 bg-line" />
              {fmtDate(g.messages[0].created_at)}
              <div className="h-px flex-1 bg-line" />
            </div>
          )}
          {g.isUser ? (
            g.messages.map((m) => <UserMessage key={m.id} m={m} readOnly={readOnly} />)
          ) : (
            <AgentGroup g={g} readOnly={readOnly} />
          )}
        </div>
      ))}
      {lastRunning && <PendingIndicator />}
    </div>
  );
}

function PendingIndicator() {
  const t = useT();
  return (
    <div className="mt-4 flex items-center gap-2 text-[13px] text-muted">
      <span className="flex gap-1">
        <span className="typing-dot h-1.5 w-1.5 rounded-full bg-brand" />
        <span className="typing-dot h-1.5 w-1.5 rounded-full bg-brand" />
        <span className="typing-dot h-1.5 w-1.5 rounded-full bg-brand" />
      </span>
      {t("thinking")}…
    </div>
  );
}

function UserMessage({ m, readOnly }: { m: Message; readOnly?: boolean }) {
  const t = useT();
  const user = useAuth((s) => s.user);
  const isRunning = useChat((s) => s.isRunning);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const isTaskReply = !!m.metadata?.task_reply;
  return (
    <div className="group mt-5 flex flex-col items-end">
      {editing ? (
        <div className="w-full max-w-[90%]">
          <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
          <div className="mt-2 flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              {t("cancel")}
            </Button>
            <Button
              size="sm"
              onClick={async () => {
                setEditing(false);
                await chatActions.edit(m.chat_id, m.id, [{ insert: text }]);
              }}
            >
              {t("save")}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex max-w-[90%] items-start gap-2">
          <div
            className={cn(
              "rounded-xl rounded-tr-s bg-card px-3.5 py-2.5 text-[14px] shadow-card",
              isTaskReply && "border border-brand/30 bg-brand-soft",
            )}
          >
            <DeltaView content={m.content} />
          </div>
          <UserAvatar name={user?.name || m.name} src={user?.avatar} size={24} className="mt-1" />
        </div>
      )}
      {!editing && (
        <div className="mt-1 mr-8 flex items-center gap-0.5 text-muted opacity-0 transition-opacity group-hover:opacity-100">
          {m.attempts > 1 && (
            <span className="mr-1 flex items-center text-[11px] opacity-100">
              <button
                className="rounded p-0.5 hover:bg-hover disabled:opacity-30"
                disabled={m.attempt <= 0 || readOnly}
                onClick={() => chatActions.switchAttempt(m.chat_id, m.round_id, m.attempt - 1)}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              {m.attempt + 1}/{m.attempts}
              <button
                className="rounded p-0.5 hover:bg-hover disabled:opacity-30"
                disabled={m.attempt >= m.attempts - 1 || readOnly}
                onClick={() => chatActions.switchAttempt(m.chat_id, m.round_id, m.attempt + 1)}
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </span>
          )}
          <span className="mr-1 text-[11px]">{fmtTime(m.created_at)}</span>
          <IconBtn label={t("copy")} onClick={() => copyText(deltaToText(m.content))?.then(() => toast.success(t("copied")))}>
            <Copy className="h-3.5 w-3.5" />
          </IconBtn>
          {!readOnly && !isTaskReply && (
            <>
              <IconBtn
                label={t("edit")}
                disabled={isRunning}
                onClick={() => {
                  setText(deltaToText(m.content));
                  setEditing(true);
                }}
              >
                <Pencil className="h-3.5 w-3.5" />
              </IconBtn>
              <IconBtn label={t("retry")} disabled={isRunning} onClick={() => chatActions.retry(m.chat_id, m.id)}>
                <RotateCcw className="h-3.5 w-3.5" />
              </IconBtn>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function IconBtn({ label, children, onClick, disabled }: { label: string; children: React.ReactNode; onClick?: () => void; disabled?: boolean }) {
  return (
    <Tip label={label}>
      <button disabled={disabled} onClick={onClick} className="cursor-pointer rounded-s p-1 hover:bg-hover hover:text-fg disabled:opacity-30">
        {children}
      </button>
    </Tip>
  );
}

function AgentGroup({ g, readOnly }: { g: Group; readOnly?: boolean }) {
  const lang = useLang((s) => s.lang);
  const a = agentFor(g.role, g.name);
  const last = g.messages[g.messages.length - 1];
  return (
    <div className="group mt-5">
      <div className="mb-1.5 flex items-center gap-2">
        <AgentAvatar role={g.role} name={g.name} size={26} />
        <span className="text-[13px] font-semibold">{a?.name ?? g.name ?? g.role}</span>
        <span className="text-[11px]" style={{ color: a?.color }}>
          {a ? (lang === "zh" ? a.skillZh : a.skill) : g.role}
        </span>
        <span className="text-[11px] text-subtle">{fmtTime(g.messages[0].created_at)}</span>
      </div>
      <div className="flex flex-col gap-1.5 pl-[34px]">
        {g.messages.map((m) => (
          <AgentMessage key={m.id} m={m} readOnly={readOnly} />
        ))}
      </div>
      <div className="mt-1 flex items-center gap-0.5 pl-[30px] text-muted opacity-0 transition-opacity group-hover:opacity-100">
        <IconBtn
          label="Copy"
          onClick={() =>
            copyText(g.messages.map((m) => (typeof m.content === "object" ? deltaToText(m.content) : String(m.content))).join("\n\n"))?.then(() =>
              toast.success("Copied"),
            )
          }
        >
          <Copy className="h-3.5 w-3.5" />
        </IconBtn>
        <IconBtn label="Good" onClick={() => toast.success("Thanks for the feedback")}>
          <ThumbsUp className="h-3.5 w-3.5" />
        </IconBtn>
        <IconBtn label="Bad" onClick={() => toast.success("Thanks for the feedback")}>
          <ThumbsDown className="h-3.5 w-3.5" />
        </IconBtn>
        <span className="hidden">{last.id}</span>
      </div>
    </div>
  );
}

function EmojiReplies({ m }: { m: Message }) {
  if (!m.reply_messages?.length) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {m.reply_messages
        .filter((r) => r.type === "emoji")
        .map((r) => (
          <span key={r.id} className="inline-flex items-center gap-1 rounded-full border border-line bg-card px-1.5 py-0.5 text-[11px] text-muted">
            <AgentAvatar role={r.role} name={r.name} size={14} />
            👌 {r.name}
          </span>
        ))}
    </div>
  );
}

function AgentMessage({ m, readOnly }: { m: Message; readOnly?: boolean }) {
  let body: React.ReactNode;
  switch (m.type) {
    case "think":
      body = <ThinkBlock m={m} />;
      break;
    case "task":
      body = <TaskCard m={m} readOnly={readOnly} />;
      break;
    case "questions_card":
      body = <QuestionsCard m={m} readOnly={readOnly} />;
      break;
    case "plan_ready":
      body = <PlanReadyCard m={m} readOnly={readOnly} />;
      break;
    case "version":
      body = <VersionCard m={m} readOnly={readOnly} />;
      break;
    case "boost":
      body = <BoostCard m={m} readOnly={readOnly} />;
      break;
    case "error":
    case "quota":
      body = <AlertCard m={m} />;
      break;
    case "restore":
    case "remix":
      body = (
        <div className="rounded-m bg-bg-2 px-3 py-2 text-[12px] text-muted">
          {typeof m.content === "string" ? m.content : m.content?.message ?? deltaToText(m.content) ?? m.type}
        </div>
      );
      break;
    default:
      body = <DeltaView content={m.content} streaming={!m.is_finished} />;
  }
  return (
    <div className="min-w-0 text-[14px]">
      {body}
      <EmojiReplies m={m} />
    </div>
  );
}

function ThinkBlock({ m }: { m: Message }) {
  const t = useT();
  const segs = segments(m.content);
  const hasText = segs.some((s) => s.kind === "text" && s.text.trim());
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? !m.is_finished;
  return (
    <div className="min-w-0">
      {hasText && (
        <button onClick={() => setOpen(!expanded)} className="flex cursor-pointer items-center gap-1.5 text-[12px] text-muted hover:text-fg">
          <Brain className="h-3.5 w-3.5" />
          {m.is_finished ? (t("thinking") === "思考中" ? "思考过程" : "Thoughts") : `${t("thinking")}…`}
          <ChevronDown className={cn("h-3 w-3 transition-transform", expanded && "rotate-180")} />
        </button>
      )}
      <div className={cn(hasText && "mt-1 border-l-2 border-line pl-3")}>
        {segs.map((s, i) => {
          if (s.kind === "action") return <ToolCard key={i} uuid={s.uuid} />;
          if (s.kind === "text" && s.text.trim())
            return expanded ? <Markdown key={i} text={s.text} className="text-[13px] text-fg-2" /> : null;
          return null;
        })}
        {!expanded && hasText && segs.every((s) => s.kind !== "action") && (
          <div className="line-clamp-1 text-[12px] text-subtle">{deltaToText(m.content).slice(0, 140)}</div>
        )}
      </div>
    </div>
  );
}
