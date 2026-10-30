"use client";
import {
  AppWindow,
  Check,
  Code2,
  Database,
  FileCode2,
  Globe,
  ListTodo,
  Loader2,
  NotebookPen,
  Search,
  SquareTerminal,
  Wrench,
  X,
} from "lucide-react";
import { useLang } from "@/lib/i18n";
import type { ActionData } from "@/lib/types";
import { basename, cn } from "@/lib/utils";
import { useChat } from "@/store/chat";

export const BLOCK_ICON: Record<string, typeof Code2> = {
  Editor: FileCode2,
  Terminal: SquareTerminal,
  Task: ListTodo,
  Browser: Globe,
  "Browser-RT": AppWindow,
  Notebook: NotebookPen,
  Search: Search,
  Backend: Database,
};

const COMMAND_LABEL: Record<string, [string, string]> = {
  editor_read: ["Read", "读取"],
  editor_write: ["Write", "写入"],
  editor_edit_file_by_replace: ["Edit", "编辑"],
  editor_append_file: ["Append", "追加"],
  terminal_run: ["Run", "运行"],
  plan_append_task: ["Add task", "添加任务"],
  plan_reset_task: ["Reset task", "重置任务"],
  plan_finish_current_task: ["Finish task", "完成任务"],
  browser_goto: ["Visit", "访问"],
  browser_click: ["Click", "点击"],
  browser_scroll: ["Scroll", "滚动"],
  browser_type: ["Type", "输入"],
  dataanalyst_write_and_exec_code: ["Run code", "执行代码"],
  checkui_run: ["Check app", "检查应用"],
  searchenhancedqa_run: ["Search", "搜索"],
  backendmanager_create_table: ["Create table", "建表"],
  backendmanager_create_tables: ["Create tables", "建表"],
  backendmanager_get_schemas: ["Get schemas", "读取表结构"],
  backendmanager_insert_table_data: ["Insert rows", "插入数据"],
  deployer_deploy_to_public: ["Deploy", "部署"],
};

export function actionSubject(a: ActionData) {
  const e = a.extra || {};
  return (
    e.path ||
    e.cmd ||
    e.title ||
    e.page_url ||
    e.url ||
    e.query ||
    e.table ||
    (a.src_path ? basename(a.src_path) : "") ||
    (a.file ? basename(a.file) : "")
  );
}

export function ToolCard({ uuid }: { uuid: string }) {
  const a = useChat((s) => s.actionDatas[uuid]);
  const showBlock = useChat((s) => s.showBlock);
  const lang = useLang((s) => s.lang) === "zh" ? 1 : 0;
  if (!a) {
    return (
      <div className="my-1 inline-flex items-center gap-2 rounded-m border border-line bg-bg-2 px-2.5 py-1.5 text-[12px] text-muted">
        <Wrench className="h-3.5 w-3.5" />…
      </div>
    );
  }
  const Icon = BLOCK_ICON[a.block_type] ?? Wrench;
  const label = COMMAND_LABEL[a.command_name]?.[lang] ?? a.command_name;
  const subject = String(actionSubject(a) ?? "");
  const clickable = a.timeline_index !== null && a.timeline_index !== undefined;
  return (
    <button
      onClick={() => clickable && showBlock(a.timeline_index!)}
      className={cn(
        "my-1 flex w-full max-w-full items-center gap-2 rounded-m border border-line bg-bg-2 px-2.5 py-1.5 text-left text-[12px] transition-colors",
        clickable && "cursor-pointer hover:border-line-strong hover:bg-hover",
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 text-fg-2" />
      <span className="shrink-0 font-medium text-fg-2">{label}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-muted">{subject}</span>
      {a.status === "running" ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-brand" />
      ) : a.status === "failed" ? (
        <X className="h-3.5 w-3.5 shrink-0 text-danger" />
      ) : (
        <Check className="h-3.5 w-3.5 shrink-0 text-positive" />
      )}
    </button>
  );
}
