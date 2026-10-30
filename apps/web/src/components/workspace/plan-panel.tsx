"use client";
import { CheckCircle2, Circle, ListTodo, Loader2 } from "lucide-react";
import { AgentAvatar } from "@/components/ui/avatar";
import { agentByRole } from "@/lib/agents";
import type { Task } from "@/lib/types";
import { cn, safeJson } from "@/lib/utils";
import { PanelEmpty, useBlockSelection, useSnapshot } from "./common";

export function PlanPanel() {
  const { selected } = useBlockSelection(["Task"]);
  const snap = useSnapshot(selected && !selected.content ? selected : undefined);
  if (!selected) return <PanelEmpty icon={ListTodo} text="No plan yet" />;
  const d = safeJson<{ tasks?: Task[]; current_task_id?: string }>(selected.content || snap || "{}", {});
  const tasks = d.tasks ?? [];
  const done = tasks.filter((t) => t.status === "done").length;
  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-[15px] font-semibold">Plan</h3>
        <span className="text-[12px] text-muted">
          {done}/{tasks.length} done
        </span>
      </div>
      <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-hover">
        <div className="h-full bg-brand transition-all" style={{ width: `${tasks.length ? (done / tasks.length) * 100 : 0}%` }} />
      </div>
      <div className="flex flex-col gap-2">
        {tasks.map((t, i) => {
          const a = agentByRole(t.assignee);
          const cur = t.id === d.current_task_id || t.status === "running";
          return (
            <div key={t.id ?? i} className={cn("flex items-start gap-3 rounded-l border bg-card p-3", cur ? "border-brand/40 shadow-card" : "border-line")}>
              <div className="mt-0.5">
                {t.status === "done" ? (
                  <CheckCircle2 className="h-4 w-4 text-positive" />
                ) : cur ? (
                  <Loader2 className="h-4 w-4 animate-spin text-brand" />
                ) : (
                  <Circle className="h-4 w-4 text-subtle" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className={cn("text-[13px]", t.status === "done" && "text-muted line-through")}>{t.title}</div>
                {t.instruction && <div className="mt-1 line-clamp-3 text-[12px] text-muted">{t.instruction}</div>}
              </div>
              <div className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted">
                <AgentAvatar role={t.assignee} size={20} />
                {a?.name}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
