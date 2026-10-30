"""ReAct tool-calling loop for one role (ARCHITECTURE §6.3).

Each step streams one `think` message (text + inline tool cards), executes the tool calls, appends results to
the role's history in Run.state and persists it — so a run can be suspended/resumed or recovered after a crash.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from oa.agents.context import RunContext, Suspend
from oa.agents.emit import StreamingMessage
from oa.agents.roles import RoleDef
from oa.llm import get_llm
from oa.tools import ToolContext, execute, schemas

log = logging.getLogger("oa.agents")

KEEP_RECENT_TOOL_RESULTS = 8
OLD_RESULT_CHARS = 1500
HISTORY_CHAR_BUDGET = 400_000
NUDGE = ("Continue by calling tools. When the work is completely done, call "
         "`plan_finish_current_task` (plan tasks) or `reply_to_human` (final answer).")


def history(rc: RunContext, role: RoleDef) -> list[dict[str, Any]]:
    hist = rc.state.setdefault("histories", {}).setdefault(role.key, [])
    if not hist:
        hist.append({"role": "system", "content": role.system_prompt})
    return hist


def compact(hist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Context management: shrink stale tool output and file bodies, then drop oldest step groups if needed."""
    tool_idx = [i for i, m in enumerate(hist) if m.get("role") == "tool"]
    stale = set(tool_idx[:-KEEP_RECENT_TOOL_RESULTS])
    last_assistants = {i for i, m in enumerate(hist) if m.get("role") == "assistant"}
    recent_assistant = max(last_assistants) if last_assistants else -1
    out: list[dict[str, Any]] = []
    for i, m in enumerate(hist):
        if i in stale and len(str(m.get("content") or "")) > OLD_RESULT_CHARS:
            c = str(m["content"])
            m = {**m, "content": c[:OLD_RESULT_CHARS] + f"\n…[{len(c) - OLD_RESULT_CHARS} chars omitted]"}
        elif m.get("role") == "assistant" and m.get("tool_calls") and i != recent_assistant:
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments") or "{}"
                if len(args) > 2000:
                    try:
                        a = json.loads(args)
                        for k in ("content", "code", "new_str", "old_str"):
                            if isinstance(a.get(k), str) and len(a[k]) > 400:
                                a[k] = a[k][:200] + f"…[{len(a[k])} chars, already applied]"
                        args = json.dumps(a, ensure_ascii=False)
                    except json.JSONDecodeError:
                        args = args[:2000]
                calls.append({**tc, "function": {**fn, "arguments": args}})
            m = {**m, "tool_calls": calls}
        out.append(m)

    def size(ms: list[dict[str, Any]]) -> int:
        return sum(len(json.dumps(x, ensure_ascii=False)) for x in ms)

    if size(out) <= HISTORY_CHAR_BUDGET:
        return out
    # keep system + first instruction; drop whole assistant(+tool results) groups from the oldest
    head_end = next((i for i, m in enumerate(out) if m.get("role") == "user"), 0) + 1
    head, body = out[:head_end], out[head_end:]
    groups: list[list[dict[str, Any]]] = []
    for m in body:
        if m.get("role") in ("assistant", "user") or not groups:
            groups.append([m])
        else:
            groups[-1].append(m)
    while len(groups) > 2 and size(head) + sum(size(g) for g in groups) > HISTORY_CHAR_BUDGET:
        groups.pop(0)
    note = {"role": "user", "content": "(Earlier steps were omitted to save context. Re-read files if needed.)"}
    return head + [note] + [m for g in groups for m in g]


async def _charge(rc: RunContext, role: RoleDef, model: str, usage: Any) -> None:
    rc.state["usage"] = {
        "input_tokens": rc.state.get("usage", {}).get("input_tokens", 0) + usage.input_tokens,
        "output_tokens": rc.state.get("usage", {}).get("output_tokens", 0) + usage.output_tokens,
    }
    try:
        from oa.domain.billing import charge_usage

        await charge_usage(rc.chat_id, rc.run_id, role.role, model, usage.input_tokens, usage.output_tokens)
    except Exception:
        log.debug("charge_usage unavailable/failed", exc_info=True)


async def run_role(
    rc: RunContext,
    role: RoleDef,
    *,
    instruction: str | None = None,
    tools: list[str] | None = None,
    max_steps: int | None = None,
) -> str:
    """Run until the role ends its turn. Returns 'done' | 'no_tools' | 'max_steps'. May raise Suspend/RunCancelled."""
    hist = history(rc, role)
    if instruction:
        hist.append({"role": "user", "content": instruction})
        await rc.save_state()
    tool_names = list(tools or role.tools)
    tool_schemas = schemas(tool_names)
    llm = get_llm(rc.model)
    nudged = False
    steps = max_steps or role.max_steps

    for step in range(steps):
        await rc.poll_cancel()
        msg = StreamingMessage(rc.chat_id, role=role.role, name=role.name, round_id=rc.round_id,
                               attempt=rc.attempt, on_open=rc.open_messages.add)

        async def on_text(t: str, msg: StreamingMessage = msg) -> None:
            rc.check_cancel()  # aborts the provider stream promptly on stop
            await msg.append_text(t)

        try:
            result = await llm.stream(compact(hist), tool_schemas, on_text=on_text, role=role.role)
        except BaseException:
            await msg.finish()
            raise
        await _charge(rc, role, getattr(llm, "model", rc.model), result.usage)
        hist.append(result.assistant_message())

        if not result.tool_calls:
            await msg.finish()
            rc.open_messages.discard(msg.id or -1)
            if not nudged and step < steps - 1:
                nudged = True
                hist.append({"role": "user", "content": NUDGE})
                await rc.save_state()
                continue
            if result.text.strip():
                rc.state["summary"] = result.text.strip()
            await rc.save_state()
            return "no_tools"

        end = False
        suspend: tuple[str, int | None, str] | None = None
        for tc in result.tool_calls:
            if suspend is not None:
                hist.append({"role": "tool", "tool_call_id": tc.id,
                             "content": "Not executed: waiting for the user's answer to the previous question."})
                continue
            await rc.poll_cancel()
            res = await execute(ToolContext(rc, role, msg, tc.id), tc.name, tc.arguments)
            hist.append({"role": "tool", "tool_call_id": tc.id, "content": res.output})
            if res.suspend:
                suspend = (res.suspend, res.message_id, tc.id)
            end = end or res.end_loop
        await msg.finish()
        rc.open_messages.discard(msg.id or -1)
        if suspend is not None:
            rc.state["pending"] = {"role": role.key, "tool_call_id": suspend[2], "kind": suspend[0]}
            await rc.save_state()
            raise Suspend(suspend[0], suspend[1])
        await rc.save_state()
        if end:
            return "done"
    return "max_steps"


def answer_pending(rc: RunContext, answer: str) -> bool:
    """Replace the placeholder result of the suspended tool call with the human's answer."""
    pending = rc.state.get("pending") or {}
    hist = rc.state.get("histories", {}).get(pending.get("role", ""), [])
    for m in reversed(hist):
        if m.get("role") == "tool" and m.get("tool_call_id") == pending.get("tool_call_id"):
            m["content"] = answer
            rc.state.pop("pending", None)
            return True
    rc.state.pop("pending", None)
    return False
