"use client";
import { GitFork, Heart, Pause, Play, SkipForward } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Workbench } from "@/components/chat/workbench";
import { FullPageLoader } from "@/components/shell/auth-guard";
import { Button } from "@/components/ui/button";
import { api, errMsg, tokenStore } from "@/lib/api";
import type { ActionData, BlockTimeline, Chat, Message, Version } from "@/lib/types";
import { cn, parseTs } from "@/lib/utils";
import { useChat } from "@/store/chat";

interface ShareData {
  chat: Chat;
  messages: Message[];
  action_datas: ActionData[];
  timelines: BlockTimeline[];
  versions: Version[];
}

type Step = { kind: "m"; at: number; m: Message } | { kind: "t"; at: number; t: BlockTimeline };

function ShareInner() {
  const { id } = useParams<{ id: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const [data, setData] = useState<ShareData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    useChat.getState().reset(id, "replay");
    api
      .get<ShareData>(`/share/${id}`, { version: sp.get("version") ?? undefined }, { noAuthRedirect: true })
      .then((d) => {
        setData(d);
        const ads: Record<string, ActionData> = {};
        for (const a of d.action_datas) ads[a.uuid] = a;
        useChat.getState().set({ chat: d.chat, actionDatas: ads, versions: d.versions });
      })
      .catch((e) => setErr(errMsg(e)));
  }, [id]);

  const steps = useMemo<Step[]>(() => {
    if (!data) return [];
    const s: Step[] = [
      ...data.messages.map((m) => ({ kind: "m" as const, at: parseTs(m.created_at), m })),
      ...data.timelines.map((t) => ({ kind: "t" as const, at: parseTs(t.created_at), t })),
    ];
    return s.sort((a, b) => a.at - b.at);
  }, [data]);

  useEffect(() => {
    if (steps.length) setPos(steps.length);
  }, [steps.length]);

  // apply replay position to the store
  useEffect(() => {
    if (!data) return;
    const visible = steps.slice(0, pos);
    const messages = visible.filter((s): s is Extract<Step, { kind: "m" }> => s.kind === "m").map((s) => s.m);
    const tl: Record<number, BlockTimeline> = {};
    let lastIdx: number | null = null;
    for (const s of visible)
      if (s.kind === "t") {
        tl[s.t.index] = s.t;
        lastIdx = s.t.index;
      }
    const st = useChat.getState();
    st.set({ messages: messages.filter((m) => !(m.type === "emoji" && m.refer_id)), timelines: tl });
    for (const m of messages) if (m.type === "emoji" && m.refer_id) st.upsertMessage(m);
    if (playing && lastIdx !== null) st.showBlock(lastIdx);
  }, [pos, steps]);

  useEffect(() => {
    if (!playing) return;
    if (pos >= steps.length) {
      setPlaying(false);
      return;
    }
    const id2 = setTimeout(() => setPos((p) => p + 1), 900 / speed);
    return () => clearTimeout(id2);
  }, [playing, pos, speed, steps.length]);

  if (err)
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 text-[14px] text-muted">
        {err}
        <Button variant="outline" onClick={() => router.push("/")}>
          Home
        </Button>
      </div>
    );
  if (!data) return <FullPageLoader />;

  const banner = (
    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-card px-3 text-[12px]">
      <span className="rounded-full bg-brand-soft px-2 py-0.5 font-medium text-brand">Replay</span>
      <Button
        size="icon-sm"
        variant="ghost"
        onClick={() => {
          if (pos >= steps.length) setPos(0);
          setPlaying(!playing);
        }}
      >
        {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
      </Button>
      <input type="range" min={0} max={steps.length} value={pos} onChange={(e) => (setPlaying(false), setPos(Number(e.target.value)))} className="min-w-0 flex-1 accent-[var(--brand)]" />
      <span className="w-16 text-center text-muted">
        {pos}/{steps.length}
      </span>
      {[1, 2, 4].map((s) => (
        <button key={s} onClick={() => setSpeed(s)} className={cn("cursor-pointer rounded-full px-1.5", speed === s ? "bg-inverse text-inverse-fg" : "text-muted")}>
          {s}x
        </button>
      ))}
      <Button size="icon-sm" variant="ghost" onClick={() => (setPlaying(false), setPos(steps.length))}>
        <SkipForward className="h-3.5 w-3.5" />
      </Button>
      <div className="mx-1 h-5 w-px bg-line" />
      <Button
        size="sm"
        variant="outline"
        onClick={async () => {
          if (!tokenStore.get()) return router.push(`/login?next=/share/${id}`);
          try {
            if (data.chat.is_liked) await api.del(`/chats/${id}/like`);
            else await api.post(`/chats/${id}/like`);
            const liked = !data.chat.is_liked;
            const chat = { ...data.chat, is_liked: liked, stats: { ...data.chat.stats, likes: data.chat.stats.likes + (liked ? 1 : -1) } };
            setData({ ...data, chat });
            useChat.getState().set({ chat });
          } catch (e) {
            toast.error(errMsg(e));
          }
        }}
      >
        <Heart className={cn("h-3.5 w-3.5", data.chat.is_liked && "fill-danger text-danger")} />
        {data.chat.stats.likes}
      </Button>
      <Button
        size="sm"
        onClick={async () => {
          if (!tokenStore.get()) return router.push(`/login?next=/share/${id}`);
          try {
            const c = await api.post<Chat>(`/chats/${id}/fork`, { version: sp.get("version") ?? data.chat.version?.version });
            router.push(`/chat/${c.chat_id}`);
          } catch (e) {
            toast.error(errMsg(e));
          }
        }}
      >
        <GitFork className="h-3.5 w-3.5" /> Remix
      </Button>
    </div>
  );
  return <Workbench readOnly banner={banner} />;
}

export default function SharePage() {
  return (
    <Suspense>
      <ShareInner />
    </Suspense>
  );
}
