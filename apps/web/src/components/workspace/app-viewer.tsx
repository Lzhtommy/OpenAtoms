"use client";
import {
  AppWindow,
  ChevronDown,
  ExternalLink,
  Home,
  Monitor,
  MousePointerClick,
  RotateCw,
  Smartphone,
  Sparkles,
  Terminal as TermIcon,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { Tip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { bus } from "@/lib/bus";
import { chatActions } from "@/lib/chat-actions";
import { useT } from "@/lib/i18n";
import { cn, safeJson } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { useVisualEdit } from "@/store/visual-edit";
import { useBlockSelection } from "./common";
import { VisualEditPanel } from "./visual-edit-panel";

export function AppViewer({ readOnly }: { readOnly?: boolean }) {
  const t = useT();
  const chatId = useChat((s) => s.chatId)!;
  const chat = useChat((s) => s.chat);
  const consoleLogs = useChat((s) => s.consoleLogs);
  const routes = useChat((s) => s.routes);
  const currentRoute = useChat((s) => s.currentRoute);
  const addConsole = useChat((s) => s.addConsole);
  const set = useChat((s) => s.set);
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  const isMobileProject = chat?.config.template_name === "expo-app";
  // mobile (Expo) projects open in the phone frame by default
  useEffect(() => setDevice(isMobileProject ? "mobile" : "desktop"), [isMobileProject, chatId]);
  const [src, setSrc] = useState<string | null>(null);
  const [frameKey, setFrameKey] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [showConsole, setShowConsole] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const authed = useRef(false);
  const status = chat?.metadata.status;
  const devUrl = readOnly ? chat?.version?.link ?? null : chat?.uris.dev ?? null;
  const errorCount = consoleLogs.filter((l) => l.level === "error").length;
  const { selected: rtBlock } = useBlockSelection(["Browser-RT"]);
  const activeIndex = useChat((s) => s.activeIndex);
  const showRt = rtBlock && activeIndex === rtBlock.index;
  const designActive = useVisualEdit((s) => s.active) && !readOnly;
  const setVE = useVisualEdit((s) => s.set);

  // visual edit state is per project
  useEffect(() => {
    if (useVisualEdit.getState().chatId !== chatId) useVisualEdit.getState().reset(chatId);
  }, [chatId]);

  const postToFrame = useCallback((msg: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage(msg, "*");
  }, []);

  // keep bridge.js design mode in sync (also re-sent after every (re)load of the app)
  useEffect(() => {
    postToFrame({ type: "appviewer:design-mode", enabled: designActive });
  }, [designActive, loaded, frameKey, postToFrame]);

  useEffect(() => {
    const off = bus.on("appviewer:post", postToFrame);
    return () => void off();
  }, [postToFrame]);

  const authAndLoad = useCallback(async () => {
    if (readOnly) {
      setSrc(devUrl);
      return;
    }
    try {
      const d = await api.post<{ ticket: string; url: string }>(`/chats/${chatId}/preview-ticket`);
      authed.current = true;
      setSrc(d.url);
    } catch {
      setSrc(devUrl);
    }
    setLoaded(false);
    setFrameKey((k) => k + 1);
  }, [chatId, devUrl, readOnly]);

  // wake + authenticate on open
  useEffect(() => {
    if (!chat) return;
    if (!readOnly && (status === "stopped" || status === "sleeping" || status === "failed")) {
      api.post(`/chats/${chatId}/runtime/start`).catch(() => {});
    }
    authAndLoad();
  }, [chat?.chat_id]);

  // heartbeat
  useEffect(() => {
    if (readOnly) return;
    const beat = () => api.post("/runtime/heartbeat", { chat_id: chatId }).catch(() => {});
    beat();
    const id = setInterval(beat, 60_000);
    return () => clearInterval(id);
  }, [chatId, readOnly]);

  const reload = useCallback(() => {
    setLoaded(false);
    if (authed.current && devUrl) setSrc(devUrl.replace(/\/$/, "") + (useChat.getState().currentRoute || "/"));
    setFrameKey((k) => k + 1);
  }, [devUrl]);

  useEffect(() => {
    const offs = [
      bus.on("appviewer:reload", reload),
      bus.on("appviewer:runtime-ready", () => {
        if (!loaded) reload();
      }),
    ];
    return () => offs.forEach((f) => f());
  }, [reload, loaded]);

  // messages from bridge.js
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (!iframeRef.current || e.source !== iframeRef.current.contentWindow) return;
      const d = e.data;
      if (!d || typeof d !== "object") return;
      const p = d.payload ?? {};
      switch (d.type) {
        case "mgx-appview-error":
        case "mgx-appview-root-error":
          addConsole({ level: "error", text: `${p.message ?? "Error"}${p.stack ? "\n" + p.stack : ""}` });
          break;
        case "mgx-appview-console": {
          const level = ["error", "warn", "info"].includes(p.level) ? p.level : "log";
          addConsole({ level, text: (p.args ?? []).join(" ") });
          break;
        }
        case "appviewer:sync-design-selection":
          useVisualEdit.getState().set({ selection: d.payload ?? null });
          break;
        case "mgx-appview-route":
        case "mgx-appview-ready": {
          const path = p.path || "/";
          const st = useChat.getState();
          set({ currentRoute: path, routes: st.routes.includes(path) ? st.routes : [...st.routes, path] });
          setLoaded(true);
          break;
        }
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const navigate = (path: string) => {
    iframeRef.current?.contentWindow?.postMessage({ type: "appviewer:navigate", path }, "*");
    set({ currentRoute: path });
  };

  const fixWithAI = () => {
    const errs = consoleLogs.filter((l) => l.level === "error").slice(-5).map((l) => l.text).join("\n\n");
    if (!errs) return;
    chatActions.sendText(chatId, `The app preview reports these errors, please fix them:\n\n\`\`\`\n${errs}\n\`\`\``);
    setShowConsole(false);
  };

  const notRunning = !readOnly && status !== "running";
  const rtData = showRt ? safeJson<{ url?: string; status?: number | string; errors?: string[]; console?: string[] }>(rtBlock!.content, {}) : null;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[34px] shrink-0 items-center gap-1 border-b border-line px-2">
        <div className="inline-flex rounded-full bg-hover p-0.5">
          <Tip label="Desktop">
            <button onClick={() => setDevice("desktop")} className={cn("cursor-pointer rounded-full p-1", device === "desktop" && "bg-card shadow-card")}>
              <Monitor className="h-3.5 w-3.5" />
            </button>
          </Tip>
          <Tip label="Mobile">
            <button onClick={() => setDevice("mobile")} className={cn("cursor-pointer rounded-full p-1", device === "mobile" && "bg-card shadow-card")}>
              <Smartphone className="h-3.5 w-3.5" />
            </button>
          </Tip>
        </div>
        <Tip label={t("refresh")}>
          <Button variant="ghost" size="icon-sm" onClick={reload}>
            <RotateCw className="h-3.5 w-3.5" />
          </Button>
        </Tip>
        <Tip label="Home">
          <Button variant="ghost" size="icon-sm" onClick={() => navigate("/")}>
            <Home className="h-3.5 w-3.5" />
          </Button>
        </Tip>
        <Menu>
          <MenuTrigger asChild>
            <button className="flex h-6 max-w-[220px] min-w-[120px] cursor-pointer items-center gap-1 rounded-full border border-line bg-bg-2 px-2.5 text-[12px] text-fg-2">
              <span className="flex-1 truncate text-left">{currentRoute === "/" ? "Home" : currentRoute}</span>
              <ChevronDown className="h-3 w-3 opacity-60" />
            </button>
          </MenuTrigger>
          <MenuContent>
            {routes.map((r) => (
              <MenuItem key={r} onSelect={() => navigate(r)}>
                {r === "/" ? "Home" : r}
              </MenuItem>
            ))}
          </MenuContent>
        </Menu>
        <Tip label={t("openNew")}>
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={!devUrl}
            onClick={() => devUrl && window.open(devUrl.replace(/\/$/, "") + currentRoute, "_blank")}
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>
        </Tip>
        <div className="flex-1" />
        {!readOnly && (
          <Tip label={t("visualEdit")}>
            <Button
              variant={designActive ? "secondary" : "ghost"}
              size="xs"
              disabled={status !== "running"}
              onClick={() => setVE({ active: !designActive, selection: null })}
              aria-pressed={designActive}
            >
              <MousePointerClick className={cn("h-3 w-3", designActive && "text-brand")} />
              {t("visualEdit")}
            </Button>
          </Tip>
        )}
        {!readOnly && (
          <span className="mr-1 flex items-center gap-1 text-[11px] text-muted">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                status === "running" ? "bg-positive" : status === "activating" ? "animate-pulse bg-warning" : status === "failed" ? "bg-danger" : "bg-subtle",
              )}
            />
            {status}
          </span>
        )}
        <Button variant={showConsole ? "secondary" : "ghost"} size="xs" onClick={() => setShowConsole(!showConsole)}>
          <TermIcon className="h-3 w-3" />
          {t("console")}
          {errorCount > 0 && <span className="rounded-full bg-danger px-1.5 text-[10px] text-white">{errorCount}</span>}
        </Button>
      </div>

      {rtData && (
        <div className="flex items-start gap-2 border-b border-line bg-bg-2 px-3 py-2 text-[12px]">
          <AppWindow className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" />
          <div className="min-w-0 flex-1">
            <div className="text-fg-2">
              UI check · {rtData.url} · status {String(rtData.status ?? "")}
            </div>
            {(rtData.errors ?? []).map((e, i) => (
              <div key={i} className="truncate text-danger">
                {e}
              </div>
            ))}
            {!(rtData.errors ?? []).length && <div className="text-positive">No errors detected</div>}
          </div>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
      <div className="relative min-h-0 min-w-0 flex-1 bg-bg-2">
        {src ? (
          <div className={cn("h-full w-full", device === "mobile" && "flex items-center justify-center py-4")}>
            <iframe
              key={frameKey}
              ref={iframeRef}
              src={src}
              onLoad={() => setLoaded(true)}
              className={cn(
                "border-0 bg-white",
                device === "desktop" ? "h-full w-full" : "h-[760px] max-h-full w-[390px] rounded-[28px] border-[6px] border-inverse shadow-pop",
              )}
              allow="clipboard-read; clipboard-write; microphone; camera"
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads"
            />
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-[13px] text-muted">{t("noPreview")}</div>
        )}
        {(notRunning || (!loaded && src)) && (
          <div className="pointer-events-none absolute inset-0 flex flex-col gap-3 bg-bg-2 p-6">
            {notRunning && (
              <div className="pointer-events-auto mx-auto mb-2 flex items-center gap-2 rounded-full bg-card px-3 py-1.5 text-[12px] text-muted shadow-card">
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand border-t-transparent" />
                {status === "activating" || status === "stopped" || status === "sleeping" ? t("waking") : status}
              </div>
            )}
            <div className="skeleton h-10 w-full rounded-l" />
            <div className="flex flex-1 gap-3">
              <div className="skeleton w-1/4 rounded-l" />
              <div className="flex flex-1 flex-col gap-3">
                <div className="skeleton h-1/3 rounded-l" />
                <div className="skeleton flex-1 rounded-l" />
              </div>
            </div>
          </div>
        )}
        {showConsole && (
          <div className="absolute inset-x-0 bottom-0 flex h-[45%] flex-col border-t border-line bg-card shadow-pop">
            <div className="flex h-8 shrink-0 items-center gap-2 border-b border-line px-3 text-[12px]">
              <span className="font-medium">{t("console")}</span>
              <span className="text-muted">{consoleLogs.length}</span>
              <div className="flex-1" />
              {!readOnly && errorCount > 0 && (
                <Button size="xs" variant="brand" onClick={fixWithAI}>
                  <Sparkles className="h-3 w-3" />
                  {t("fixWithAI")}
                </Button>
              )}
              <Button size="icon-sm" variant="ghost" onClick={() => set({ consoleLogs: [] })}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
              <Button size="icon-sm" variant="ghost" onClick={() => setShowConsole(false)}>
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto font-mono text-[11.5px]">
              {consoleLogs.length === 0 && <div className="p-3 text-muted">No console output</div>}
              {consoleLogs.map((l) => (
                <div
                  key={l.id}
                  className={cn(
                    "border-b border-line px-3 py-1 whitespace-pre-wrap",
                    l.level === "error" && "bg-danger-soft text-danger",
                    l.level === "warn" && "bg-warning-soft text-warning",
                  )}
                >
                  {l.text}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      {designActive && <VisualEditPanel />}
      </div>
    </div>
  );
}
