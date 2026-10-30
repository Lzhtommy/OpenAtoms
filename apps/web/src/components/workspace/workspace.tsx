"use client";
import {
  AppWindow,
  BookOpenText,
  Code2,
  Database,
  FolderTree,
  Globe,
  ImageIcon,
  LayoutDashboard,
  ListTodo,
  NotebookPen,
  SquareTerminal,
  TrendingUp,
  Trophy,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useRef } from "react";
import { bus } from "@/lib/bus";
import type { DictKey } from "@/lib/i18n";
import type { BlockTimeline } from "@/lib/types";
import { BLOCK_PANEL, type PanelKey, useChat } from "@/store/chat";
import { AppViewer } from "./app-viewer";
import { AssetsPanel } from "./assets-panel";
import { BoostPanel } from "./boost-panel";
import { BrowserPanel } from "./browser-panel";
import { CloudPanel } from "./cloud-panel";
import { PanelBoundary, TimelineScrubber } from "./common";
import { GrowthPanel } from "./growth-panel";
import { NotebookPanel } from "./notebook-panel";
import { OverviewPanel } from "./overview-panel";
import { PlanPanel } from "./plan-panel";
import { ResearchPanel } from "./research-panel";

const EditorPanel = dynamic(() => import("./editor-panel").then((m) => m.EditorPanel), { ssr: false });
const TerminalPanel = dynamic(() => import("./terminal-panel").then((m) => m.TerminalPanel), { ssr: false });
const FilesPanel = dynamic(() => import("./files-panel").then((m) => m.FilesPanel), { ssr: false });

export interface PanelPresence {
  blockTypes: Set<string>;
  hasBoost: boolean;
}

export const PANELS: { key: PanelKey; label: DictKey; icon: typeof Code2; when?: (p: PanelPresence) => boolean }[] = [
  { key: "appviewer", label: "appViewer", icon: AppWindow },
  { key: "editor", label: "editor", icon: Code2 },
  { key: "terminal", label: "terminal", icon: SquareTerminal },
  { key: "files", label: "files", icon: FolderTree },
  { key: "browser", label: "browser", icon: Globe },
  { key: "notebook", label: "notebook", icon: NotebookPen },
  { key: "plan", label: "plan", icon: ListTodo },
  { key: "cloud", label: "cloud", icon: Database },
  { key: "overview", label: "overview", icon: LayoutDashboard },
  { key: "assets", label: "assets", icon: ImageIcon, when: (p) => p.blockTypes.has("Assets") || p.blockTypes.has("File") },
  { key: "research", label: "research", icon: BookOpenText, when: (p) => p.blockTypes.has("Research") },
  { key: "growth", label: "growth", icon: TrendingUp, when: (p) => p.blockTypes.has("Growth") },
  { key: "boost", label: "boost", icon: Trophy, when: (p) => p.hasBoost },
];

export const pinned = { at: 0 };

const SCRUBBER_PANELS: PanelKey[] = ["appviewer", "editor", "terminal", "browser", "notebook", "plan", "assets", "research", "growth"];

export function Workspace({ readOnly }: { readOnly?: boolean }) {
  const panel = useChat((s) => s.panel);
  const lastSwitch = useRef(0);

  // auto-follow: switch to the panel of each new running block (unless user picked a panel recently)
  useEffect(() => {
    const offs = [
      bus.on("timeline:start", (t: BlockTimeline) => {
        const st = useChat.getState();
        if (!st.follow || Date.now() - pinned.at < 30_000) return;
        // blocks the user produced themselves (visual edits) must not pull them away from the App Viewer
        if (t.block_owner === "User") return;
        const target = BLOCK_PANEL[t.block_type];
        if (!target || Date.now() - lastSwitch.current < 400) return;
        lastSwitch.current = Date.now();
        if (st.panel !== target) st.set({ panel: target });
      }),
      bus.on("run:end", () => {
        const st = useChat.getState();
        if (Date.now() - pinned.at < 30_000) return;
        st.set({ panel: "appviewer", follow: true, activeIndex: null });
      }),
    ];
    return () => offs.forEach((f) => f());
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-line bg-card shadow-card">
      <div className="relative min-h-0 flex-1">
        <div className={panel === "appviewer" ? "h-full" : "hidden"}>
          <AppViewer readOnly={readOnly} />
        </div>
        <PanelBoundary key={panel}>
          {panel === "editor" && <EditorPanel />}
          {panel === "terminal" && <TerminalPanel />}
          {panel === "files" && !readOnly && <FilesPanel />}
          {panel === "browser" && <BrowserPanel />}
          {panel === "notebook" && <NotebookPanel />}
          {panel === "plan" && <PlanPanel />}
          {panel === "cloud" && <CloudPanel readOnly={readOnly} />}
          {panel === "overview" && <OverviewPanel readOnly={readOnly} />}
          {panel === "assets" && <AssetsPanel />}
          {panel === "research" && <ResearchPanel />}
          {panel === "growth" && <GrowthPanel />}
          {panel === "boost" && <BoostPanel />}
        </PanelBoundary>
      </div>
      {SCRUBBER_PANELS.includes(panel) && <TimelineScrubber />}
    </div>
  );
}
