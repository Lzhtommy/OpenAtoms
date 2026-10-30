"use client";
import { Plus, Trophy, X } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tip } from "@/components/ui/tooltip";
import { useLang } from "@/lib/i18n";
import type { ModelInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

export const BOOST_MIN = 2;
export const BOOST_MAX = 4;

/**
 * Best-of-N ("竞赛模式") toggle + candidate model slots. `value === null` means boost is off.
 * The same model may be picked several times: candidates are also steered to different visual directions.
 */
export function BoostPicker({
  models,
  value,
  onChange,
  defaultModel,
}: {
  models: ModelInfo[];
  value: string[] | null;
  onChange: (v: string[] | null) => void;
  defaultModel?: string;
}) {
  const lang = useLang((s) => s.lang);
  const zh = lang === "zh";
  const on = value !== null;
  const base = defaultModel || models[0]?.id || "oa-mock";
  const slots = value ?? [];
  const label = on ? (zh ? `竞赛 ×${slots.length}` : `Boost ×${slots.length}`) : zh ? "竞赛模式" : "Boost";

  return (
    <Popover>
      <Tip label={zh ? "多个模型并行生成候选方案，挑选最好的一个合并" : "Run several models in parallel and merge the best candidate"}>
        <PopoverTrigger asChild>
          <button
            type="button"
            onClick={() => {
              if (!on) onChange([base, models.find((m) => m.id !== base)?.id ?? base]);
            }}
            className={cn(
              "flex h-8 cursor-pointer items-center gap-1 rounded-full px-2.5 text-[12px] text-muted transition-colors hover:bg-hover hover:text-fg",
              on && "bg-brand-soft text-brand",
            )}
            aria-pressed={on}
          >
            <Trophy className="h-3.5 w-3.5" />
            {label}
          </button>
        </PopoverTrigger>
      </Tip>
      <PopoverContent className="w-72">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[13px] font-medium">{zh ? "竞赛模式（Best-of-N）" : "Boost (Best-of-N)"}</span>
          {on && (
            <button className="cursor-pointer text-[12px] text-muted hover:text-fg" onClick={() => onChange(null)}>
              {zh ? "关闭" : "Turn off"}
            </button>
          )}
        </div>
        <p className="mb-2 text-[12px] text-muted">
          {zh
            ? `为每个候选选择模型（${BOOST_MIN}–${BOOST_MAX} 个）。所有候选并行构建，完成后对比预览并挑选一个合并。`
            : `Pick a model per candidate (${BOOST_MIN}–${BOOST_MAX}). All candidates build in parallel; compare the previews and merge one.`}
        </p>
        <div className="flex flex-col gap-1.5">
          {slots.map((m, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <span className="w-5 text-center text-[12px] text-subtle">#{i + 1}</span>
              <select
                value={m}
                onChange={(e) => onChange(slots.map((x, j) => (j === i ? e.target.value : x)))}
                className="h-7 min-w-0 flex-1 cursor-pointer rounded-m border border-line bg-card px-2 text-[12px] outline-none"
                aria-label={`candidate ${i + 1} model`}
              >
                {models.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name || x.id}
                  </option>
                ))}
              </select>
              <button
                className="cursor-pointer rounded-m p-1 text-muted hover:bg-hover disabled:cursor-not-allowed disabled:opacity-40"
                disabled={slots.length <= BOOST_MIN}
                onClick={() => onChange(slots.filter((_, j) => j !== i))}
                aria-label="remove candidate"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          {(!on || slots.length < BOOST_MAX) && (
            <button
              className="mt-0.5 flex cursor-pointer items-center gap-1 self-start rounded-m px-1.5 py-1 text-[12px] text-brand hover:bg-hover"
              onClick={() => onChange(on ? [...slots, base] : [base, base])}
            >
              <Plus className="h-3.5 w-3.5" /> {zh ? "添加候选" : "Add candidate"}
            </button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
