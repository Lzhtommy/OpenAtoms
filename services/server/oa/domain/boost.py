"""Best-of-N / Boost (竞赛模式).

Mirrors Atoms' design (chat `bon.parent_chat_id`): each candidate is a hidden *child chat* forked from the
parent's current workspace, running an ordinary round with its own model, sandbox, preview and versions.
A watcher follows the children, publishes `boost:update`, asks a judge (basic model; deterministic for
`oa-mock`) for a recommendation and emits `boost:all_end`. Selecting a candidate syncs its workspace into the
parent, creates a parent version and emits `boost:merge_end`.

While a boost is active the parent is marked running, so new prompts go to its prompt queue.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from oa.config import settings
from oa.core.events import bus
from oa.db import models as m
from oa.db.session import session_scope
from oa.domain import hooks
from oa.domain import serializers as ser

log = logging.getLogger("oa.boost")

MIN_K, MAX_K = 2, 4
POLL = 1.0
FINAL = ("success", "failed", "cancelled")
SKIP = {".git", "node_modules", "dist", ".pnpm-store", ".vite", ".expo"}
_watchers: dict[int, asyncio.Task] = {}


class BoostError(ValueError):
    pass


# ---------------------------------------------------------------- serialization
def serialize(b: m.Boost) -> dict[str, Any]:
    return {
        "id": b.id, "chat_id": b.chat_id, "status": b.status, "items": b.items or [],
        "recommended": b.recommended, "selected": b.selected, "judge_reason": b.judge_reason,
        "message_id": b.message_id, "card_message_id": b.card_message_id,
        "created_at": ser.iso(b.created_at), "updated_at": ser.iso(b.updated_at),
    }


async def _publish(boost_id: int, event: str = "boost:update") -> dict[str, Any]:
    """Persist-free broadcast of the boost state + refresh of the parent's `boost` card message."""
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        out = serialize(b)
        card = await s.get(m.Message, b.card_message_id) if b.card_message_id else None
        if card is not None:
            card.content = {"boost_id": b.id, "status": b.status, "items": b.items, "recommended": b.recommended,
                            "selected": b.selected, "judge_reason": b.judge_reason}
            card.is_finished = b.status in ("ready", "merged", "aborted", "failed")
            payload = {"id": card.id, "content": card.content, "is_finished": card.is_finished,
                       "metadata": card.metadata_ or {}}
        chat_id = b.chat_id
    if card is not None:
        await bus.emit(chat_id, "msg:update", payload)
    await bus.emit(chat_id, event, {"boost": out, "items": out["items"], "boost_id": boost_id})
    return out


async def _set_parent_running(chat_id: str, running: bool) -> None:
    async with session_scope() as s:
        chat = await s.get(m.Chat, chat_id)
        chat.is_running = running
        meta = {"status": chat.runtime_status, "is_running": running, "preview_port": 5173}
    await bus.emit(chat_id, "chat:update", {"chat": {"chat_id": chat_id, "metadata": meta}})
    if not running:
        with contextlib.suppress(Exception):
            from oa.domain.queue import pop_next

            await pop_next(chat_id)


# ---------------------------------------------------------------- start
async def start(chat: m.Chat, user: m.User, content: list[dict[str, Any]], metadata: dict[str, Any],
                models: list[str]) -> tuple[m.Message, m.Boost]:
    from oa.domain.messages import add_message, delta_text, next_round

    models = [str(x).strip() for x in models if str(x).strip()][:MAX_K]
    if len(models) < MIN_K:
        raise BoostError(f"Boost needs {MIN_K}–{MAX_K} models")
    runner = hooks.runner()
    mgr = hooks.manager()
    if runner is None or mgr is None:
        raise BoostError("Agent runtime unavailable")
    async with session_scope() as s:
        parent = await s.get(m.Chat, chat.id)
        if parent.is_running:
            raise BoostError("The project is busy; wait for the current run or stop it first")
        active = (await s.execute(select(m.Boost.id).where(m.Boost.chat_id == chat.id,
                                                           m.Boost.status.in_(("running", "ready"))))).first()
        if active:
            raise BoostError("Finish the current boost (pick a candidate or discard it) first")
        parent.is_running = True
    try:
        await _reserve_sandboxes(chat, len(models), mgr)
    except BoostError:
        async with session_scope() as s:
            (await s.get(m.Chat, chat.id)).is_running = False
        raise
    await mgr.init_workspace(chat.id)

    agent_mode = metadata.get("agent_mode") or (chat.config or {}).get("agent_mode") or "lite"
    async with session_scope() as s:
        parent = await s.get(m.Chat, chat.id)
        round_id = await next_round(s, chat.id)
        meta = {**metadata, "use_boost": True, "boost_models": models}
        msg = await add_message(s, chat.id, role="User", name=user.name, type="message", content=content,
                                round_id=round_id, attempt=0, user_id=user.id, metadata=meta)
        b = m.Boost(chat_id=chat.id, user_id=user.id, message_id=msg.id, status="running", items=[])
        s.add(b)
        await s.flush()
        children: list[tuple[int, str, str]] = []
        for k, model in enumerate(models):
            child = m.Chat(
                workspace_id=parent.workspace_id, user_id=user.id, title=f"{parent.title} · #{k + 1} {model}"[:200],
                access_mode=parent.access_mode, parent_chat_id=parent.id,
                config={**(parent.config or {}), "agent_mode": agent_mode,
                        "bon": {"parent_chat_id": parent.id, "boost_id": b.id, "index": k}},
                llm={"default_model": model},
            )
            s.add(child)
            await s.flush()
            children.append((k, model, child.id))
        b.items = [{"index": k, "model": model, "chat_id": cid, "status": "queued", "summary": "",
                    "preview_url": settings.preview_url(cid), "version": None, "version_link": None,
                    "build_ok": None, "checkui_ok": None} for k, model, cid in children]
        card = await add_message(s, chat.id, role="System", name="System", type="boost", round_id=round_id,
                                 attempt=0, emit=False,
                                 content={"boost_id": b.id, "status": "running", "items": b.items,
                                          "recommended": None, "selected": None, "judge_reason": ""})
        card.is_finished = False
        b.card_message_id = card.id
        boost_id = b.id
        msg_out, card_out = ser.message(msg), ser.message(card)
    await bus.emit(chat.id, "msg:create", {"message": card_out})
    await _set_parent_running(chat.id, True)

    text = delta_text(content)
    for k, model, cid in children:
        try:
            await mgr.init_workspace(cid, from_chat=chat.id)
            async with session_scope() as s:
                cm = m.Message(chat_id=cid, role="User", name=user.name, type="message", content=content,
                               round_id=1, attempt=0, user_id=user.id, metadata_={
                                   **metadata, "agent_mode": agent_mode, "default_model": model,
                                   "boost_variant": {"index": k, "total": len(models), "boost_id": boost_id}})
                s.add(cm)
                await s.flush()
                cmid = cm.id
            await runner.start_round(cid, cmid)
            await _patch_item(boost_id, k, status="running")
        except Exception as e:
            log.exception("boost %s candidate %s failed to start", boost_id, k)
            await _patch_item(boost_id, k, status="failed", summary=f"Failed to start: {e}")
    log.info("boost %s started for %s (%d candidates): %s", boost_id, chat.id, len(models), text[:80])
    _watch(boost_id)
    async with session_scope() as s:
        return await s.get(m.Message, msg_out["id"]), await s.get(m.Boost, boost_id)


async def _reserve_sandboxes(chat: m.Chat, k: int, mgr: Any) -> None:
    """Each candidate needs its own sandbox: check the plan's concurrent-sandbox limit up front and free the
    parent's (idle — it is busy for the whole boost) sandbox when that makes room."""
    from oa.domain.quotas import max_sandboxes

    limit = await max_sandboxes(chat.workspace_id)
    async with session_scope() as s:
        rows = (await s.execute(
            select(m.Sandbox.chat_id).join(m.Chat, m.Chat.id == m.Sandbox.chat_id)
            .where(m.Chat.workspace_id == chat.workspace_id, m.Sandbox.status.in_(("running", "activating")))
        )).all()
    busy = {cid for (cid,) in rows}
    others = len(busy - {chat.id})
    if k > limit - others:
        raise BoostError(
            f"Your plan runs at most {limit} sandboxes at once ({others} in use by other projects), "
            f"so a boost can have at most {max(0, limit - others)} candidates. Stop other projects or upgrade.")
    if chat.id in busy and k > limit - others - 1:
        with contextlib.suppress(Exception):
            await mgr.stop(chat.id)


async def _patch_item(boost_id: int, index: int, **fields: Any) -> None:
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        items = [dict(x) for x in b.items or []]
        for it in items:
            if it["index"] == index:
                it.update(fields)
        b.items = items
    await _publish(boost_id)


# ---------------------------------------------------------------- watching
def _watch(boost_id: int) -> None:
    t = _watchers.get(boost_id)
    if t is None or t.done():
        _watchers[boost_id] = asyncio.create_task(_watch_loop(boost_id), name=f"boost:{boost_id}")


async def _candidate_state(s, it: dict[str, Any]) -> dict[str, Any]:
    cid = it["chat_id"]
    run = (await s.execute(select(m.Run).where(m.Run.chat_id == cid).order_by(m.Run.id.desc()).limit(1))
           ).scalar_one_or_none()
    status = it["status"]
    if run is not None:
        status = {"queued": "running", "running": "running", "suspended": "running"}.get(run.status, run.status)
    ver = (await s.execute(select(m.Version).where(m.Version.chat_id == cid).order_by(m.Version.number.desc())
                           .limit(1))).scalar_one_or_none()
    msg = (await s.execute(select(m.Message).where(m.Message.chat_id == cid, m.Message.type == "message",
                                                   m.Message.role != "User").order_by(m.Message.id.desc()).limit(1))
           ).scalar_one_or_none()
    ads = (await s.execute(select(m.ActionData.command_name, m.ActionData.status, m.ActionData.extra)
                           .where(m.ActionData.chat_id == cid, m.ActionData.command_name.in_(("terminal_run",
                                                                                              "checkui_run")))
                           .order_by(m.ActionData.id))).all()
    builds = [st for cmd, st, extra in ads if cmd == "terminal_run" and "build" in json.dumps(extra or {})]
    checks = [st for cmd, st, _ in ads if cmd == "checkui_run"]
    steps = (await s.execute(select(func.count()).select_from(m.ActionData).where(m.ActionData.chat_id == cid))
             ).scalar_one()
    from oa.domain.messages import delta_text

    # the runner creates the version before marking the run successful; wait for an in-flight build to settle
    if status == "success" and ver is not None and ver.status == "building":
        status = "running"
    return {
        "status": status,
        "summary": delta_text(msg.content)[:400] if msg else it.get("summary", ""),
        "version": ver.version if ver else None,
        "version_link": ver.link if ver and ver.status == "ready" else None,
        "build_ok": (builds[-1] == "success") if builds else None,
        "checkui_ok": (checks[-1] == "success") if checks else None,
        "steps": int(steps),
        "error": run.error if run is not None and run.status == "failed" else None,
    }


async def _watch_loop(boost_id: int) -> None:
    try:
        while True:
            async with session_scope() as s:
                b = await s.get(m.Boost, boost_id)
                if b is None or b.status != "running":
                    return
                items = [dict(x) for x in b.items or []]
                changed = False
                for it in items:
                    if it["status"] in FINAL:
                        continue
                    st = await _candidate_state(s, it)
                    if any(it.get(k) != v for k, v in st.items()):
                        it.update(st)
                        changed = True
                if changed:
                    b.items = items
                done = all(it["status"] in FINAL for it in items)
            if changed:
                await _publish(boost_id)
            if done:
                await _finish(boost_id)
                return
            await asyncio.sleep(POLL)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("boost watcher %s crashed", boost_id)
    finally:
        _watchers.pop(boost_id, None)


def score(it: dict[str, Any]) -> int:
    return (4 if it["status"] == "success" else 0) + (2 if it.get("version_link") else 0) + \
           (1 if it.get("build_ok") else 0) + (1 if it.get("checkui_ok") else 0)


async def judge(items: list[dict[str, Any]], prompt: str) -> tuple[int | None, str]:
    """Recommend a candidate. Real basic model: LLM judgement over the summaries; oa-mock: deterministic score."""
    viable = [it for it in items if it["status"] == "success" and it.get("build_ok") is not False
              and (it.get("steps") or 0) > 0]
    if not viable:
        return None, "No candidate finished successfully."
    best = max(viable, key=lambda it: (score(it), -it["index"]))
    reason = (f"Candidate #{best['index'] + 1} ({best['model']}) built and passed the preview check"
              if best.get("checkui_ok") else f"Candidate #{best['index'] + 1} ({best['model']}) finished successfully")
    if not settings.basic_model.startswith("oa-mock") and len(viable) > 1:
        try:
            from oa.llm import complete

            listing = "\n".join(f"#{it['index'] + 1} model={it['model']} build_ok={it.get('build_ok')} "
                                f"check_ok={it.get('checkui_ok')}\n{it.get('summary', '')[:600]}" for it in viable)
            res = await complete([{"role": "user", "content":
                                   f"User request: {prompt}\n\nCandidates:\n{listing}\n\nWhich candidate best fulfils "
                                   'the request? Answer as JSON {"index": <number>, "reason": "<one sentence>"}.'}])
            mm = re.search(r"\{.*\}", res.text, re.S)
            if mm:
                d = json.loads(mm.group(0))
                idx = int(d["index"]) - 1
                if any(it["index"] == idx for it in viable):
                    return idx, str(d.get("reason") or reason)
        except Exception as e:
            log.warning("boost judge failed, using heuristic: %s", e)
    return best["index"], reason + "."


async def _finish(boost_id: int) -> None:
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        if b.status != "running":
            return
        um = await s.get(m.Message, b.message_id) if b.message_id else None
        from oa.domain.messages import delta_text

        prompt = delta_text(um.content) if um else ""
        items = list(b.items or [])
    rec, reason = await judge(items, prompt)
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        b.recommended, b.judge_reason = rec, reason
        b.status = "ready" if rec is not None else "failed"
        chat_id = b.chat_id
    await _publish(boost_id, "boost:all_end")
    await _set_parent_running(chat_id, False)


# ---------------------------------------------------------------- select / abort / discard
def _files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if rel.parts and (rel.parts[0] in SKIP or rel.parts[0] == ".atoms" or any(x in SKIP for x in rel.parts)):
            continue
        if p.is_file() or p.is_symlink():
            out[rel.as_posix()] = p
    return out


def sync_tree(src: Path, dst: Path) -> dict[str, int]:
    """Make dst's tracked files equal src's (ignoring .git, node_modules, build output and .atoms)."""
    a, b = _files(src), _files(dst)
    added = changed = removed = 0
    for rel, sp in a.items():
        dp = dst / rel
        if rel in b and dp.read_bytes() == sp.read_bytes():
            continue
        dp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sp, dp)
        added += rel not in b
        changed += rel in b
    for rel, dp in b.items():
        if rel not in a:
            dp.unlink()
            removed += 1
    return {"added": added, "changed": changed, "removed": removed}


async def select_candidate(chat: m.Chat, user: m.User, index: int) -> dict[str, Any]:
    from oa.domain.messages import add_message

    async with session_scope() as s:
        b = (await s.execute(select(m.Boost).where(m.Boost.chat_id == chat.id).order_by(m.Boost.id.desc()).limit(1))
             ).scalar_one_or_none()
        if b is None:
            raise BoostError("No boost for this project")
        if b.status not in ("ready", "running"):
            raise BoostError(f"Boost is {b.status}")
        it = next((x for x in b.items or [] if x["index"] == index), None)
        if it is None:
            raise BoostError("Unknown candidate")
        if it["status"] != "success" or it.get("build_ok") is False:
            raise BoostError("This candidate has not finished successfully")
        was_running = b.status == "running"
        b.status, b.selected = "merging", index
        boost_id, child_id, model = b.id, it["chat_id"], it["model"]
        um = await s.get(m.Message, b.message_id) if b.message_id else None
        round_id = um.round_id if um else 0
    if was_running:  # picked early: stop the others
        await _abort_children(boost_id, keep=index)
    await _publish(boost_id)

    ws, src = settings.workspace_dir(chat.id), settings.workspace_dir(child_id)
    stats = await asyncio.to_thread(sync_tree, src, ws)
    mgr = hooks.manager()
    if mgr is not None:
        with contextlib.suppress(Exception):
            client = await mgr.get_client(chat.id)
            if client is not None and stats["added"] + stats["changed"] and (src / "package.json").exists():
                await client.run("pnpm install --prefer-offline 2>&1 | tail -n 5", timeout_ms=300_000)
    summary = f"Boost: merged candidate #{index + 1} ({model})"
    async with session_scope() as s:
        await add_message(s, chat.id, role="System", name="System", type="message", round_id=round_id, attempt=0,
                          content=[{"insert": f"{summary} — {stats['added']} added, {stats['changed']} changed, "
                                              f"{stats['removed']} removed files."}])
    version = None
    try:
        from oa.domain.versions import create_version

        v = await create_version(chat.id, summary)
        version = v.version
    except Exception:
        log.exception("boost merge version failed for %s", chat.id)
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        b.status = "merged"
    out = await _publish(boost_id, "boost:merge_end")
    await bus.emit(chat.id, "boost:merge_end", {"boost_id": boost_id, "selected": index, "version": version,
                                                 "stats": stats})
    await _stop_children(boost_id)
    if was_running:
        await _set_parent_running(chat.id, False)
    return {**out, "version": version, "stats": stats}


async def _abort_children(boost_id: int, keep: int | None = None) -> None:
    runner = hooks.runner()
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        targets = [x["chat_id"] for x in b.items or [] if x["index"] != keep and x["status"] not in FINAL]
    for cid in targets:
        if runner is not None:
            with contextlib.suppress(Exception):
                await runner.cancel(cid)
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        b.items = [{**x, "status": "cancelled"} if x["chat_id"] in targets else x for x in b.items or []]


async def _stop_children(boost_id: int) -> None:
    mgr = hooks.manager()
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        ids = [x["chat_id"] for x in b.items or []]
    if mgr is None:
        return
    for cid in ids:
        with contextlib.suppress(Exception):
            await mgr.stop(cid)


async def abort(chat: m.Chat, running_only: bool = False) -> dict[str, Any] | None:
    states = ("running",) if running_only else ("running", "ready")
    async with session_scope() as s:
        b = (await s.execute(select(m.Boost).where(m.Boost.chat_id == chat.id, m.Boost.status.in_(states))
                             .order_by(m.Boost.id.desc()).limit(1))).scalar_one_or_none()
        if b is None:
            return None
        was_running, boost_id = b.status == "running", b.id
    t = _watchers.pop(boost_id, None)
    if t is not None:
        t.cancel()
    await _abort_children(boost_id)
    async with session_scope() as s:
        b = await s.get(m.Boost, boost_id)
        b.status = "aborted"
    out = await _publish(boost_id, "boost:all_end")
    await _stop_children(boost_id)
    if was_running:
        await _set_parent_running(chat.id, False)
    return out


async def discard(chat: m.Chat) -> dict[str, Any] | None:
    """Abort (if active) and hide the latest boost's candidate projects."""
    from datetime import UTC, datetime

    out = await abort(chat)
    async with session_scope() as s:
        b = (await s.execute(select(m.Boost).where(m.Boost.chat_id == chat.id).order_by(m.Boost.id.desc()).limit(1))
             ).scalar_one_or_none()
        if b is None:
            return out
        for it in b.items or []:
            c = await s.get(m.Chat, it["chat_id"])
            if c is not None and c.deleted_at is None:
                c.deleted_at = datetime.now(UTC)
        return out or serialize(b)


async def is_building(chat_id: str) -> bool:
    """True while this chat's candidates are building: the parent's own sandbox must stay down so it does not take
    a plan slot from a candidate (the workbench would otherwise wake it on page load)."""
    async with session_scope() as s:
        row = (await s.execute(select(m.Boost.id).where(m.Boost.chat_id == chat_id, m.Boost.status == "running")
                               .limit(1))).first()
    return row is not None


async def latest(chat_id: str) -> dict[str, Any] | None:
    async with session_scope() as s:
        b = (await s.execute(select(m.Boost).where(m.Boost.chat_id == chat_id).order_by(m.Boost.id.desc()).limit(1))
             ).scalar_one_or_none()
        return serialize(b) if b else None


async def recover() -> int:
    """Resume watchers for boosts that were running when the process stopped."""
    async with session_scope() as s:
        ids = [x for (x,) in (await s.execute(select(m.Boost.id).where(m.Boost.status == "running"))).all()]
    for i in ids:
        _watch(i)
    return len(ids)
