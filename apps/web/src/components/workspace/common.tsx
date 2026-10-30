"use client";
import { ChevronLeft, ChevronRight, Radio } from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { AgentAvatar } from "@/components/ui/avatar";
import { fetchFileText } from "@/lib/api";
import type { BlockTimeline } from "@/lib/types";
import { basename, cn } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { BLOCK_ICON } from "@/components/chat/tool-card";

/** Timelines of given block types, plus the currently selected one (activeIndex or latest when following). */
export function useBlockSelection(types: string[]) {
  const timelines = useChat((s) => s.timelines);
  const activeIndex = useChat((s) => s.activeIndex);
  const follow = useChat((s) => s.follow);
  const list = useMemo(
    () => Object.values(timelines).filter((t) => types.includes(t.block_type)).sort((a, b) => a.index - b.index),
    [timelines, types.join(",")],
  );
  let selected: BlockTimeline | undefined;
  if (!follow && activeIndex !== null) selected = list.find((t) => t.index === activeIndex);
  if (!selected) selected = list[list.length - 1];
  return { list, selected };
}

/** Load snapshot file content for a finished block (falls back to inline content). */
export function useSnapshot(t: BlockTimeline | undefined) {
  const chatId = useChat((s) => s.chatId);
  const mode = useChat((s) => s.mode);
  const [snap, setSnap] = useState<string | null>(null);
  useEffect(() => {
    setSnap(null);
    if (!t || t.is_running || !t.file || !chatId || mode === "replay") return;
    let alive = true;
    fetchFileText(chatId, t.file)
      .then((s) => alive && setSnap(s))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [t?.index, t?.file, t?.is_running, chatId]);
  return snap;
}

export function PanelEmpty({ icon: Icon, text }: { icon?: React.ComponentType<{ className?: string }>; text: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-[13px] text-muted">
      {Icon && <Icon className="h-8 w-8 text-subtle" />}
      {text}
    </div>
  );
}

export function PanelBar({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("flex h-[34px] shrink-0 items-center gap-1.5 border-b border-line px-2.5 text-[12px]", className)}>{children}</div>;
}

/** Bottom scrubber across ALL timelines; selecting one switches the panel to that block. */
export function TimelineScrubber() {
  const timelines = useChat((s) => s.timelines);
  const activeIndex = useChat((s) => s.activeIndex);
  const follow = useChat((s) => s.follow);
  const showBlock = useChat((s) => s.showBlock);
  const set = useChat((s) => s.set);
  const list = useMemo(() => Object.values(timelines).sort((a, b) => a.index - b.index), [timelines]);
  if (!list.length) return null;
  const pos = follow || activeIndex === null ? list.length - 1 : Math.max(0, list.findIndex((t) => t.index === activeIndex));
  const cur = list[pos];
  const Icon = cur ? BLOCK_ICON[cur.block_type] : undefined;
  const go = (p: number) => {
    const t = list[Math.max(0, Math.min(list.length - 1, p))];
    if (t) showBlock(t.index);
  };
  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-t border-line bg-card px-2.5 text-[12px]">
      <button className="cursor-pointer rounded p-1 hover:bg-hover disabled:opacity-30" disabled={pos <= 0} onClick={() => go(pos - 1)}>
        <ChevronLeft className="h-3.5 w-3.5" />
      </button>
      <button className="cursor-pointer rounded p-1 hover:bg-hover disabled:opacity-30" disabled={pos >= list.length - 1} onClick={() => go(pos + 1)}>
        <ChevronRight className="h-3.5 w-3.5" />
      </button>
      <input
        type="range"
        min={0}
        max={list.length - 1}
        value={pos}
        onChange={(e) => go(Number(e.target.value))}
        className="min-w-0 flex-1 accent-[var(--brand)]"
      />
      <span className="flex max-w-[45%] min-w-0 items-center gap-1.5 text-muted">
        {cur && <AgentAvatar role={cur.block_owner} size={16} />}
        {Icon && <Icon className="h-3.5 w-3.5 shrink-0" />}
        <span className="truncate">
          {cur?.command_name} {cur?.src_path || cur?.extra_data?.path ? basename(cur?.src_path || cur?.extra_data?.path) : ""}
        </span>
        <span className="shrink-0 text-subtle">
          {pos + 1}/{list.length}
        </span>
      </span>
      <button
        onClick={() => set({ follow: true, activeIndex: null })}
        className={cn("flex cursor-pointer items-center gap-1 rounded-full px-2 py-0.5", follow ? "bg-brand-soft text-brand" : "text-muted hover:bg-hover")}
      >
        <Radio className="h-3 w-3" /> Live
      </button>
    </div>
  );
}

/** Keeps one broken panel (e.g. an unexpected block payload) from taking down the whole workbench. */
export class PanelBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error) {
    console.error("[workspace panel]", error);
  }
  render() {
    if (this.state.error) return <PanelEmpty text={`This panel failed to render: ${this.state.error.message}`} />;
    return this.props.children;
  }
}
