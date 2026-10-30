"""User message intake and branch-aware history (round_id / attempt tree, ARCHITECTURE §4).

Each user message opens a (round_id, attempt) node whose parent is (prev_round_id, prev_attempt): the node that
was at the tip of the active path when it was sent. Edit/retry create a sibling attempt with the same parent.
The active path is resolved by walking rounds in order and picking, among the children of the current tip,
the attempt recorded in round_state.active_attempts (default: the newest).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.core.events import bus
from oa.db import models as m
from oa.domain import hooks
from oa.domain import serializers as ser

log = logging.getLogger("oa.messages")


def normalize_content(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"insert": content}]
    if isinstance(content, dict):
        return [content]
    return list(content)


def delta_text(content: Any) -> str:
    out: list[str] = []
    for op in normalize_content(content) if not isinstance(content, dict) else [content]:
        ins = op.get("insert") if isinstance(op, dict) else None
        if isinstance(ins, str):
            out.append(ins)
        elif isinstance(ins, dict) and "mentiontrigger" in ins:
            out.append("@" + str(ins["mentiontrigger"].get("value", "")))
    return "".join(out).strip()


async def get_round_state(session: AsyncSession, chat_id: str) -> m.RoundState:
    rs = await session.get(m.RoundState, chat_id)
    if rs is None:
        rs = m.RoundState(chat_id=chat_id, active_attempts={})
        session.add(rs)
        await session.flush()
    return rs


async def active_path(session: AsyncSession, chat_id: str) -> tuple[list[tuple[int, int]], dict[int, int]]:
    """Return ([(round_id, attempt)...] along the active branch, {round_id: sibling attempt count})."""
    rs = await session.get(m.RoundState, chat_id)
    active = {int(k): int(v) for k, v in ((rs.active_attempts if rs else {}) or {}).items()}
    rows = (
        await session.execute(
            select(m.Message.round_id, m.Message.attempt, m.Message.prev_round_id, m.Message.prev_attempt)
            .where(m.Message.chat_id == chat_id, m.Message.role == "User")
            .order_by(m.Message.id)
        )
    ).all()
    nodes: dict[int, dict[int, tuple[int | None, int | None]]] = defaultdict(dict)
    for r, a, pr, pa in rows:
        nodes[r].setdefault(a, (pr, pa))
    path: list[tuple[int, int]] = []
    counts: dict[int, int] = {}
    tip: tuple[int | None, int | None] = (None, None)
    for r in sorted(nodes):
        children = [a for a, parent in nodes[r].items() if parent == tip or (tip == (None, None) and parent[0] is None)]
        if not children:
            continue
        a = active.get(r) if active.get(r) in children else max(children)
        path.append((r, a))
        counts[r] = len(children)
        tip = (r, a)
    return path, counts


async def history(session: AsyncSession, chat_id: str) -> dict[str, Any]:
    path, counts = await active_path(session, chat_id)
    on_path = set(path)
    all_user_rounds = set(
        (await session.execute(select(m.Message.round_id).where(m.Message.chat_id == chat_id, m.Message.role == "User")))
        .scalars()
        .all()
    )
    msgs = (
        (await session.execute(select(m.Message).where(m.Message.chat_id == chat_id).order_by(m.Message.id)))
        .scalars()
        .all()
    )
    visible: list[m.Message] = []
    for msg in msgs:
        if (msg.round_id, msg.attempt) in on_path:
            visible.append(msg)
        elif msg.round_id not in all_user_rounds:
            visible.append(msg)  # system messages not tied to a user round (remix banner, ...)
    by_id = {x.id: x for x in visible}
    replies: dict[int, list[dict[str, Any]]] = defaultdict(list)
    top: list[m.Message] = []
    for x in visible:
        if x.type == "emoji" and x.refer_id in by_id:
            replies[x.refer_id].append(ser.message(x))
        else:
            top.append(x)
    out = [ser.message(x, attempts=counts.get(x.round_id, 1), replies=replies.get(x.id)) for x in top]
    uuids = [x.uuid for x in visible]
    ads: list[m.ActionData] = []
    if uuids:
        ads = list(
            (
                await session.execute(
                    select(m.ActionData).where(m.ActionData.chat_id == chat_id, m.ActionData.message_uuid.in_(uuids))
                    .order_by(m.ActionData.id)
                )
            )
            .scalars()
            .all()
        )
    rs = await session.get(m.RoundState, chat_id)
    chat = await session.get(m.Chat, chat_id)
    return {
        "messages": out,
        "action_datas": [ser.action_data(a) for a in ads],
        "round_state": {"active_attempts": (rs.active_attempts if rs else {}) or {}},
        "is_running": bool(chat and chat.is_running),
        "last_seq": await bus.current_seq(chat_id),
    }


async def emit_created(msg: m.Message) -> None:
    await bus.emit(msg.chat_id, "msg:create", {"message": ser.message(msg)})


async def emit_content(msg: m.Message) -> None:
    await bus.emit(
        msg.chat_id,
        "msg:update",
        {"id": msg.id, "content": msg.content, "is_finished": msg.is_finished, "metadata": msg.metadata_ or {}},
    )


async def add_message(
    session: AsyncSession,
    chat_id: str,
    *,
    role: str,
    type: str,
    content: Any,
    name: str = "",
    round_id: int = 0,
    attempt: int = 0,
    user_id: int | None = None,
    refer_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    prev: tuple[int | None, int | None] = (None, None),
    emit: bool = True,
) -> m.Message:
    msg = m.Message(
        chat_id=chat_id,
        role=role,
        name=name,
        type=type,
        content=content,
        round_id=round_id,
        attempt=attempt,
        prev_round_id=prev[0],
        prev_attempt=prev[1],
        user_id=user_id,
        refer_id=refer_id,
        metadata_=metadata or {},
        is_finished=True,
    )
    session.add(msg)
    await session.flush()
    if emit:
        await session.commit()
        await emit_created(msg)
    return msg


async def next_round(session: AsyncSession, chat_id: str) -> int:
    v = (await session.execute(select(func.max(m.Message.round_id)).where(m.Message.chat_id == chat_id))).scalar()
    return int(v or 0) + 1


async def _finish_task_card(session: AsyncSession, chat_id: str, reply: dict[str, Any]) -> None:
    card = await session.get(m.Message, int(reply.get("message_id") or 0))
    if card is None or card.chat_id != chat_id or card.type != "task":
        return
    content = dict(card.content or {})
    content["status"] = "finished"
    if reply.get("option_index") is not None:
        content["selected"] = int(reply["option_index"])
    card.content = content
    await session.flush()
    await emit_content(card)


async def post_user_message(
    session: AsyncSession, chat: m.Chat, user: m.User, body: dict[str, Any]
) -> tuple[m.Message | None, bool, m.PromptQueueItem | None]:
    from oa.domain import queue

    content = normalize_content(body.get("content"))
    metadata = dict(body.get("metadata") or {})
    await _moderate(content)
    runner = hooks.runner()

    suspended = await runner.suspended_run(chat.id) if runner else None
    if suspended is not None:
        if metadata.get("task_reply"):
            await _finish_task_card(session, chat.id, metadata["task_reply"])
        msg = await add_message(
            session, chat.id, role="User", name=user.name, type=body.get("type") or "message", content=content,
            round_id=suspended.round_id, attempt=suspended.attempt, user_id=user.id, refer_id=body.get("refer_id"),
            metadata=metadata, prev=await _round_parent(session, chat.id, suspended.round_id, suspended.attempt),
        )
        await runner.resume(chat.id, "reply", msg.id, {"text": delta_text(content), **metadata})
        return msg, False, None

    if chat.is_running:
        item = await queue.enqueue(session, chat.id, content, metadata, user.id)
        return None, True, item

    path, _ = await active_path(session, chat.id)
    tip = path[-1] if path else (None, None)
    round_id = await next_round(session, chat.id)
    msg = await add_message(
        session, chat.id, role="User", name=user.name, type=body.get("type") or "message", content=content,
        round_id=round_id, attempt=0, user_id=user.id, refer_id=body.get("refer_id"), metadata=metadata, prev=tip,
    )
    await start_or_quota(session, chat, msg)
    return msg, False, None


async def _moderate(content: Any) -> None:
    """Reject prompts that violate the platform content policy (oa.domain.moderation)."""
    from oa.core.envelope import ApiError
    from oa.domain.moderation import BLOCKED_CODE, delta_text as _text, moderate

    verdict = await moderate(_text(content))
    if verdict.flagged:
        raise ApiError(BLOCKED_CODE, "This request violates the OpenAtoms content policy "
                       f"({verdict.category}) and can't be processed.", {"category": verdict.category}, status=400)


async def _round_parent(session: AsyncSession, chat_id: str, round_id: int, attempt: int) -> tuple[int | None, int | None]:
    row = (
        await session.execute(
            select(m.Message.prev_round_id, m.Message.prev_attempt)
            .where(m.Message.chat_id == chat_id, m.Message.round_id == round_id, m.Message.attempt == attempt,
                   m.Message.role == "User")
            .order_by(m.Message.id)
            .limit(1)
        )
    ).first()
    return (row[0], row[1]) if row else (None, None)


async def start_or_quota(session: AsyncSession, chat: m.Chat, msg: m.Message) -> bool:
    """Credit precheck then start the round. Returns True if a run was started."""
    from oa.domain import billing, quotas

    block = await quotas.check_run_allowed(chat.id)
    if block is not None:
        await add_message(
            session, chat.id, role="System", type="quota", round_id=msg.round_id, attempt=msg.attempt,
            content={"message": block.message, "code": block.code, "reason": block.reason, **block.data},
        )
        return False
    if not await billing.has_credits(chat.id):
        await add_message(
            session, chat.id, role="System", type="quota", round_id=msg.round_id, attempt=msg.attempt,
            content={"message": "Your credits are exhausted. Upgrade your plan or buy credits to continue.",
                     "code": 4290},
        )
        return False
    runner = hooks.runner()
    if runner is None:
        await add_message(
            session, chat.id, role="System", type="error", round_id=msg.round_id, attempt=msg.attempt,
            content={"message": "Agent runtime unavailable"},
        )
        return False
    await session.commit()
    await runner.start_round(chat.id, msg.id)
    return True


async def new_attempt(
    session: AsyncSession, chat: m.Chat, user: m.User, source: m.Message, content: list[dict[str, Any]] | None
) -> m.Message:
    """Edit (content given) or retry (content None) a user message: sibling attempt in the same round."""
    runner = hooks.runner()
    if chat.is_running and runner is not None:
        await runner.cancel(chat.id)
    max_attempt = (
        await session.execute(
            select(func.max(m.Message.attempt)).where(m.Message.chat_id == chat.id, m.Message.round_id == source.round_id)
        )
    ).scalar() or 0
    attempt = int(max_attempt) + 1
    msg = await add_message(
        session, chat.id, role="User", name=user.name, type="message",
        content=content if content is not None else source.content,
        round_id=source.round_id, attempt=attempt, user_id=user.id, metadata=dict(source.metadata_ or {}),
        prev=(source.prev_round_id, source.prev_attempt), emit=False,
    )
    rs = await get_round_state(session, chat.id)
    rs.active_attempts = {**(rs.active_attempts or {}), str(source.round_id): attempt}
    await session.commit()
    await emit_created(msg)
    await start_or_quota(session, chat, msg)
    return msg
