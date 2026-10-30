"""Task block (plan_*) and coordination tools (mention / ask_human / ask_questions / reply_to_human)."""
from __future__ import annotations

import json
from typing import Any

from oa.agents.emit import post_message
from oa.agents.roles import ALEX, find_role
from oa.tools.base import ToolContext, ToolResult, tool


def plan_of(ctx: ToolContext) -> dict[str, Any]:
    st = ctx.rc.state
    st.setdefault("tasks", [])
    st.setdefault("current_task_id", None)
    return st


def current_task(st: dict[str, Any]) -> dict[str, Any] | None:
    cid = st.get("current_task_id")
    return next((t for t in st.get("tasks", []) if t["id"] == cid), None)


async def emit_plan_block(ctx: ToolContext, command: str) -> None:
    st = plan_of(ctx)
    doc = json.dumps({"tasks": st["tasks"], "current_task_id": st["current_task_id"]}, ensure_ascii=False)
    async with ctx.record(command, "Task") as blk:
        await blk.replace(doc)
        await blk.complete(snapshots={"plan.json": doc})


def _plan_text(st: dict[str, Any]) -> str:
    return "\n".join(f"[{t['status']}] {t['id']}. {t['title']} → {t['assignee']}" for t in st["tasks"]) or "(empty)"


@tool(
    "plan_append_task",
    "Append a task to the team plan. Tasks run in order; each has one assignee.",
    {
        "task_id": {"type": "string", "description": "Short unique id, e.g. '1'", "optional": True},
        "title": {"type": "string", "description": "Short task title"},
        "assignee": {"type": "string", "description": "Team member name: David, Emma, Bob, Alex, Iris, Sarah or Adrian"},
        "instruction": {"type": "string", "description": "Self-contained instructions for the assignee"},
        "dependent_task_ids": {"type": "array", "items": {"type": "string"}, "optional": True},
    },
    block_type="Task",
)
async def plan_append_task(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    st = plan_of(ctx)
    role = find_role(str(args.get("assignee"))) or ALEX
    if role.role == "Team Leader":
        return ToolResult("Error: assign tasks to team members (David, Emma, Bob, Alex, Iris, Sarah, Adrian), not yourself.", ok=False)
    tid = str(args.get("task_id") or len(st["tasks"]) + 1)
    if any(t["id"] == tid for t in st["tasks"]):
        tid = str(len(st["tasks"]) + 1)
    st["tasks"].append({
        "id": tid, "title": str(args["title"])[:200], "assignee": role.role, "assignee_name": role.name,
        "status": "pending", "instruction": str(args["instruction"]),
        "dependent_task_ids": [str(x) for x in args.get("dependent_task_ids") or []],
    })
    await emit_plan_block(ctx, "plan_append_task")
    return ToolResult(f"Task {tid} added. Current plan:\n{_plan_text(st)}")


@tool(
    "plan_reset_task",
    "Reset a task (and all tasks after it) back to pending so it will be redone.",
    {"task_id": {"type": "string"}},
    block_type="Task",
)
async def plan_reset_task(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    st = plan_of(ctx)
    ids = [t["id"] for t in st["tasks"]]
    tid = str(args["task_id"])
    if tid not in ids:
        return ToolResult(f"Error: no task {tid}. Plan:\n{_plan_text(st)}", ok=False)
    for t in st["tasks"][ids.index(tid):]:
        t["status"] = "pending"
        t.pop("result", None)
    await emit_plan_block(ctx, "plan_reset_task")
    return ToolResult(f"Reset from task {tid}.\n{_plan_text(st)}")


@tool(
    "plan_finish_current_task",
    "Mark your current task as finished and hand control back to the Team Leader. Call this when done.",
    {"summary": {"type": "string", "description": "One or two sentences on what was delivered", "optional": True}},
    block_type="Task",
)
async def plan_finish_current_task(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    st = plan_of(ctx)
    t = current_task(st)
    if t is not None:
        t["status"] = "done"
        t["result"] = str(args.get("summary") or "")
        await emit_plan_block(ctx, "plan_finish_current_task")
    return ToolResult("Task marked as finished.", end_loop=True)


@tool(
    "mention",
    "Hand off work to a team member by @mentioning them with an instruction. Use after planning.",
    {
        "name": {"type": "string", "description": "David, Emma, Bob, Alex, Iris, Sarah or Adrian"},
        "instruction": {"type": "string"},
    },
)
async def mention(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    role = find_role(str(args["name"]))
    if role is None or role.role == "Team Leader":
        return ToolResult("Error: unknown team member. Use David, Emma, Bob, Alex, Iris, Sarah or Adrian.", ok=False)
    st = plan_of(ctx)
    instruction = str(args["instruction"])
    task = next((t for t in st["tasks"] if t["assignee"] == role.role and t["status"] != "done"), None)
    if task is None:
        task = {"id": str(len(st["tasks"]) + 1), "title": instruction[:80], "assignee": role.role,
                "assignee_name": role.name, "status": "pending", "instruction": instruction,
                "dependent_task_ids": []}
        st["tasks"].append(task)
    msg = await post_message(
        ctx.chat_id, role=ctx.role.role, name=ctx.role.name, type="message",
        content=[{"insert": {"mentiontrigger": {"id": role.role, "char": "@", "value": role.name}}},
                 {"insert": " " + instruction}],
        round_id=ctx.rc.round_id, attempt=ctx.rc.attempt,
    )
    st.setdefault("task_mentions", {})[task["id"]] = msg.id
    return ToolResult(f"@{role.name} has been notified.", end_loop=True, data={"message_id": msg.id})


@tool(
    "ask_human",
    "Ask the user to make a decision and wait for the answer. Provide 2-4 options when possible.",
    {
        "question": {"type": "string"},
        "options": {
            "type": "array", "optional": True,
            "items": {"type": "object", "properties": {"label": {"type": "string"}, "danger": {"type": "boolean"}},
                      "required": ["label"]},
        },
        "title": {"type": "string", "optional": True},
    },
)
async def ask_human(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    question = str(args["question"])
    opts = []
    for o in args.get("options") or []:
        if isinstance(o, str):
            o = {"label": o}
        label = str(o.get("label", "")).strip()
        if label:
            opts.append({"label": label, "content": str(o.get("content") or label), **({"danger": True} if o.get("danger") else {})})
    msg = await post_message(
        ctx.chat_id, role=ctx.role.role, name=ctx.role.name, type="task",
        content={"name": str(args.get("title") or question)[:120], "description": question, "option": opts,
                 "status": "pending"},
        round_id=ctx.rc.round_id, attempt=ctx.rc.attempt,
    )
    return ToolResult("Waiting for the user's answer.", suspend="ask_human", message_id=msg.id)


@tool(
    "ask_questions",
    "Ask the user a short form of clarifying questions before planning, then wait for answers.",
    {
        "title": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "question": {"type": "string"},
                "type": {"type": "string", "enum": ["single", "multi", "text"]},
                "options": {"type": "array", "items": {"type": "string"}}},
                "required": ["question"]},
        },
    },
)
async def ask_questions(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    qs = []
    for i, q in enumerate(args.get("questions") or [], 1):
        if not isinstance(q, dict) or not q.get("question"):
            continue
        typ = q.get("type") if q.get("type") in ("single", "multi", "text") else ("single" if q.get("options") else "text")
        item = {"id": str(q.get("id") or f"q{i}"), "question": str(q["question"]), "type": typ}
        if typ != "text":
            item["options"] = [str(o) for o in q.get("options") or []]
        qs.append(item)
    if not qs:
        return ToolResult("Error: provide at least one question.", ok=False)
    msg = await post_message(
        ctx.chat_id, role=ctx.role.role, name=ctx.role.name, type="questions_card",
        content={"title": str(args.get("title") or "A few questions"), "questions": qs, "status": "pending"},
        round_id=ctx.rc.round_id, attempt=ctx.rc.attempt,
    )
    return ToolResult("Waiting for answers.", suspend="questions", message_id=msg.id)


@tool(
    "reply_to_human",
    "Send the final reply to the user (markdown). This ends your turn.",
    {"content": {"type": "string"}},
)
async def reply_to_human(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    content = str(args["content"]).strip()
    await post_message(
        ctx.chat_id, role=ctx.role.role, name=ctx.role.name, type="message", content=[{"insert": content}],
        round_id=ctx.rc.round_id, attempt=ctx.rc.attempt,
    )
    ctx.rc.state["summary"] = content
    ctx.rc.state["replied"] = True
    return ToolResult("Reply sent.", end_loop=True)
