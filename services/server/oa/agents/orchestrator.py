"""Team orchestration (ARCHITECTURE §6.3, CONTRACT §4).

A resumable state machine stored in Run.state:

  lite:  lite ──▶ verify ──▶ finish
  full:  plan ──▶ [plan_review ⏸] ──▶ execute(task₁…taskₙ, Alex tasks verified) ──▶ summary ──▶ finish
                       └─ declined

Every phase is idempotent with respect to resumption: a Suspend raised anywhere leaves `phase` (and per-task
`stage`) where it was, and the runner re-enters `run()` after injecting the human's answer.
"""
from __future__ import annotations

import logging
from typing import Any

from oa.agents.context import RunContext, Suspend
from oa.agents.emit import StreamingMessage, post_message
from oa.agents.role_loop import run_role
from oa.agents.roles import ALEX, IRIS, MIKE, ROLES, RoleDef
from oa.tools import ToolContext, execute

log = logging.getLogger("oa.agents")

MAX_BUILD_FIX_ROUNDS = 3
BUILD_DECISION = "Decision to continue fixing the build"


class Orchestrator:
    def __init__(self, rc: RunContext, prompt: str, context: str = "") -> None:
        self.rc = rc
        self.st = rc.state
        self.st.setdefault("prompt", prompt)
        self.st.setdefault("context", context)

    # ------------------------------------------------------------------ helpers
    @property
    def prompt(self) -> str:
        return self.st["prompt"]

    async def _role(self, key: str, role: RoleDef, instruction: str | None, **kw: Any) -> str:
        """Run a role; the instruction is added only the first time `key` is entered (resume-safe)."""
        started = self.st.setdefault("started", [])
        if key in started:
            instruction = None
        else:
            started.append(key)
        return await run_role(self.rc, role, instruction=instruction, **kw)

    async def _post(self, role: RoleDef, type: str, content: Any, refer_id: int | None = None) -> int:
        m = await post_message(self.rc.chat_id, role=role.role, name=role.name, type=type, content=content,
                               round_id=self.rc.round_id, attempt=self.rc.attempt, refer_id=refer_id)
        return m.id

    def _requirement_block(self) -> str:
        ctx = self.st.get("context") or ""
        return f"User requirement:\n{self.prompt}" + (f"\n\n{ctx}" if ctx else "")

    # ------------------------------------------------------------------ main loop
    async def run(self) -> dict[str, Any]:
        st = self.st
        st.setdefault("phase", "research" if st.get("deep_research") else
                      "full_plan" if self.rc.agent_mode == "full" else "lite")
        while True:
            self.rc.check_cancel()
            phase = st["phase"]
            if phase == "lite":
                await self._role("lite", ALEX, self._requirement_block())
                st["phase"] = "lite_verify"
            elif phase == "lite_verify":
                await self.verify()
                if not st.get("replied"):
                    await self._post(ALEX, "message", [{"insert": self._fallback_summary()}])
                st["phase"] = "finish"
            elif phase == "research":
                # Deep Research mode (enable_dr): Iris runs an iterative search → report loop on her own.
                await self._role("research", IRIS, self._requirement_block() + "\n\nResearch this in depth and "
                                 "write the report with researchexecutor_finalize_article, then reply_to_human.",
                                 tools=[t for t in IRIS.tools if t != "plan_finish_current_task"])
                if not st.get("replied"):
                    await self._post(IRIS, "message", [{"insert": "The research report is ready in "
                                                        "docs/research/report.md."}])
                st["phase"] = "finish"
            elif phase == "full_plan":
                await self._plan()
            elif phase == "plan_review":
                await self._plan_review()
            elif phase == "execute":
                await self._execute()
                st["phase"] = "summary"
            elif phase == "summary":
                await self._summary()
                st["phase"] = "finish"
            elif phase == "declined":
                if not st.get("declined_msg"):
                    st["declined_msg"] = True
                    reason = st.get("decline_reason")
                    await self._post(MIKE, "message", [{"insert": "Understood — I've set this plan aside"
                                                        + (f" ({reason})" if reason else "")
                                                        + ". Tell me what you'd like to change and I'll re-plan."}])
                return {"summary": "", "build_ok": None, "skip_version": True}
            elif phase == "finish":
                return {"summary": st.get("summary") or self.prompt[:200], "build_ok": not st.get("build_failed"),
                        "skip_version": bool(st.get("build_failed"))}
            else:  # pragma: no cover
                raise RuntimeError(f"unknown phase {phase}")
            await self.rc.save_state()

    # ------------------------------------------------------------------ full mode
    async def _plan(self) -> None:
        st = self.st
        tools = [t for t in MIKE.tools if t != "plan_finish_current_task"]
        instruction = (
            f"{self._requirement_block()}\n\n"
            "Plan the work for the team now (plan_append_task for each task, then mention the first assignee). "
            "If no work is needed, answer with reply_to_human."
        )
        await self._role("plan", MIKE, instruction, tools=tools)
        tasks = st.get("tasks") or []
        if not tasks:
            if not st.get("replied"):
                await self._post(MIKE, "message", [{"insert": st.get("summary") or "Okay."}])
                st["replied"] = True
            st["phase"] = "finish"
            st["no_work"] = True
            return
        if self.rc.plan_mode and not st.get("plan_approved"):
            st["phase"] = "plan_review"
        else:
            st["phase"] = "execute"

    async def _plan_review(self) -> None:
        st = self.st
        if st.get("plan_approved"):
            st["phase"] = "execute"
            return
        tasks = [{k: t[k] for k in ("id", "title", "assignee", "status", "instruction")} for t in st.get("tasks", [])]
        summary = "Here is the proposed plan. Approve it to start, or decline and tell me what to change."
        card_id = await self._post(MIKE, "plan_ready", {"summary": summary, "tasks": tasks, "status": "pending"})
        st["plan_card_id"] = card_id
        await self.rc.save_state()
        raise Suspend("plan_ready", card_id)

    async def _execute(self) -> None:
        st = self.st
        mentions: dict[str, int] = st.setdefault("task_mentions", {})
        acks: list[str] = st.setdefault("acks", [])
        for t in st.get("tasks", []):
            if t.get("status") == "done":
                continue
            role = ROLES.get(t["assignee"], ALEX)
            st["current_task_id"] = t["id"]
            t["status"] = "running"
            if t["id"] not in mentions:
                mentions[t["id"]] = await self._post(MIKE, "message", [
                    {"insert": {"mentiontrigger": {"id": role.role, "char": "@", "value": role.name}}},
                    {"insert": " " + t["instruction"]},
                ])
            if t["id"] not in acks:
                await self._post(role, "emoji", {"id": "ok"}, refer_id=mentions[t["id"]])
                acks.append(t["id"])
            await self.rc.save_state()

            if t.get("stage") != "verify":
                instruction = (
                    f"## Task {t['id']}: {t['title']}\n{t['instruction']}\n\n{self._requirement_block()}\n\n"
                    f"{self._done_so_far()}When this task is complete, call plan_finish_current_task."
                )
                tools = [x for x in role.tools if x != "reply_to_human"] if role is ALEX else None
                await self._role(f"task:{t['id']}", role, instruction, tools=tools)
                t["stage"] = "verify"
                await self.rc.save_state()
            if role.role in ("Engineer", "SEO Specialist"):  # roles that edit app code must leave the build green
                await self.verify()
            t["status"] = "done"
            await self.rc.save_state()
        st["current_task_id"] = None

    def _done_so_far(self) -> str:
        done = [t for t in self.st.get("tasks", []) if t.get("status") == "done"]
        if not done:
            return ""
        lines = "\n".join(f"- {t['assignee']} ({t['title']}): {t.get('result') or 'done'}" for t in done)
        return f"Completed so far by the team:\n{lines}\n\n"

    async def _summary(self) -> None:
        st = self.st
        results = "\n".join(f"- {t['title']} ({t['assignee']}): {t.get('result') or 'done'}" for t in st.get("tasks", []))
        build = "The production build FAILED and the user chose to stop fixing." if st.get("build_failed") else \
            "The production build passes."
        st["replied"] = False
        await self._role(
            "summary", MIKE,
            f"[summary] All tasks are complete.\n{results}\n{build}\n\n{self._requirement_block()}\n\n"
            "Write the final reply to the user with reply_to_human.",
            tools=["reply_to_human"], max_steps=2,
        )
        if not st.get("replied"):
            await self._post(MIKE, "message", [{"insert": st.get("summary") or self._fallback_summary()}])

    def _fallback_summary(self) -> str:
        if self.st.get("build_failed"):
            return "I stopped here: the build is still failing. Tell me how you'd like to proceed."
        return "All done — the app is ready in the preview."

    # ------------------------------------------------------------------ build verification / self-repair
    async def _has_build(self) -> bool:
        try:
            sb = await self.rc.sandbox()
            import json as _json

            pkg = _json.loads((await sb.read("package.json")).decode())
            return "build" in pkg.get("scripts", {})
        except Exception:
            return False

    async def _run_build(self) -> bool:
        msg = StreamingMessage(self.rc.chat_id, role=ALEX.role, name=ALEX.name, round_id=self.rc.round_id,
                               attempt=self.rc.attempt, on_open=self.rc.open_messages.add)
        await msg.append_text("Verifying the production build…")
        try:
            res = await execute(ToolContext(self.rc, ALEX, msg), "terminal_run", {"cmd": "pnpm build"})
        finally:
            await msg.finish()
        return res.ok

    def _fix_tools(self) -> list[str]:
        if self.rc.agent_mode == "full":
            return [x for x in ALEX.tools if x != "reply_to_human"]
        return [x for x in ALEX.tools if x != "plan_finish_current_task"]

    async def verify(self) -> bool:
        st = self.st
        if not await self._has_build():
            return True
        if st.get("fixing"):
            # resumed while a fix pass was suspended (e.g. Alex asked the user something): continue it
            await self._role(st["fixing"], ALEX, None, tools=self._fix_tools())
            st.pop("fixing", None)
        while True:
            self.rc.check_cancel()
            if st.get("last_build_ok") and not st.get("dirty"):
                return True
            rounds = int(st.get("build_fix_rounds", 0))
            if rounds >= MAX_BUILD_FIX_ROUNDS:
                decision = st.pop("build_decision", None)
                if decision == "continue":
                    st["build_fix_rounds"] = 0
                    continue
                if decision == "stop":
                    st["build_failed"] = True
                    return False
                card = await self._post(ALEX, "task", {
                    "name": BUILD_DECISION,
                    "description": "The build is still failing after several automatic fix attempts. "
                                   "Should I continue fixing it?",
                    "option": [{"label": "Yes, continue", "content": "Yes, continue"},
                               {"label": "Do not continue", "content": "Do not continue", "danger": True}],
                    "status": "pending",
                })
                st["pending"] = {"kind": "build_decision"}
                await self.rc.save_state()
                raise Suspend("ask_human", card)
            if await self._run_build():
                return True
            st["build_fix_rounds"] = rounds + 1
            await self.rc.save_state()
            if st["build_fix_rounds"] >= MAX_BUILD_FIX_ROUNDS:
                continue
            key = f"fix:{st.get('fix_seq', 0)}"
            st["fix_seq"] = int(st.get("fix_seq", 0)) + 1
            st["fixing"] = key
            output = st.get("last_build_output", "")[-5000:]
            await self._role(
                key, ALEX,
                "The production build failed. Read the errors, fix the code, then run `pnpm build` again until it "
                f"passes.\n\n```\n{output}\n```",
                tools=self._fix_tools(),
            )
            st.pop("fixing", None)
