"use client";
import { AlertTriangle, CheckCircle2, ClipboardList, ExternalLink, History, HelpCircle, ShieldAlert, Trophy } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { AgentAvatar } from "@/components/ui/avatar";
import { boostActions, chatActions } from "@/lib/chat-actions";
import { useLang, useT } from "@/lib/i18n";
import type { Boost, BoostItem, Message, Task } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useChat } from "@/store/chat";

export function TaskCard({ m, readOnly }: { m: Message; readOnly?: boolean }) {
  const c = m.content || {};
  const [busy, setBusy] = useState<number | null>(null);
  const finished = c.status === "finished";
  const options: { label: string; content: string; danger?: boolean }[] = c.option ?? [];
  return (
    <div className="mt-1 rounded-l border border-line bg-card p-3.5 shadow-card">
      <div className="flex items-center gap-2 text-[13px] font-medium">
        <ShieldAlert className="h-4 w-4 text-warning" />
        {c.name || "Decision needed"}
        {finished && <Badge tone="positive">done</Badge>}
      </div>
      {c.description && <p className="mt-1.5 text-[13px] whitespace-pre-wrap text-fg-2">{c.description}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {options.map((o, i) => (
          <Button
            key={i}
            size="sm"
            variant={o.danger ? "outline" : "primary"}
            className={cn(o.danger && "text-danger", finished && c.selected === i && "ring-2 ring-brand")}
            disabled={finished || readOnly || busy !== null}
            loading={busy === i}
            onClick={async () => {
              setBusy(i);
              await chatActions.send(m.chat_id, {
                content: [{ insert: o.content }],
                type: "message",
                refer_id: m.id,
                metadata: { task_reply: { message_id: m.id, option_index: i } },
              });
              setBusy(null);
            }}
          >
            {o.label}
          </Button>
        ))}
      </div>
    </div>
  );
}

interface Q {
  id: string;
  question: string;
  type: "single" | "multi" | "text";
  options?: string[];
}

export function QuestionsCard({ m, readOnly }: { m: Message; readOnly?: boolean }) {
  const t = useT();
  const c = m.content || {};
  const questions: Q[] = c.questions ?? [];
  const finished = c.status === "finished";
  const [answers, setAnswers] = useState<Record<string, string | string[]>>(c.answers ?? {});
  const [busy, setBusy] = useState(false);
  const setA = (id: string, v: string | string[]) => setAnswers((a) => ({ ...a, [id]: v }));
  return (
    <div className="mt-1 rounded-l border border-line bg-card p-3.5 shadow-card">
      <div className="flex items-center gap-2 text-[13px] font-medium">
        <HelpCircle className="h-4 w-4 text-brand" />
        {c.title || "A few questions"}
        {finished && <Badge tone="positive">submitted</Badge>}
      </div>
      <div className="mt-3 flex flex-col gap-4">
        {questions.map((q, qi) => (
          <div key={q.id}>
            <div className="mb-1.5 text-[13px] text-fg">
              {qi + 1}. {q.question}
            </div>
            {q.type === "text" ? (
              <Textarea
                rows={2}
                disabled={finished || readOnly}
                value={(answers[q.id] as string) ?? ""}
                onChange={(e) => setA(q.id, e.target.value)}
              />
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {(q.options ?? []).map((o) => {
                  const cur = answers[q.id];
                  const selected = q.type === "multi" ? Array.isArray(cur) && cur.includes(o) : cur === o;
                  return (
                    <button
                      key={o}
                      disabled={finished || readOnly}
                      onClick={() => {
                        if (q.type === "multi") {
                          const arr = Array.isArray(cur) ? cur : [];
                          setA(q.id, selected ? arr.filter((x) => x !== o) : [...arr, o]);
                        } else setA(q.id, o);
                      }}
                      className={cn(
                        "cursor-pointer rounded-full border px-3 py-1 text-[12px] transition-colors disabled:cursor-default",
                        selected ? "border-brand bg-brand-soft text-brand" : "border-line-strong hover:bg-hover",
                      )}
                    >
                      {o}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>
      {!finished && !readOnly && (
        <div className="mt-4 flex justify-end">
          <Button
            size="sm"
            loading={busy}
            onClick={async () => {
              setBusy(true);
              await chatActions.submitQuestions(m.chat_id, m.id, answers);
              setBusy(false);
            }}
          >
            {t("submit")}
          </Button>
        </div>
      )}
    </div>
  );
}

export function PlanReadyCard({ m, readOnly }: { m: Message; readOnly?: boolean }) {
  const t = useT();
  const c = m.content || {};
  const tasks: Task[] = c.tasks ?? [];
  const status: string = c.status ?? "pending";
  const [busy, setBusy] = useState<"a" | "d" | null>(null);
  return (
    <div className="mt-1 rounded-l border border-line bg-card p-3.5 shadow-card">
      <div className="flex items-center gap-2 text-[13px] font-medium">
        <ClipboardList className="h-4 w-4 text-brand" />
        Plan draft
        <Badge tone={status === "approved" ? "positive" : status === "declined" ? "danger" : "brand"}>{status}</Badge>
      </div>
      {c.summary && <p className="mt-1.5 text-[13px] text-fg-2">{c.summary}</p>}
      <ol className="mt-3 flex flex-col gap-2">
        {tasks.map((tk, i) => (
          <li key={tk.id ?? i} className="flex items-start gap-2.5 rounded-m bg-bg-2 px-2.5 py-2">
            <span className="mt-0.5 text-[12px] text-muted">{i + 1}</span>
            <AgentAvatar role={tk.assignee} size={20} />
            <div className="min-w-0 flex-1">
              <div className="text-[13px]">{tk.title}</div>
              {tk.instruction && <div className="mt-0.5 line-clamp-2 text-[12px] text-muted">{tk.instruction}</div>}
            </div>
          </li>
        ))}
      </ol>
      {status === "pending" && !readOnly && (
        <div className="mt-3 flex justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            loading={busy === "d"}
            onClick={async () => {
              setBusy("d");
              await chatActions.declinePlan(m.chat_id);
              setBusy(null);
            }}
          >
            {t("decline")}
          </Button>
          <Button
            size="sm"
            loading={busy === "a"}
            onClick={async () => {
              setBusy("a");
              await chatActions.approvePlan(m.chat_id);
              setBusy(null);
            }}
          >
            {t("approve")}
          </Button>
        </div>
      )}
    </div>
  );
}

export function VersionCard({ m, readOnly }: { m: Message; readOnly?: boolean }) {
  const t = useT();
  const c = m.content || {};
  return (
    <div className="mt-1 flex items-center gap-3 rounded-l border border-line bg-card p-3 shadow-card">
      <span className="flex h-9 w-9 items-center justify-center rounded-m bg-brand-soft text-brand">
        <CheckCircle2 className="h-4.5 w-4.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium">{c.version}</div>
        <div className="truncate text-[12px] text-muted">{c.summary}</div>
      </div>
      {c.link && (
        <a href={c.link} target="_blank" rel="noreferrer">
          <Button size="xs" variant="outline">
            <ExternalLink className="h-3 w-3" />
            {t("open")}
          </Button>
        </a>
      )}
      {!readOnly && (
        <Button size="xs" variant="ghost" onClick={() => chatActions.restore(m.chat_id, c.version)}>
          <History className="h-3 w-3" />
        </Button>
      )}
    </div>
  );
}

export function AlertCard({ m }: { m: Message }) {
  const c = m.content || {};
  const isQuota = m.type === "quota";
  return (
    <div
      className={cn(
        "mt-1 flex items-start gap-2 rounded-l border p-3 text-[13px]",
        isQuota ? "border-warning/30 bg-warning-soft text-warning" : "border-danger/30 bg-danger-soft text-danger",
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1 break-words">
        {typeof c === "string" ? c : c.message ?? JSON.stringify(c)}
        {isQuota && (
          <a href="/pricing" className="ml-2 underline">
            Upgrade
          </a>
        )}
      </div>
    </div>
  );
}

/** `boost` message: Best-of-N progress + quick pick (full comparison lives in the Boost panel). */
export function BoostCard({ m, readOnly }: { m: Message; readOnly?: boolean }) {
  const zh = useLang((s) => s.lang) === "zh";
  const live = useChat((s) => (s.boost && s.boost.id === m.content?.boost_id ? s.boost : null));
  const c: Boost = (live ?? m.content ?? {}) as Boost;
  const items: BoostItem[] = c.items ?? [];
  const done = items.filter((i) => i.status === "success" || i.status === "failed" || i.status === "cancelled").length;
  return (
    <div className="mt-1 rounded-l border border-line bg-card p-3 shadow-card">
      <div className="flex items-center gap-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-m bg-brand-soft text-brand">
          <Trophy className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium">
            {zh ? "竞赛模式" : "Boost"} · {items.length} {zh ? "个候选" : "candidates"}
          </div>
          <div className="text-[12px] text-muted">
            {c.status === "running"
              ? zh ? `构建中 ${done}/${items.length}` : `Building ${done}/${items.length}`
              : c.status === "ready"
                ? zh ? "全部完成，请挑选一个合并" : "All done — pick one to merge"
                : c.status === "merged" && c.selected != null
                  ? zh ? `已合并 #${c.selected + 1}` : `Merged #${c.selected + 1}`
                  : c.status}
          </div>
        </div>
        <Button size="xs" variant="outline" onClick={() => useChat.getState().set({ panel: "boost", follow: false })}>
          {zh ? "对比" : "Compare"}
        </Button>
      </div>
      <div className="mt-2 flex flex-col gap-1">
        {items.map((it) => (
          <div key={it.index} className="flex items-center gap-2 text-[12px]">
            <span className="w-6 text-subtle">#{it.index + 1}</span>
            <span className="min-w-0 flex-1 truncate">{it.model}</span>
            {c.recommended === it.index && <span className="rounded-full bg-brand-soft px-1.5 text-[10px] text-brand">{zh ? "推荐" : "best"}</span>}
            <span className="text-muted">{it.status}</span>
            {!readOnly && c.status === "ready" && it.status === "success" && (
              <Button size="xs" variant="ghost" onClick={() => boostActions.select(m.chat_id, it.index)}>
                {zh ? "选择" : "Pick"}
              </Button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
