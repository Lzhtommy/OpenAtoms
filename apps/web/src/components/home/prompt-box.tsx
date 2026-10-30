"use client";
import { ArrowUp, Bot, ClipboardList, Globe, Microscope, Paperclip, Smartphone, Users, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { BoostPicker } from "@/components/chat/boost-picker";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { GeneralConfig, ModelInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface PromptSubmit {
  text: string;
  files: File[];
  agent_mode: "full" | "lite";
  plan_mode: boolean;
  model: string;
  /** project template: web (react-shadcn-vite) or mobile (expo-app) */
  template_name: "react-shadcn-vite" | "expo-app";
  /** Deep Research mode (Iris) */
  enable_dr: boolean;
  /** Best-of-N candidate models, or null */
  boost_models: string[] | null;
}

export function useGeneralConfig() {
  const [cfg, setCfg] = useState<GeneralConfig | null>(null);
  useEffect(() => {
    api.get<GeneralConfig>("/configs/general").then(setCfg).catch(() => {});
  }, []);
  return cfg;
}

export function PromptBox({
  onSubmit,
  loading,
  initial = "",
  showControls = true,
  autoFocus,
}: {
  onSubmit: (p: PromptSubmit) => void;
  loading?: boolean;
  initial?: string;
  showControls?: boolean;
  autoFocus?: boolean;
}) {
  const t = useT();
  const cfg = useGeneralConfig();
  const [text, setText] = useState(initial);
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<"full" | "lite">("full");
  const [planMode, setPlanMode] = useState(false);
  const [model, setModel] = useState("");
  const [template, setTemplate] = useState<PromptSubmit["template_name"]>("react-shadcn-vite");
  const [research, setResearch] = useState(false);
  const [boost, setBoost] = useState<string[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => setText(initial), [initial]);
  useEffect(() => {
    if (cfg) {
      setMode(cfg.agent_mode || "full");
      setModel(cfg.default_model);
    }
  }, [cfg]);
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 280) + "px";
  }, [text]);

  const models: ModelInfo[] = cfg
    ? (cfg.models?.length ? cfg.models : [cfg.default_model, ...cfg.adv_models].map((id): ModelInfo => ({ id, name: id, provider: "" }))).filter(
        (m, i, arr) => arr.findIndex((x) => x.id === m.id) === i,
      )
    : [];

  const submit = () => {
    if (!text.trim() || loading) return;
    onSubmit({
      text: text.trim(),
      files,
      agent_mode: mode,
      plan_mode: planMode,
      model,
      template_name: template,
      enable_dr: research,
      boost_models: boost,
    });
  };

  return (
    <div className="rounded-xl border border-line bg-card p-3 shadow-card transition-shadow focus-within:shadow-pop">
      {files.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {files.map((f, i) => (
            <span key={i} className="inline-flex items-center gap-1 rounded-full bg-hover px-2 py-0.5 text-[12px]">
              <Paperclip className="h-3 w-3" />
              {f.name}
              <button onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <textarea
        ref={taRef}
        autoFocus={autoFocus}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        rows={3}
        placeholder={t("promptPlaceholder")}
        className="w-full resize-none bg-transparent px-1 text-[15px] leading-relaxed outline-none placeholder:text-subtle"
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Tip label={t("attach")}>
          <Button variant="outline" size="icon-sm" onClick={() => fileRef.current?.click()}>
            <Paperclip className="h-3.5 w-3.5" />
          </Button>
        </Tip>
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            setFiles([...files, ...Array.from(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
        {showControls && (
          <>
            <div className="inline-flex rounded-full bg-hover p-0.5">
              {(["lite", "full"] as const).map((m) => (
                <Tip key={m} label={m === "lite" ? t("liteDesc") : t("fullDesc")}>
                  <button
                    onClick={() => setMode(m)}
                    className={cn(
                      "flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-1 text-[12px] text-muted",
                      mode === m && "bg-card text-fg shadow-card",
                    )}
                  >
                    {m === "lite" ? <Bot className="h-3.5 w-3.5" /> : <Users className="h-3.5 w-3.5" />}
                    {m === "lite" ? t("lite") : t("full")}
                  </button>
                </Tip>
              ))}
            </div>
            <div className="inline-flex rounded-full bg-hover p-0.5">
              {(["react-shadcn-vite", "expo-app"] as const).map((tpl) => (
                <Tip key={tpl} label={tpl === "expo-app" ? t("mobileAppDesc") : "React + Vite + Tailwind"}>
                  <button
                    onClick={() => setTemplate(tpl)}
                    aria-pressed={template === tpl}
                    className={cn(
                      "flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-1 text-[12px] text-muted",
                      template === tpl && "bg-card text-fg shadow-card",
                    )}
                  >
                    {tpl === "expo-app" ? <Smartphone className="h-3.5 w-3.5" /> : <Globe className="h-3.5 w-3.5" />}
                    {tpl === "expo-app" ? t("mobileApp") : t("webApp")}
                  </button>
                </Tip>
              ))}
            </div>
            <Tip label={t("deepResearchDesc")}>
              <button
                onClick={() => setResearch(!research)}
                aria-pressed={research}
                className={cn(
                  "flex cursor-pointer items-center gap-1 rounded-full px-2 py-1 text-[12px] text-muted hover:bg-hover",
                  research && "bg-brand-soft text-brand",
                )}
              >
                <Microscope className="h-3.5 w-3.5" />
                {t("deepResearch")}
              </button>
            </Tip>
            {models.length > 0 && <BoostPicker models={models} value={boost} onChange={setBoost} defaultModel={model} />}
            <Tip label={t("planModeDesc")}>
              <label className={cn("flex cursor-pointer items-center gap-1.5 rounded-full px-2 py-1 text-[12px] text-muted", planMode && "text-brand")}>
                <ClipboardList className="h-3.5 w-3.5" />
                {t("planMode")}
                <Switch checked={planMode} onCheckedChange={setPlanMode} />
              </label>
            </Tip>
            {models.length > 0 && (
              <Select
                value={model}
                onValueChange={setModel}
                options={models.map((m) => ({ value: m.id, label: m.name || m.id, desc: m.description }))}
                className="h-7"
              />
            )}
          </>
        )}
        <div className="flex-1" />
        <Button size="icon" onClick={submit} disabled={!text.trim()} loading={loading} aria-label="send">
          {!loading && <ArrowUp className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
}

export const EXAMPLES = [
  { zh: "番茄钟计时器", en: "Pomodoro timer", prompt: "做一个番茄钟页面，支持开始/暂停/重置，有专注与休息两种模式和进度环。" },
  { zh: "待办应用（带登录）", en: "Todo app with login", prompt: "做一个带登录的待办应用，支持添加、完成、删除任务和按状态筛选。" },
  { zh: "个人作品集", en: "Portfolio site", prompt: "为一名前端工程师做一个简洁优雅的个人作品集网站，包含简介、项目、联系方式。" },
  { zh: "数据看板", en: "Analytics dashboard", prompt: "做一个销售数据看板，包含 KPI 卡片、折线图和明细表格。" },
  { zh: "留言板", en: "Guestbook", prompt: "做一个留言板应用，数据存储在云数据库中，所有访客都能看到留言。" },
  { zh: "落地页", en: "Landing page", prompt: "为一款 AI 笔记应用做一个营销落地页，包含 Hero、功能、定价和 FAQ。" },
];
