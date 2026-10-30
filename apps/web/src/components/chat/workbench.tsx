"use client";
import { AlertTriangle, MessageSquare, MonitorSmartphone, WifiOff, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { Workspace } from "@/components/workspace/workspace";
import { Composer } from "./composer";
import { ChatHeader } from "./header";
import { MessageList } from "./message-list";

export function Workbench({ readOnly, banner }: { readOnly?: boolean; banner?: React.ReactNode }) {
  const t = useT();
  const [width, setWidth] = useState(500);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileTab, setMobileTab] = useState<"chat" | "workspace">("chat");
  const disconnected = useChat((s) => s.disconnected);
  const errors = useChat((s) => s.errors);
  const set = useChat((s) => s.set);
  const dragging = useRef(false);

  useEffect(() => {
    const saved = Number(localStorage.getItem("oa_chat_width") || 0);
    if (saved >= 340) setWidth(saved);
  }, []);

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const startX = e.clientX;
    const startW = width;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const iframes = Array.from(document.querySelectorAll("iframe"));
    iframes.forEach((f) => (f.style.pointerEvents = "none"));
    const onMove = (ev: MouseEvent) => {
      const w = Math.max(340, Math.min(window.innerWidth * 0.65, startW + ev.clientX - startX));
      setWidth(w);
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      iframes.forEach((f) => (f.style.pointerEvents = ""));
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      setWidth((w) => {
        localStorage.setItem("oa_chat_width", String(Math.round(w)));
        return w;
      });
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      {banner}
      <ChatHeader collapsed={collapsed} onToggleCollapse={() => setCollapsed(!collapsed)} readOnly={readOnly} />
      {disconnected && !readOnly && (
        <div className="flex items-center justify-center gap-2 bg-warning-soft py-1 text-[12px] text-warning">
          <WifiOff className="h-3.5 w-3.5" />
          {t("networkUnstable")}
        </div>
      )}
      {errors.map((e) => (
        <div key={e.id} className="flex items-center gap-2 bg-danger-soft px-4 py-1 text-[12px] text-danger">
          <AlertTriangle className="h-3.5 w-3.5" />
          <span className="flex-1">{e.message}</span>
          <button onClick={() => set({ errors: errors.filter((x) => x.id !== e.id) })}>
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <div className="flex min-h-0 flex-1">
        <section
          className={cn("min-h-0 shrink-0 flex-col", collapsed ? "md:hidden" : "md:flex", mobileTab === "chat" ? "flex w-full md:w-[var(--chat-w)]" : "hidden md:w-[var(--chat-w)]")}
          style={{ "--chat-w": `${width}px` } as React.CSSProperties}
        >
          <MessageList readOnly={readOnly} />
          {!readOnly && <Composer />}
        </section>
        {!collapsed && <div onMouseDown={startDrag} className="hidden w-1.5 shrink-0 cursor-col-resize transition-colors hover:bg-brand-soft-2 md:block" />}
        <section className={cn("min-h-0 min-w-0 flex-1 pr-2 pb-2 md:block", mobileTab === "workspace" ? "block pl-2" : "hidden", collapsed && "md:pl-2")}>
          <Workspace readOnly={readOnly} />
        </section>
      </div>
      <div className="flex h-12 shrink-0 border-t border-line bg-card md:hidden">
        {(["chat", "workspace"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setMobileTab(k)}
            className={cn("flex flex-1 items-center justify-center gap-1.5 text-[12px] text-muted", mobileTab === k && "text-brand")}
          >
            {k === "chat" ? <MessageSquare className="h-4 w-4" /> : <MonitorSmartphone className="h-4 w-4" />}
            {k === "chat" ? "Project" : "Workspace"}
          </button>
        ))}
      </div>
    </div>
  );
}
