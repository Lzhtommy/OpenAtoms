"use client";
import { CalendarClock, History, Play, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { Badge, Empty } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, errMsg } from "@/lib/api";
import { deltaToText } from "@/lib/delta";
import { useLang, useT } from "@/lib/i18n";
import type { Automation, Chat, Message, Page } from "@/lib/types";
import { fmtDate, fmtTime } from "@/lib/utils";

const formatTime = (iso: string) => `${fmtDate(iso)} ${fmtTime(iso)}`;

interface Preset {
  label: string;
  cron: string;
}

const TIMEZONES = ["UTC", "Asia/Shanghai", "Asia/Tokyo", "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles"];

function localTz() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function CreateDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: () => void }) {
  const zh = useLang((s) => s.lang) === "zh";
  const [projects, setProjects] = useState<Chat[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [chatId, setChatId] = useState("");
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [tz, setTz] = useState(localTz());
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    api.get<Page<Chat>>("/chats", { cur_page: 1, page_num: 100 }).then((d) => {
      setProjects(d.items);
      if (!chatId && d.items[0]) setChatId(d.items[0].chat_id);
    }).catch(() => {});
    api.get<Preset[]>("/automations/presets").then(setPresets).catch(() => {});
  }, [open]);
  const create = async () => {
    setBusy(true);
    try {
      await api.post("/automations", { chat_id: chatId, name: name.trim(), prompt: prompt.trim(), cron: cron.trim(), timezone: tz });
      toast.success(zh ? "已创建定时任务" : "Automation created");
      onOpenChange(false);
      setName("");
      setPrompt("");
      onCreated();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const tzs = TIMEZONES.includes(tz) ? TIMEZONES : [tz, ...TIMEZONES];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={zh ? "新建定时任务" : "New automation"}
        description={zh ? "按计划把提示词发送到项目，由智能体团队自动执行。" : "Send a prompt to a project on a schedule; the agents run it automatically."}
      >
        <div className="flex flex-col gap-3 text-[13px]">
          <label className="flex flex-col gap-1">
            {zh ? "项目" : "Project"}
            <select
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              className="h-9 cursor-pointer rounded-m border border-line-strong bg-card px-2 outline-none"
            >
              {projects.map((p) => (
                <option key={p.chat_id} value={p.chat_id}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            {zh ? "名称" : "Name"}
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={zh ? "每日数据报告" : "Daily report"} />
          </label>
          <label className="flex flex-col gap-1">
            {zh ? "提示词" : "Prompt"}
            <Textarea
              rows={3}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={zh ? "汇总昨天新增的留言并更新首页统计" : "Summarise yesterday's signups and update the stats section"}
            />
          </label>
          <div className="flex flex-col gap-1">
            {zh ? "计划（cron）" : "Schedule (cron)"}
            <div className="flex flex-wrap gap-1.5">
              {presets.map((p) => (
                <button
                  key={p.cron}
                  onClick={() => setCron(p.cron)}
                  className={`cursor-pointer rounded-full border px-2 py-0.5 text-[12px] ${cron === p.cron ? "border-brand bg-brand-soft text-brand" : "border-line hover:bg-hover"}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <Input value={cron} onChange={(e) => setCron(e.target.value)} className="font-mono" aria-label="cron expression" />
              <select
                value={tz}
                onChange={(e) => setTz(e.target.value)}
                className="h-9 cursor-pointer rounded-m border border-line-strong bg-card px-2 outline-none"
                aria-label="timezone"
              >
                {tzs.map((z) => (
                  <option key={z}>{z}</option>
                ))}
              </select>
            </div>
            <span className="text-[11px] text-muted">minute hour day-of-month month day-of-week · e.g. */30 * * * * · 0 9 * * 1-5</span>
          </div>
          <div className="flex justify-end">
            <Button onClick={create} loading={busy} disabled={!chatId || !name.trim() || !prompt.trim() || !cron.trim()}>
              <Plus className="h-3.5 w-3.5" /> {zh ? "创建" : "Create"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function HistoryDialog({ a, onClose }: { a: Automation | null; onClose: () => void }) {
  const zh = useLang((s) => s.lang) === "zh";
  const [items, setItems] = useState<Message[] | null>(null);
  useEffect(() => {
    setItems(null);
    if (a) api.get<Message[]>(`/automations/${a.id}/history`).then(setItems).catch(() => setItems([]));
  }, [a?.id]);
  return (
    <Dialog open={!!a} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={a ? `${a.name} — ${zh ? "运行记录" : "history"}` : ""}>
        {items === null ? (
          <div className="skeleton h-20" />
        ) : items.length === 0 ? (
          <Empty>{zh ? "还没有运行过" : "No runs yet"}</Empty>
        ) : (
          <ul className="flex max-h-[50vh] flex-col gap-2 overflow-y-auto">
            {items.map((m) => (
              <li key={m.id} className="rounded-l border border-line p-2.5 text-[13px]">
                <div className="flex items-center gap-2 text-[11px] text-muted">
                  {formatTime(m.created_at)}
                  {m.metadata?.automation?.manual && <Badge>manual</Badge>}
                </div>
                <div className="mt-1 line-clamp-2">{deltaToText(m.content)}</div>
              </li>
            ))}
          </ul>
        )}
        {a && (
          <div className="mt-3 flex justify-end">
            <Link href={`/chat/${a.chat_id}`}>
              <Button size="xs" variant="outline">
                {zh ? "打开项目" : "Open project"}
              </Button>
            </Link>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Inner() {
  const t = useT();
  const zh = useLang((s) => s.lang) === "zh";
  const [list, setList] = useState<Automation[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [history, setHistory] = useState<Automation | null>(null);
  const load = useCallback(() => {
    api.get<Automation[]>("/automations").then(setList).catch((e) => (toast.error(errMsg(e)), setList([])));
  }, []);
  useEffect(load, [load]);
  const patch = async (a: Automation, body: Partial<Automation>) => {
    try {
      const n = await api.patch<Automation>(`/automations/${a.id}`, body);
      setList((l) => (l ?? []).map((x) => (x.id === a.id ? n : x)));
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const run = async (a: Automation) => {
    try {
      const n = await api.post<Automation>(`/automations/${a.id}/run`);
      setList((l) => (l ?? []).map((x) => (x.id === a.id ? n : x)));
      toast.success(n.last_status === "queued" ? (zh ? "项目忙，已加入队列" : "Project busy — queued") : zh ? "已发送" : "Sent");
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const remove = async (a: Automation) => {
    try {
      await api.del(`/automations/${a.id}`);
      setList((l) => (l ?? []).filter((x) => x.id !== a.id));
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-center gap-3">
        <h1 className="font-serif text-[28px]">{t("automations")}</h1>
        <div className="flex-1" />
        <Button onClick={() => setCreating(true)}>
          <Plus className="h-3.5 w-3.5" /> {zh ? "新建" : "New automation"}
        </Button>
      </div>
      {list === null ? (
        <div className="skeleton h-32 rounded-xl" />
      ) : list.length === 0 ? (
        <Empty>
          <CalendarClock className="h-8 w-8 text-subtle" />
          {zh ? "按计划自动运行提示词，例如每天早上更新数据看板。" : "Run prompts on a schedule — e.g. refresh a dashboard every morning."}
        </Empty>
      ) : (
        <div className="overflow-hidden rounded-xl border border-line bg-card">
          {list.map((a) => (
            <div key={a.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 last:border-0">
              <Switch checked={a.enabled} onCheckedChange={(v) => patch(a, { enabled: v })} aria-label="enabled" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[14px] font-medium">{a.name}</span>
                  {a.last_status && <Badge tone={a.last_status === "failed" ? "danger" : "positive"}>{a.last_status}</Badge>}
                </div>
                <div className="truncate text-[12px] text-muted">
                  <Link href={`/chat/${a.chat_id}`} className="hover:text-fg">
                    {a.chat_title ?? a.chat_id}
                  </Link>{" "}
                  · {a.schedule} ({a.timezone}) · {a.prompt}
                </div>
              </div>
              <div className="text-right text-[11px] text-muted">
                <div>
                  {zh ? "下次" : "Next"}: {a.next_run_at ? formatTime(a.next_run_at) : "—"}
                </div>
                <div>
                  {zh ? "上次" : "Last"}: {a.last_run_at ? formatTime(a.last_run_at) : "—"} · {a.run_count}×
                </div>
              </div>
              <Button size="icon-sm" variant="ghost" onClick={() => run(a)} aria-label="run now">
                <Play className="h-3.5 w-3.5" />
              </Button>
              <Button size="icon-sm" variant="ghost" onClick={() => setHistory(a)} aria-label="history">
                <History className="h-3.5 w-3.5" />
              </Button>
              <Button size="icon-sm" variant="ghost" onClick={() => remove(a)} aria-label="delete">
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}
      <CreateDialog open={creating} onOpenChange={setCreating} onCreated={load} />
      <HistoryDialog a={history} onClose={() => setHistory(null)} />
    </div>
  );
}

export default function AutomationsPage() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}
