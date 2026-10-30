"use client";
import { create } from "zustand";
import { appendDelta } from "@/lib/delta";
import type {
  ActionData,
  BlockTimeline,
  Boost,
  Chat,
  Deployment,
  Message,
  QueueItem,
  Version,
} from "@/lib/types";

export type PanelKey =
  | "appviewer"
  | "editor"
  | "terminal"
  | "files"
  | "browser"
  | "notebook"
  | "plan"
  | "cloud"
  | "overview"
  | "assets"
  | "research"
  | "growth"
  | "boost";

export const BLOCK_PANEL: Record<string, PanelKey> = {
  Editor: "editor",
  Terminal: "terminal",
  Task: "plan",
  Browser: "browser",
  "Browser-RT": "appviewer",
  Notebook: "notebook",
  Search: "browser",
  Backend: "cloud",
  File: "assets",
  Assets: "assets",
  Research: "research",
  Growth: "growth",
};

export interface ConsoleEntry {
  id: number;
  level: "error" | "warn" | "info" | "log";
  text: string;
  ts: number;
}

interface ChatState {
  chatId: string | null;
  mode: "live" | "replay";
  chat: Chat | null;
  messages: Message[];
  actionDatas: Record<string, ActionData>;
  timelines: Record<number, BlockTimeline>;
  lastSeq: number;
  isRunning: boolean;
  queue: { paused: boolean; items: QueueItem[] };
  versions: Version[];
  deployment: Deployment | null;
  boost: Boost | null;
  build: { status: string; log?: string } | null;
  errors: { id: string; message: string }[];
  disconnected: boolean;
  joinFailed: boolean;
  panel: PanelKey;
  activeIndex: number | null; // selected timeline index for block panels (null = latest/follow)
  follow: boolean;
  consoleLogs: ConsoleEntry[];
  routes: string[];
  currentRoute: string;

  reset: (chatId: string | null, mode?: "live" | "replay") => void;
  set: (p: Partial<ChatState>) => void;
  setPanel: (p: PanelKey, index?: number | null) => void;
  showBlock: (index: number) => void;
  upsertMessage: (m: Message) => void;
  updateMessage: (u: { id: number; delta?: any; content?: any; is_finished?: boolean; metadata?: Record<string, any> }) => void;
  upsertAction: (a: ActionData) => void;
  upsertTimeline: (t: BlockTimeline) => void;
  timelineContent: (index: number, content: string, mode: "append" | "replace") => void;
  timelineComplete: (index: number, file: string | null, extra: any) => void;
  upsertVersion: (v: Version) => void;
  mergeChat: (c: Partial<Chat>) => void;
  addConsole: (e: Omit<ConsoleEntry, "id" | "ts">) => void;
  sortedTimelines: () => BlockTimeline[];
}

let consoleId = 0;

const initial = {
  chatId: null,
  mode: "live" as const,
  chat: null,
  messages: [],
  actionDatas: {},
  timelines: {},
  lastSeq: 0,
  isRunning: false,
  queue: { paused: false, items: [] },
  versions: [],
  deployment: null,
  boost: null,
  build: null,
  errors: [],
  disconnected: false,
  joinFailed: false,
  panel: "appviewer" as PanelKey,
  activeIndex: null,
  follow: true,
  consoleLogs: [],
  routes: ["/"],
  currentRoute: "/",
};

function sortMsgs(ms: Message[]) {
  return ms.sort((a, b) => a.id - b.id);
}

export const useChat = create<ChatState>((set, get) => ({
  ...initial,
  reset: (chatId, mode = "live") => set({ ...initial, chatId, mode, timelines: {}, actionDatas: {}, messages: [] }),
  set: (p) => set(p),
  setPanel: (panel, index = null) => set({ panel, activeIndex: index, follow: index === null }),
  showBlock: (index) => {
    const t = get().timelines[index];
    if (!t) return;
    set({ panel: BLOCK_PANEL[t.block_type] ?? "editor", activeIndex: index, follow: false });
  },
  upsertMessage: (m) =>
    set((s) => {
      if (m.type === "emoji" && m.refer_id) {
        let attached = false;
        const messages = s.messages.map((p) => {
          if (p.id !== m.refer_id) return p;
          attached = true;
          const replies = (p.reply_messages ?? []).filter((r) => r.id !== m.id);
          return { ...p, reply_messages: [...replies, m] };
        });
        if (attached) return { messages };
      }
      const idx = s.messages.findIndex((x) => x.id === m.id);
      if (idx >= 0) {
        const messages = [...s.messages];
        messages[idx] = { ...m, reply_messages: m.reply_messages?.length ? m.reply_messages : s.messages[idx].reply_messages };
        return { messages };
      }
      return { messages: sortMsgs([...s.messages, { ...m, reply_messages: m.reply_messages ?? [] }]) };
    }),
  updateMessage: (u) =>
    set((s) => {
      const idx = s.messages.findIndex((x) => x.id === u.id);
      if (idx < 0) return {};
      const m = { ...s.messages[idx] };
      if (u.content !== undefined) m.content = u.content;
      else if (u.delta) m.content = appendDelta(m.content, u.delta);
      if (u.is_finished !== undefined) m.is_finished = u.is_finished;
      if (u.metadata) m.metadata = { ...m.metadata, ...u.metadata };
      const messages = [...s.messages];
      messages[idx] = m;
      return { messages };
    }),
  upsertAction: (a) => set((s) => ({ actionDatas: { ...s.actionDatas, [a.uuid]: a } })),
  upsertTimeline: (t) =>
    set((s) => {
      const prev = s.timelines[t.index];
      const next = { ...prev, ...t, content: t.content || prev?.content || "" };
      const patch: Partial<ChatState> = { timelines: { ...s.timelines, [t.index]: next } };
      return patch;
    }),
  timelineContent: (index, content, mode) =>
    set((s) => {
      const t = s.timelines[index];
      if (!t) return {};
      return {
        timelines: { ...s.timelines, [index]: { ...t, content: mode === "append" ? (t.content || "") + content : content } },
      };
    }),
  timelineComplete: (index, file, extra) =>
    set((s) => {
      const t = s.timelines[index];
      if (!t) return {};
      return {
        timelines: {
          ...s.timelines,
          [index]: { ...t, file: file ?? t.file, extra_data: extra ?? t.extra_data, is_running: false },
        },
      };
    }),
  upsertVersion: (v) =>
    set((s) => {
      const others = s.versions.filter((x) => x.version !== v.version);
      const versions = [...others, v].sort((a, b) => Number(b.version.slice(1)) - Number(a.version.slice(1)));
      return { versions };
    }),
  mergeChat: (c) =>
    set((s) => {
      if (!s.chat) return {};
      const chat: Chat = {
        ...s.chat,
        ...c,
        metadata: { ...s.chat.metadata, ...(c.metadata ?? {}) },
        config: { ...s.chat.config, ...(c.config ?? {}) },
        uris: { ...s.chat.uris, ...(c.uris ?? {}) },
      } as Chat;
      const isRunning = c.metadata?.is_running ?? s.isRunning;
      return { chat, isRunning };
    }),
  addConsole: (e) =>
    set((s) => ({ consoleLogs: [...s.consoleLogs.slice(-299), { ...e, id: ++consoleId, ts: Date.now() }] })),
  sortedTimelines: () =>
    Object.values(get().timelines).sort((a, b) => a.index - b.index),
}));

export function latestOfTypes(types: string[]): BlockTimeline | undefined {
  const tl = useChat.getState().sortedTimelines();
  for (let i = tl.length - 1; i >= 0; i--) if (types.includes(tl[i].block_type)) return tl[i];
  return undefined;
}
