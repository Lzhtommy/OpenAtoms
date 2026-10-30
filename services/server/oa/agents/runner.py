"""Run scheduling and lifecycle (CONTRACT §10.2).

start_round → Run(queued) → _execute (in-process task, or arq job in a worker) → Orchestrator.run
  ├─ Suspend   → Run(suspended)   … resume(action) → Run(queued) → _execute again (state-preserving)
  ├─ cancelled → Run(cancelled)
  ├─ failure   → Run(failed) + error message
  └─ success   → memory update → version (git commit/tag + build + upload) → Run(success)
Always: Chat.is_running/chat:update, run:start/run:end, persist_seq, and (unless suspended) queue.pop_next.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from oa.agents.context import RunCancelled, RunContext, Suspend
from oa.agents.emit import delta_text, update_message
from oa.config import settings
from oa.core.events import bus, persist_seq
from oa.db.models import Chat, Message, Run, Version
from oa.db.session import session_scope

log = logging.getLogger("oa.runner")

_tasks: dict[str, asyncio.Task] = {}  # chat_id -> running task (inproc)
_events: dict[str, asyncio.Event] = {}  # chat_id -> cancel event of the live run
CANCEL_GRACE = 8.0
# Test/embedding seams: override how runs obtain a sandbox client and the preview upstream.
SANDBOX_FACTORY: Any = None
PREVIEW_RESOLVER: Any = None
MEMORY_MAX = 20_000


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- public API
async def start_round(chat_id: str, message_id: int) -> int:
    async with session_scope() as s:
        chat = await s.get(Chat, chat_id)
        msg = await s.get(Message, message_id)
        if chat is None or msg is None:
            raise ValueError("chat or message not found")
        if _live(chat_id):
            raise RuntimeError("a run is already in progress for this chat")
        # supersede anything left over (suspended runs of earlier rounds, stale rows after a crash)
        stale_q = select(Run).where(Run.chat_id == chat_id, Run.status.in_(("queued", "running", "suspended")))
        for old in (await s.execute(stale_q)).scalars():
            if settings.queue == "arq" and old.status in ("queued", "running"):
                raise RuntimeError("a run is already in progress for this chat")
            old.status, old.finished_at = "cancelled", _now()
            if old.suspend_message_id:
                await _close_card(s, old.suspend_message_id, "finished")
        meta = msg.metadata_ or {}
        cfg = chat.config or {}
        run = Run(
            chat_id=chat_id, round_id=msg.round_id, attempt=msg.attempt, trigger_message_id=msg.id, status="queued",
            agent_mode=str(meta.get("agent_mode") or cfg.get("agent_mode") or "full"),
            model=str(meta.get("default_model") or (chat.llm or {}).get("default_model") or settings.default_model),
            state={"plan_mode": bool(meta.get("plan_mode", cfg.get("plan_mode", False))),
                   "deep_research": bool(meta.get("enable_dr"))},
        )
        s.add(run)
        chat.is_running = True
        await s.flush()
        run_id = run.id
    await _flush_cards()
    await _emit_chat(chat_id)
    await _schedule(chat_id, run_id)
    return run_id


async def suspended_run(chat_id: str) -> Run | None:
    async with session_scope() as s:
        return (
            await s.execute(
                select(Run).where(Run.chat_id == chat_id, Run.status == "suspended").order_by(Run.id.desc()).limit(1)
            )
        ).scalar_one_or_none()


async def resume(chat_id: str, action: str, message_id: int | None = None, payload: dict | None = None) -> bool:
    payload = payload or {}
    async with session_scope() as s:
        run = (
            await s.execute(
                select(Run).where(Run.chat_id == chat_id, Run.status == "suspended").order_by(Run.id.desc()).limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            return False
        st: dict[str, Any] = dict(run.state or {})
        reason = run.suspend_reason
        card_id = run.suspend_message_id
        card = await s.get(Message, card_id) if card_id else None
        text = await _reply_text(s, message_id, payload, card)

        if reason == "plan_ready":
            if action == "approve":
                st["plan_approved"], st["phase"] = True, "execute"
                await _close_card(s, card_id, "approved")
            elif action == "decline":
                st["phase"], st["decline_reason"] = "declined", payload.get("reason") or ""
                await _close_card(s, card_id, "declined")
            else:  # free-text feedback → Mike revises the plan
                await _close_card(s, card_id, "declined")
                st["tasks"], st["phase"], st["current_task_id"] = [], "full_plan", None
                _push(st, "Mike", f"The user reviewed the plan and replied: {text}\n"
                                  "Create a revised plan with plan_append_task, then mention the first assignee.")
        elif reason == "questions":
            answers = payload.get("answers")
            ans = _format_answers(card, answers) if answers else text
            _answer_pending(st, f"The user answered the questions:\n{ans}")
            if card is not None and (card.content or {}).get("status") != "finished":
                await _close_card(s, card_id, "finished", answers=answers or {"text": text})
        else:  # ask_human
            pending = st.get("pending") or {}
            option = _selected_option(card, payload)
            if pending.get("kind") == "build_decision":
                st.pop("pending", None)
                yes = option == 0 or (option is None and _affirmative(text))
                st["build_decision"] = "continue" if yes else "stop"
            else:
                label = _option_label(card, option)
                _answer_pending(st, f"The user answered: {label or text}")
            if card is not None and (card.content or {}).get("status") != "finished":
                await _close_card(s, card_id, "finished", **({"selected": option} if option is not None else {}))

        run.state = st
        run.status, run.suspend_reason, run.suspend_message_id = "queued", None, None
        chat = await s.get(Chat, chat_id)
        if chat is not None:
            chat.is_running = True
        run_id = run.id
    await _flush_cards()
    await _emit_chat(chat_id)
    await _schedule(chat_id, run_id)
    return True


async def cancel(chat_id: str) -> bool:
    ev = _events.get(chat_id)
    task = _tasks.get(chat_id)
    if ev is not None and task is not None and not task.done():
        ev.set()
        done, _ = await asyncio.wait({task}, timeout=CANCEL_GRACE)
        if not done:
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait({task}, timeout=5)
        return True
    async with session_scope() as s:
        runs = (await s.execute(
            select(Run).where(Run.chat_id == chat_id, Run.status.in_(("queued", "running", "suspended")))
        )).scalars().all()
        if not runs:
            return False
        for run in runs:
            if run.status == "suspended" or settings.queue != "arq":
                run.status, run.finished_at = "cancelled", _now()
                if run.suspend_message_id:
                    await _close_card(s, run.suspend_message_id, "finished")
            else:
                await _set_remote_cancel(run.id)
        if all(r.status == "cancelled" for r in runs):
            chat = await s.get(Chat, chat_id)
            if chat is not None:
                chat.is_running = False
    await _flush_cards()
    await _emit_chat(chat_id)
    return True


async def recover_interrupted() -> int:
    """Re-schedule runs left `running`/`queued` by a crashed process (state is resumable)."""
    async with session_scope() as s:
        runs = (await s.execute(select(Run).where(Run.status.in_(("queued", "running"))))).scalars().all()
        ids = [(r.chat_id, r.id) for r in runs]
        for r in runs:
            r.status = "queued"
    for chat_id, run_id in ids:
        if not _live(chat_id):
            await _schedule(chat_id, run_id)
    return len(ids)


def is_running(chat_id: str) -> bool:
    return _live(chat_id)


# ---------------------------------------------------------------- scheduling
def _live(chat_id: str) -> bool:
    t = _tasks.get(chat_id)
    # A finishing run hands off to the next queued prompt from inside its own task (pop_next);
    # that task has already persisted its final status, so it must not count as live.
    return t is not None and not t.done() and t is not asyncio.current_task()


async def _schedule(chat_id: str, run_id: int) -> None:
    if settings.queue == "arq":
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            await pool.enqueue_job("run_job", run_id, _job_id=f"run:{run_id}:{_now().timestamp()}")
        finally:
            await pool.aclose()
        return
    task = asyncio.create_task(execute_run(run_id), name=f"run:{run_id}")
    _tasks[chat_id] = task
    task.add_done_callback(lambda t, c=chat_id: _tasks.pop(c, None) if _tasks.get(c) is t else None)


async def _set_remote_cancel(run_id: int) -> None:
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.redis_url)
    try:
        await r.set(f"oa:cancel:{run_id}", "1", ex=3600)
    finally:
        await r.aclose()


@contextlib.asynccontextmanager
async def _run_lock(chat_id: str):
    if not settings.use_redis:
        yield
        return
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.redis_url)
    lock = r.lock(f"oa:runlock:{chat_id}", timeout=120, blocking_timeout=60)
    if not await lock.acquire():
        await r.aclose()
        raise RuntimeError("could not acquire run lock")

    async def renew() -> None:
        while True:
            await asyncio.sleep(40)
            with contextlib.suppress(Exception):
                await lock.reacquire()

    t = asyncio.create_task(renew())
    try:
        yield
    finally:
        t.cancel()
        with contextlib.suppress(Exception):
            await lock.release()
        await r.aclose()


# ---------------------------------------------------------------- execution
async def execute_run(run_id: int) -> None:
    async with session_scope() as s:
        run = await s.get(Run, run_id)
        if run is None or run.status not in ("queued", "running"):
            return
        chat_id = run.chat_id
    try:
        async with _run_lock(chat_id):
            await _execute(run_id)
    except Exception:
        log.exception("run %s crashed", run_id)


async def _execute(run_id: int) -> None:
    from oa.agents.orchestrator import Orchestrator

    async with session_scope() as s:
        run = await s.get(Run, run_id)
        chat = await s.get(Chat, run.chat_id)
        trigger = await s.get(Message, run.trigger_message_id)
        first_start = not (run.state or {}).get("phase")
        run.status = "running"
        chat.is_running = True
        chat_id, round_id, attempt = run.chat_id, run.round_id, run.attempt
        state = dict(run.state or {})
        model, mode = run.model, run.agent_mode
        user_id = trigger.user_id if trigger else chat.user_id
        prompt = _prompt_text(trigger)
        n = int((await s.execute(select(func.max(Version.number)).where(Version.chat_id == chat_id))).scalar() or 0)

    ev = asyncio.Event()
    _events[chat_id] = ev
    rc = RunContext(
        run_id=run_id, chat_id=chat_id, round_id=round_id, attempt=attempt, model=model, agent_mode=mode,
        plan_mode=bool(state.get("plan_mode")), user_id=user_id, state=state, version_label=f"v{n + 1}",
        secrets=await _secret_values(chat_id), cancel_event=ev,
    )
    if SANDBOX_FACTORY is not None:
        rc.sandbox_factory = SANDBOX_FACTORY
    if PREVIEW_RESOLVER is not None:
        rc.preview_resolver = PREVIEW_RESOLVER
    await _emit_chat(chat_id)
    await bus.emit(chat_id, "run:start", {"round_id": round_id, "attempt": attempt, "run_id": run_id,
                                          "resumed": not first_start})

    status, error, outcome, suspend = "failed", None, None, None
    try:
        context = await _project_context(chat_id) if first_start else ""
        outcome = await Orchestrator(rc, prompt, context).run()
        status = "success"
    except Suspend as sp:
        status, suspend = "suspended", sp
    except (RunCancelled, asyncio.CancelledError):
        status = "cancelled"
    except Exception as e:
        log.exception("run %s failed", run_id)
        error = f"{type(e).__name__}: {e}"
    finally:
        _events.pop(chat_id, None)

    await _close_open_messages(rc)
    if status == "failed":
        with contextlib.suppress(Exception):
            from oa.agents.emit import post_message

            await post_message(chat_id, role="System", name="System", type="error", round_id=round_id,
                               attempt=attempt, content={"message": f"The run failed: {error}", "code": 5000})

    if status == "success" and outcome and not outcome.get("skip_version"):
        await _update_memory(chat_id, prompt, outcome.get("summary") or "")
        if await asyncio.to_thread(_workspace_changed, chat_id):
            try:
                from oa.domain.versions import create_version

                await create_version(chat_id, _version_summary(prompt, outcome.get("summary")), run_id)
            except Exception:
                log.exception("create_version failed for %s", chat_id)

    async with session_scope() as s:
        run = await s.get(Run, run_id)
        run.state = _jsonable(rc.state)
        run.status = status
        run.error = error
        if suspend is not None:
            run.suspend_reason, run.suspend_message_id = suspend.reason, suspend.message_id
        else:
            run.finished_at = _now()
        chat = await s.get(Chat, chat_id)
        chat.is_running = False
    await _emit_chat(chat_id)
    await bus.emit(chat_id, "run:end", {"round_id": round_id, "attempt": attempt, "run_id": run_id, "status": status,
                                        **({"suspend_reason": suspend.reason} if suspend else {})})
    with contextlib.suppress(Exception):
        await persist_seq(chat_id)
    with contextlib.suppress(Exception):
        from oa.tools.web import close_session

        await close_session(chat_id)
    if status != "suspended":
        try:
            from oa.domain.queue import pop_next

            await pop_next(chat_id)
        except Exception:
            log.exception("pop_next failed for %s", chat_id)


# ---------------------------------------------------------------- helpers
async def _emit_chat(chat_id: str) -> None:
    async with session_scope() as s:
        chat = await s.get(Chat, chat_id)
        if chat is None:
            return
        meta = {"status": chat.runtime_status, "is_running": chat.is_running, "preview_port": 5173}
    await bus.emit(chat_id, "chat:update", {"chat": {"chat_id": chat_id, "metadata": meta}})


async def _close_open_messages(rc: RunContext) -> None:
    for mid in list(rc.open_messages):
        async with session_scope() as s:
            m = await s.get(Message, mid)
            unfinished = m is not None and not m.is_finished
        if unfinished:
            with contextlib.suppress(Exception):
                await update_message(rc.chat_id, mid, is_finished=True)
    rc.open_messages.clear()
    # tool blocks left running (hard cancellation) are closed as failed
    from oa.db.models import ActionData, BlockTimeline

    async with session_scope() as s:
        await s.execute(update(ActionData).where(ActionData.chat_id == rc.chat_id, ActionData.status == "running")
                        .values(status="failed"))
        await s.execute(update(BlockTimeline).where(BlockTimeline.chat_id == rc.chat_id,
                                                    BlockTimeline.is_running.is_(True)).values(is_running=False))


_card_updates: list[tuple[str, dict]] = []


async def _close_card(s: Any, card_id: int | None, status: str, **extra: Any) -> None:
    """Update a card's status in the open session; the msg:update is sent by _flush_cards after commit."""
    if not card_id:
        return
    card = await s.get(Message, card_id)
    if card is None or not isinstance(card.content, dict):
        return
    card.content = {**card.content, "status": status, **extra}
    await s.flush()
    _card_updates.append((card.chat_id, {"id": card.id, "content": card.content}))


async def _flush_cards() -> None:
    while _card_updates:
        chat_id, payload = _card_updates.pop(0)
        await bus.emit(chat_id, "msg:update", payload)


async def _reply_text(s: Any, message_id: int | None, payload: dict, card: Message | None) -> str:
    if payload.get("text"):
        return str(payload["text"])
    if message_id and (card is None or message_id != card.id):
        m = await s.get(Message, message_id)
        if m is not None:
            return delta_text(m.content)
    return ""


def _selected_option(card: Message | None, payload: dict) -> int | None:
    tr = payload.get("task_reply") or {}
    if tr.get("option_index") is not None:
        return int(tr["option_index"])
    if card is not None and isinstance(card.content, dict):
        text = str(payload.get("text") or "").strip().lower()
        for i, o in enumerate(card.content.get("option") or []):
            if text and text in (str(o.get("label", "")).lower(), str(o.get("content", "")).lower()):
                return i
    return None


def _option_label(card: Message | None, idx: int | None) -> str | None:
    if card is None or idx is None or not isinstance(card.content, dict):
        return None
    opts = card.content.get("option") or []
    return str(opts[idx].get("content") or opts[idx].get("label")) if 0 <= idx < len(opts) else None


def _affirmative(text: str) -> bool:
    t = text.strip().lower()
    if any(x in t for x in ("do not", "don't", "dont", "stop", "no", "不要", "停止", "别")):
        return False
    return any(x in t for x in ("yes", "continue", "ok", "sure", "好", "继续", "是"))


def _format_answers(card: Message | None, answers: dict) -> str:
    qs = {q.get("id"): q.get("question") for q in ((card.content or {}).get("questions") or [])} if card else {}
    lines = []
    for k, v in answers.items():
        val = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        lines.append(f"- {qs.get(k, k)}: {val}")
    return "\n".join(lines)


def _answer_pending(st: dict, answer: str) -> None:
    pending = st.get("pending") or {}
    hist = st.get("histories", {}).get(pending.get("role", ""), [])
    for m in reversed(hist):
        if m.get("role") == "tool" and m.get("tool_call_id") == pending.get("tool_call_id"):
            m["content"] = answer
            break
    st.pop("pending", None)


def _push(st: dict, role_key: str, content: str) -> None:
    st.setdefault("histories", {}).setdefault(role_key, []).append({"role": "user", "content": content})


def _prompt_text(msg: Message | None) -> str:
    if msg is None:
        return ""
    text = delta_text(msg.content).strip()
    atts = (msg.metadata_ or {}).get("attachments") or []
    if atts:
        lines = "\n".join(f"- {a.get('name')} ({a.get('path') or a.get('url')}, {a.get('mime', '')})" for a in atts)
        text += f"\n\nAttached files (in the workspace):\n{lines}"
    variant = (msg.metadata_ or {}).get("boost_variant")
    if isinstance(variant, dict) and variant.get("total"):
        text += (
            f"\n\n(Best-of-N competition: you are candidate {int(variant.get('index', 0)) + 1} of "
            f"{int(variant['total'])}. Pick a distinctive visual direction so the candidates differ.)"
        )
    sel = (msg.metadata_ or {}).get("selection")
    if isinstance(sel, dict) and sel.get("loc"):
        # visual-edit "Ask AI about this": pin the request to the element the user picked in the preview
        snippet = str(sel.get("text") or "").strip()[:200]
        text += (
            "\n\nThe user selected this element in the live preview; apply the request to it:\n"
            f"- source location: {sel['loc']} (file:line:column of the JSX tag)\n"
            f"- tag: <{sel.get('tag') or '?'}>  selector: {sel.get('selector') or '?'}\n"
            f"- classes: {' '.join(sel.get('classes') or []) or '(none)'}"
            + (f"\n- text: {snippet!r}" if snippet else "")
        )
    return text


async def _project_context(chat_id: str) -> str:
    mem = settings.workspace_dir(chat_id) / ".atoms" / "memory.md"
    if not mem.exists():
        # first run of a new project: the workspace is created from the template only when the sandbox starts,
        # so read the template's memory (it tells agents e.g. that an expo-app project is React Native)
        from oa.sandbox.manager import template_path

        async with session_scope() as s:
            chat = await s.get(Chat, chat_id)
            template = ((chat.config or {}).get("template_name") if chat else None) or None
        mem = template_path(template) / ".atoms" / "memory.md"
    parts = []
    try:
        if mem.exists():
            body = mem.read_text()[-4000:].strip()
            if body:
                parts.append(f"Project memory (.atoms/memory.md):\n{body}")
    except OSError:
        pass
    return "\n\n".join(parts)


async def _update_memory(chat_id: str, prompt: str, summary: str) -> None:
    mem = settings.workspace_dir(chat_id) / ".atoms" / "memory.md"
    if not mem.parent.exists():
        return
    try:
        old = mem.read_text() if mem.exists() else "# Project memory\n\n"
        entry = f"## {_now():%Y-%m-%d %H:%M} — {prompt.strip().splitlines()[0][:160] if prompt.strip() else 'update'}\n" \
                f"{summary.strip()[:800]}\n\n"
        new = old + entry
        if len(new) > MEMORY_MAX:
            new = new[:400] + "\n…\n" + new[-(MEMORY_MAX - 500):]
        mem.write_text(new)
    except OSError:
        log.warning("memory update failed for %s", chat_id)


def _workspace_changed(chat_id: str) -> bool:
    ws = settings.workspace_dir(chat_id)
    if not (ws / ".git").exists():
        return False
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ws, capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def _version_summary(prompt: str, summary: str | None) -> str:
    first = (prompt.strip().splitlines() or [""])[0][:120]
    return first or (summary or "Update")[:120]


async def _secret_values(chat_id: str) -> list[str]:
    try:
        from oa.domain.secrets import secret_values

        return [v for v in await secret_values(chat_id) if v]
    except Exception:
        return []


def _jsonable(v: Any) -> Any:
    import json

    return json.loads(json.dumps(v, default=str))
