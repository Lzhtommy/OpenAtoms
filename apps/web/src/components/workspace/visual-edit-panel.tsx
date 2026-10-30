"use client";
import { MousePointerClick, Save, Sparkles, Undo2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { ApiError, api, errMsg } from "@/lib/api";
import { bus } from "@/lib/bus";
import { useLang } from "@/lib/i18n";
import type { StagedEdit, Version } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { selectionRef, useVisualEdit } from "@/store/visual-edit";

/**
 * Tailwind option tables. `css` is the equivalent inline style used for the instant preview in the iframe:
 * a class picked here usually has no CSS in the dev server yet (Tailwind's JIT only sees classes once they
 * are written to the source), so the bridge applies the style until the commit lands and HMR re-renders.
 */
type Opt = { cls: string; label: string; css: Record<string, string>; swatch?: string };
type Group = { key: string; label: [string, string]; match: RegExp; options: Opt[] };

const SIZES: [string, string][] = [["xs", "0.75rem"], ["sm", "0.875rem"], ["base", "1rem"], ["lg", "1.125rem"], ["xl", "1.25rem"],
  ["2xl", "1.5rem"], ["3xl", "1.875rem"], ["4xl", "2.25rem"], ["5xl", "3rem"], ["6xl", "3.75rem"]];
const WEIGHTS: [string, string][] = [["light", "300"], ["normal", "400"], ["medium", "500"], ["semibold", "600"], ["bold", "700"], ["extrabold", "800"]];
const COLORS: [string, string][] = [["white", "#ffffff"], ["black", "#000000"], ["gray-500", "#6b7280"], ["gray-900", "#111827"],
  ["slate-700", "#334155"], ["red-600", "#dc2626"], ["orange-500", "#f97316"], ["amber-400", "#fbbf24"], ["green-600", "#16a34a"],
  ["emerald-500", "#10b981"], ["sky-500", "#0ea5e9"], ["blue-600", "#2563eb"], ["indigo-600", "#4f46e5"], ["purple-600", "#9333ea"],
  ["pink-500", "#ec4899"]];
const SPACE: [string, string][] = [["0", "0px"], ["1", "0.25rem"], ["2", "0.5rem"], ["3", "0.75rem"], ["4", "1rem"], ["6", "1.5rem"],
  ["8", "2rem"], ["10", "2.5rem"], ["12", "3rem"], ["16", "4rem"]];
const RADII: [string, string][] = [["none", "0px"], ["sm", "0.125rem"], ["", "0.25rem"], ["md", "0.375rem"], ["lg", "0.5rem"],
  ["xl", "0.75rem"], ["2xl", "1rem"], ["3xl", "1.5rem"], ["full", "9999px"]];

const NON_COLOR_TEXT = /^text-(xs|sm|base|lg|xl|[2-9]xl|left|right|center|justify|start|end|wrap|nowrap|balance|pretty|ellipsis|clip)$/;
const NON_COLOR_BG = /^bg-(opacity-.*|gradient-.*|clip-.*|origin-.*|fixed|local|scroll|center|top|bottom|left|right|cover|contain|auto|no-repeat|repeat.*|blend-.*|none)$/;

const GROUPS: Group[] = [
  { key: "size", label: ["字号", "Font size"], match: /^text-(xs|sm|base|lg|xl|[2-9]xl)$/,
    options: SIZES.map(([k, v]) => ({ cls: `text-${k}`, label: k, css: { fontSize: v } })) },
  { key: "weight", label: ["字重", "Font weight"], match: /^font-(thin|extralight|light|normal|medium|semibold|bold|extrabold|black)$/,
    options: WEIGHTS.map(([k, v]) => ({ cls: `font-${k}`, label: k, css: { fontWeight: v } })) },
  { key: "color", label: ["文字颜色", "Text color"], match: /^text-[a-z]+(-[a-z0-9]+)*$/,
    options: COLORS.map(([k, v]) => ({ cls: `text-${k}`, label: k, css: { color: v }, swatch: v })) },
  { key: "bg", label: ["背景色", "Background"], match: /^bg-[a-z]+(-[a-z0-9]+)*$/,
    options: [{ cls: "bg-transparent", label: "transparent", css: { backgroundColor: "transparent" }, swatch: "transparent" },
      ...COLORS.map(([k, v]) => ({ cls: `bg-${k}`, label: k, css: { backgroundColor: v }, swatch: v }))] },
  { key: "padding", label: ["内边距", "Padding"], match: /^p-(\d+(\.5)?|px)$/,
    options: SPACE.map(([k, v]) => ({ cls: `p-${k}`, label: k, css: { padding: v } })) },
  { key: "margin", label: ["外边距", "Margin"], match: /^m-(\d+(\.5)?|px|auto)$/,
    options: SPACE.map(([k, v]) => ({ cls: `m-${k}`, label: k, css: { margin: v } })) },
  { key: "radius", label: ["圆角", "Radius"], match: /^rounded(-(none|sm|md|lg|xl|2xl|3xl|full))?$/,
    options: RADII.map(([k, v]) => ({ cls: k ? `rounded-${k}` : "rounded", label: k || "default", css: { borderRadius: v } })) },
];

function currentOf(g: Group, classes: string[]): string | undefined {
  return classes.find((c) => {
    if (!g.match.test(c)) return false;
    if (g.key === "color") return !NON_COLOR_TEXT.test(c);
    if (g.key === "bg") return !NON_COLOR_BG.test(c);
    return true;
  });
}

const post = (msg: Record<string, unknown>) => bus.emit("appviewer:post", msg);

export function VisualEditPanel() {
  const zh = useLang((s) => s.lang) === "zh";
  const chatId = useChat((s) => s.chatId)!;
  const isRunning = useChat((s) => s.isRunning);
  const { selection, staged, set } = useVisualEdit();
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => setText(selection?.text ?? ""), [selection?.loc, selection?.selector]);
  useEffect(() => {
    api.get<{ edits: StagedEdit[] }>(`/chats/${chatId}/edit-session`).then((d) => set({ staged: d.edits })).catch(() => {});
  }, [chatId]);

  const stage = async (edit: { add?: string[]; remove?: string[]; text?: string }, css?: Record<string, string>) => {
    if (!selection?.loc) return;
    post({ type: "appviewer:apply-preview", loc: selection.loc, ...edit, style: css });
    try {
      const d = await api.post<{ edits: StagedEdit[] }>(`/chats/${chatId}/edit-session`, {
        edits: [{ loc: selection.loc, ...edit, tag: selection.tag, selector: selection.selector }],
      });
      set({ staged: d.edits });
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const pick = (g: Group, cls: string) => {
    if (!selection) return;
    const cur = currentOf(g, selection.classes);
    if (cur === cls) return;
    const opt = g.options.find((o) => o.cls === cls);
    stage({ add: [cls], remove: cur ? [cur] : [] }, opt?.css);
  };

  const commit = async () => {
    setSaving(true);
    try {
      const d = await api.post<{ version: Version; files: string[] }>(`/chats/${chatId}/edit-session/commit`);
      set({ staged: [], selection: null });
      toast.success(zh ? `已保存为 ${d.version.version}（${d.files.join(", ")}）` : `Saved as ${d.version.version} (${d.files.join(", ")})`);
    } catch (e) {
      const errs = e instanceof ApiError ? (e.data as { errors?: { loc: string; error: string }[] })?.errors : undefined;
      toast.error(errs?.length ? errs.map((x) => `${x.loc}: ${x.error}`).join("\n") : errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  const discard = async () => {
    await api.del(`/chats/${chatId}/edit-session`).catch((e) => toast.error(errMsg(e)));
    set({ staged: [], selection: null });
    bus.emit("appviewer:reload");
  };

  const askAI = () => {
    if (!selection) return;
    set({ attached: selectionRef(selection) });
    window.dispatchEvent(new CustomEvent("oa:composer-focus"));
  };

  return (
    <div className="flex h-full w-[280px] shrink-0 flex-col border-l border-line bg-card">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-line px-3 text-[12px]">
        <MousePointerClick className="h-3.5 w-3.5 text-brand" />
        <span className="font-medium">{zh ? "可视化编辑" : "Visual edit"}</span>
        <div className="flex-1" />
        <Button size="icon-sm" variant="ghost" onClick={() => set({ active: false, selection: null })} aria-label="close">
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3 text-[12px]">
        {!selection ? (
          <div className="rounded-l bg-bg-2 p-3 text-muted">
            {zh ? "在预览中点击任意元素以编辑文字与样式。按 Esc 取消选择。" : "Click any element in the preview to edit its text and style. Esc clears the selection."}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="rounded-l border border-line bg-bg-2 p-2">
              <div className="font-mono text-[12px] text-fg">&lt;{selection.tag}&gt;</div>
              <div className="truncate font-mono text-[11px] text-muted" title={selection.loc ?? ""}>
                {selection.loc ?? (zh ? "无源码映射" : "no source mapping")}
              </div>
            </div>
            {!selection.loc && (
              <div className="rounded-l bg-warning-soft p-2 text-warning">
                {zh ? "此元素没有源码位置（可能来自第三方组件），请用“让 AI 修改”。" : "This element has no source location (likely from a library); use “Ask AI”."}
              </div>
            )}
            {selection.text_editable && selection.loc && (
              <label className="flex flex-col gap-1">
                <span className="text-muted">{zh ? "文本" : "Text"}</span>
                <textarea
                  value={text}
                  rows={2}
                  onChange={(e) => setText(e.target.value)}
                  onBlur={() => text !== selection.text && stage({ text })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      (e.target as HTMLTextAreaElement).blur();
                    }
                  }}
                  className="resize-none rounded-m border border-line bg-card px-2 py-1 text-[13px] outline-none focus:border-brand"
                />
              </label>
            )}
            {selection.loc &&
              GROUPS.map((g) => {
                const cur = currentOf(g, selection.classes);
                return (
                  <div key={g.key} className="flex items-center gap-2">
                    <span className="w-16 shrink-0 text-muted">{zh ? g.label[0] : g.label[1]}</span>
                    <Select
                      value={cur && g.options.some((o) => o.cls === cur) ? cur : ""}
                      placeholder={cur ?? "—"}
                      onValueChange={(v) => pick(g, v)}
                      className="h-7 min-w-0 flex-1 justify-between px-2.5 text-[12px]"
                      options={g.options.map((o) => ({
                        value: o.cls,
                        label: (
                          <span className="flex items-center gap-2">
                            {o.swatch && (
                              <span className="h-3 w-3 rounded-full border border-line" style={{ background: o.swatch }} />
                            )}
                            {o.cls}
                          </span>
                        ),
                      }))}
                    />
                  </div>
                );
              })}
            <Button size="xs" variant="outline" onClick={askAI}>
              <Sparkles className="h-3 w-3" />
              {zh ? "让 AI 修改这个元素" : "Ask AI about this"}
            </Button>
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-2 border-t border-line p-2.5">
        <span className={cn("text-[12px]", staged.length ? "text-fg" : "text-muted")}>
          {zh ? `${staged.length} 处待保存` : `${staged.length} pending`}
        </span>
        <div className="flex-1" />
        <Button size="xs" variant="ghost" disabled={!staged.length || saving} onClick={discard}>
          <Undo2 className="h-3 w-3" />
          {zh ? "放弃" : "Discard"}
        </Button>
        <Button size="xs" disabled={!staged.length || isRunning} loading={saving} onClick={commit}>
          {!saving && <Save className="h-3 w-3" />}
          {zh ? "保存" : "Save"}
        </Button>
      </div>
    </div>
  );
}
