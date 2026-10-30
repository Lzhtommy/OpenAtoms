"use client";
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { bus } from "@/lib/bus";
import { getSocket } from "@/lib/socket";
import type { ActionData, BlockTimeline, Boost, Chat, Deployment, Message, QueueItem, Version } from "@/lib/types";
import { useChat } from "@/store/chat";
import { useVisualEdit } from "@/store/visual-edit";

interface MessagesResp {
  messages: Message[];
  action_datas: ActionData[];
  round_state: { active_attempts: Record<string, number> };
  is_running: boolean;
  last_seq: number;
}

export async function loadMessages(chatId: string) {
  const d = await api.get<MessagesResp>(`/chats/${chatId}/messages`);
  const st = useChat.getState();
  const ads: Record<string, ActionData> = {};
  for (const a of d.action_datas ?? []) ads[a.uuid] = a;
  st.set({
    messages: (d.messages ?? []).filter((m) => !(m.type === "emoji" && m.refer_id)).sort((a, b) => a.id - b.id),
    actionDatas: { ...st.actionDatas, ...ads },
    isRunning: d.is_running,
    lastSeq: Math.max(st.lastSeq, d.last_seq ?? 0),
  });
  // attach orphan emoji replies (if server returned them flat)
  for (const m of d.messages ?? []) if (m.type === "emoji" && m.refer_id) st.upsertMessage(m);
  return d;
}

export async function loadTimelines(chatId: string) {
  const list = await api.get<BlockTimeline[]>(`/chats/${chatId}/block-timelines`);
  const map: Record<number, BlockTimeline> = {};
  for (const t of list ?? []) map[t.index] = t;
  useChat.getState().set({ timelines: map });
}

export async function loadVersions(chatId: string) {
  const v = await api.get<Version[]>(`/chats/${chatId}/versions`).catch(() => [] as Version[]);
  useChat.getState().set({ versions: [...v].sort((a, b) => Number(b.version.slice(1)) - Number(a.version.slice(1))) });
}

export async function loadQueue(chatId: string) {
  const q = await api.get<{ paused: boolean; items: QueueItem[] }>(`/chats/${chatId}/prompt-queue`).catch(() => null);
  if (q) useChat.getState().set({ queue: q });
}

export async function loadDeployment(chatId: string) {
  const d = await api.get<Deployment | null>(`/chats/${chatId}/deployment`).catch(() => null);
  useChat.getState().set({ deployment: d });
}

export async function loadBoost(chatId: string) {
  const b = await api.get<Boost | null>(`/chats/${chatId}/boost`).catch(() => null);
  useChat.getState().set({ boost: b });
}

export async function reloadAll(chatId: string) {
  const chat = await api.get<Chat>(`/chats/${chatId}`);
  useChat.getState().set({ chat, isRunning: chat.metadata.is_running });
  await Promise.all([loadMessages(chatId), loadTimelines(chatId), loadVersions(chatId), loadQueue(chatId), loadDeployment(chatId), loadBoost(chatId)]);
}

/** Apply one server event. Returns false if it was a duplicate. */
export function applyEvent(event: string, p: any): boolean {
  const st = useChat.getState();
  if (p?.chat_id && p.chat_id !== st.chatId) return false;
  if (typeof p?.seq === "number") {
    if (p.seq <= st.lastSeq) return false;
    st.set({ lastSeq: p.seq });
  }
  switch (event) {
    case "msg:create":
      st.upsertMessage(p.message as Message);
      break;
    case "msg:update":
      st.updateMessage(p);
      break;
    case "edit_session:update": {
      const ve = useVisualEdit.getState();
      if (ve.chatId === st.chatId) ve.set({ staged: p.edits ?? [] });
      break;
    }
    case "action_data:update":
      st.upsertAction(p.action_data);
      break;
    case "timeline:start": {
      const t = p.timeline as BlockTimeline;
      st.upsertTimeline({ ...t, is_running: true });
      bus.emit("timeline:start", t);
      break;
    }
    case "timeline:content":
      st.timelineContent(p.index, p.content ?? "", p.mode === "replace" ? "replace" : "append");
      bus.emit("timeline:content", p);
      break;
    case "timeline:complete":
      st.timelineComplete(p.index, p.file ?? null, p.extra_data ?? null);
      bus.emit("timeline:complete", p);
      break;
    case "version:create":
    case "version:update":
    case "version:complete":
      st.upsertVersion(p.version);
      if (event === "version:complete") {
        const v = p.version as Version;
        st.mergeChat({ version: { version: v.version, summary: v.summary, cover: v.cover, link: v.link } });
        bus.emit("appviewer:reload");
      }
      break;
    case "build:update":
    case "build:error":
    case "build:complete":
      st.set({ build: { status: p.status, log: p.log } });
      break;
    case "deploy:update":
      st.set({ deployment: p.deployment });
      break;
    case "chat:update":
      st.mergeChat(p.chat ?? {});
      if (p.chat?.metadata?.status === "running") bus.emit("appviewer:runtime-ready");
      break;
    case "prompt_queue:update":
      st.set({ queue: { paused: !!p.paused, items: p.items ?? [] } });
      break;
    case "prompt_queue:item_sent":
      break;
    case "chat_error:create":
      st.set({ errors: [...st.errors.filter((e) => e.id !== p.error?.id), p.error] });
      break;
    case "chat_error:delete":
      st.set({ errors: st.errors.filter((e) => e.id !== p.error?.id) });
      break;
    case "disk:quota:update":
    case "disk:exceeded": {
      const others = st.errors.filter((e) => e.id !== "quota-disk");
      if (p.exceeded) {
        const mb = (n: number) => Math.round(n / 1048576);
        const message = `Disk quota exceeded: ${mb(p.used_bytes)} MB of ${mb(p.limit_bytes)} MB. Delete large files or upgrade your plan to continue.`;
        st.set({ errors: [...others, { id: "quota-disk", message }] });
      } else if (others.length !== st.errors.length) st.set({ errors: others });
      break;
    }
    case "quota:daily_exhausted":
      st.set({
        errors: [
          ...st.errors.filter((e) => e.id !== "quota-daily"),
          { id: "quota-daily", message: `Daily credit limit reached (${p.used_today}/${p.daily_limit}). It resets at 00:00 UTC.` },
        ],
      });
      break;
    case "boost:update":
    case "boost:all_end":
    case "boost:merge_end":
      if (p.boost) st.set({ boost: p.boost as Boost });
      if (event === "boost:all_end" && p.boost?.status === "ready") {
        toast.success("All candidates are ready — compare and pick one");
        st.set({ panel: "boost", follow: false });
      }
      bus.emit(event, p);
      break;
    case "automation:update":
      bus.emit(event, p);
      break;
    case "run:start":
      st.set({ isRunning: true });
      st.mergeChat({ metadata: { ...(st.chat?.metadata as Chat["metadata"]), is_running: true } });
      break;
    case "run:end":
      st.set({ isRunning: false });
      st.mergeChat({ metadata: { ...(st.chat?.metadata as Chat["metadata"]), is_running: false } });
      if (p.status === "failed") toast.error("Run failed");
      bus.emit("run:end", p);
      break;
  }
  return true;
}

// Server events are consumed via socket.onAny (see useChatSession); applyEvent switches on the name.

// Live events that arrive while a catch-up is in flight are buffered and applied after it, in seq order:
// applying them immediately would advance lastSeq past the replayed events and make those look like duplicates.
let catchingUp: Promise<void> | null = null;
let pending: { ev: string; p: any }[] = [];

function flushPending() {
  const evs = pending.sort((a, b) => a.p.seq - b.p.seq);
  pending = [];
  for (const { ev, p } of evs) applyEvent(ev, p);
}

function catchUp(chatId: string): Promise<void> {
  if (catchingUp) return catchingUp;
  catchingUp = (async () => {
    try {
      const after = useChat.getState().lastSeq;
      const d = await api.get<{ events: { seq: number; event: string; payload: any }[]; complete: boolean }>(
        `/chats/${chatId}/events`,
        { after },
      );
      if (!d.complete) {
        await reloadAll(chatId);
        return;
      }
      for (const e of d.events.sort((a, b) => a.seq - b.seq)) applyEvent(e.event, e.payload);
    } catch {
      /* ignore */
    }
  })().finally(() => {
    catchingUp = null;
    flushPending();
  });
  return catchingUp;
}

export function useChatSession(chatId: string) {
  const loadedRef = useRef(false);
  useEffect(() => {
    const st = useChat.getState();
    st.reset(chatId, "live");
    pending = [];
    loadedRef.current = false;
    let alive = true;
    const socket = getSocket();
    let ackTimer: ReturnType<typeof setTimeout> | null = null;
    let connectErrors = 0;

    const join = () => {
      socket.emit("join_chat", { chat_id: chatId });
      if (ackTimer) clearTimeout(ackTimer);
      ackTimer = setTimeout(() => useChat.getState().set({ joinFailed: true }), 3000);
    };
    const onAck = (p: { chat_id: string; ok: boolean; code: number }) => {
      if (p.chat_id !== chatId) return;
      if (ackTimer) clearTimeout(ackTimer);
      useChat.getState().set({ joinFailed: !p.ok });
      if (!p.ok && p.code === 4403) toast.error("No access to this project");
      if (p.ok && loadedRef.current) catchUp(chatId);
    };
    const onConnect = () => {
      connectErrors = 0;
      useChat.getState().set({ disconnected: false });
      join();
    };
    const onDisconnect = () => useChat.getState().set({ disconnected: true });
    const onConnectError = () => {
      connectErrors++;
      if (connectErrors >= 3) useChat.getState().set({ disconnected: true });
    };
    // Every chat event carries the chat's shared `seq`, so listen to all of them (not a hand-kept list):
    // an unhandled seq-bearing event would otherwise look like a gap and could make catch-up skip messages.
    const onAnyEvent = (ev: string, p: any) => {
      if (!alive || !p || p.chat_id !== chatId || typeof p.seq !== "number" || !loadedRef.current) return;
      const st2 = useChat.getState();
      if (catchingUp || (st2.lastSeq > 0 && p.seq > st2.lastSeq + 1)) {
        pending.push({ ev, p });
        catchUp(chatId);
        return;
      }
      applyEvent(ev, p);
    };
    socket.onAny(onAnyEvent);
    const onQuota = () => useAuth.getState().refreshBalance();

    socket.on("join_chat_ack", onAck);
    socket.on("connect", onConnect);
    socket.on("disconnect", onDisconnect);
    socket.on("connect_error", onConnectError);
    socket.on("quota:update", onQuota);
    if (socket.connected) join();

    reloadAll(chatId)
      .then(() => {
        loadedRef.current = true;
        if (alive) catchUp(chatId);
      })
      .catch((e) => toast.error(errMsg(e)));

    return () => {
      alive = false;
      if (ackTimer) clearTimeout(ackTimer);
      socket.emit("leave_chat", { chat_id: chatId });
      socket.off("join_chat_ack", onAck);
      socket.off("connect", onConnect);
      socket.off("disconnect", onDisconnect);
      socket.off("connect_error", onConnectError);
      socket.off("quota:update", onQuota);
      socket.offAny(onAnyEvent);
    };
  }, [chatId]);
}
