"""Per-chat prompt queue: input received while a run is active waits here (ARCHITECTURE §1.5)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.core.events import bus
from oa.db import models as m
from oa.db.session import SessionLocal
from oa.domain import serializers as ser

log = logging.getLogger("oa.queue")


async def items(session: AsyncSession, chat_id: str) -> list[m.PromptQueueItem]:
    return list(
        (
            await session.execute(
                select(m.PromptQueueItem)
                .where(m.PromptQueueItem.chat_id == chat_id, m.PromptQueueItem.status == "queued")
                .order_by(m.PromptQueueItem.position, m.PromptQueueItem.id)
            )
        )
        .scalars()
        .all()
    )


async def is_paused(session: AsyncSession, chat_id: str) -> bool:
    st = await session.get(m.QueueState, chat_id)
    return bool(st and st.paused)


async def set_paused(session: AsyncSession, chat_id: str, paused: bool) -> None:
    st = await session.get(m.QueueState, chat_id)
    if st is None:
        session.add(m.QueueState(chat_id=chat_id, paused=paused))
    else:
        st.paused = paused
    await session.commit()


async def snapshot(session: AsyncSession, chat_id: str) -> dict[str, Any]:
    return {"paused": await is_paused(session, chat_id), "items": [ser.queue_item(q) for q in await items(session, chat_id)]}


async def broadcast(session: AsyncSession, chat_id: str) -> None:
    await bus.emit(chat_id, "prompt_queue:update", await snapshot(session, chat_id))


async def enqueue(
    session: AsyncSession, chat_id: str, content: Any, metadata: dict[str, Any] | None, user_id: int | None
) -> m.PromptQueueItem:
    pos = (
        await session.execute(
            select(func.max(m.PromptQueueItem.position)).where(
                m.PromptQueueItem.chat_id == chat_id, m.PromptQueueItem.status == "queued"
            )
        )
    ).scalar()
    item = m.PromptQueueItem(
        chat_id=chat_id, position=int(pos or 0) + 1, content=content, metadata_=metadata or {}, user_id=user_id
    )
    session.add(item)
    await session.commit()
    await broadcast(session, chat_id)
    return item


async def reorder(session: AsyncSession, chat_id: str, ids: list[int]) -> None:
    current = {q.id: q for q in await items(session, chat_id)}
    order = [i for i in ids if i in current] + [i for i in current if i not in ids]
    for pos, qid in enumerate(order, start=1):
        current[qid].position = pos
    await session.commit()
    await broadcast(session, chat_id)


async def move_to_front(session: AsyncSession, chat_id: str, item_id: int) -> None:
    ids = [q.id for q in await items(session, chat_id)]
    if item_id in ids:
        ids.remove(item_id)
        await reorder(session, chat_id, [item_id, *ids])


async def send_item(session: AsyncSession, item: m.PromptQueueItem) -> m.Message | None:
    """Turn a queued item into a real user message and start its round."""
    from oa.domain.messages import post_user_message

    chat = await session.get(m.Chat, item.chat_id)
    user = await session.get(m.User, item.user_id) if item.user_id else None
    if chat is None or user is None or chat.deleted_at is not None:
        item.status = "sent"
        await session.commit()
        return None
    item.status = "sent"
    await session.commit()
    msg, queued, _ = await post_user_message(
        session, chat, user, {"content": item.content, "metadata": item.metadata_ or {}}
    )
    if msg is not None:
        await bus.emit(chat.id, "prompt_queue:item_sent", {"item_id": item.id, "message_id": msg.id})
    await broadcast(session, chat.id)
    return msg


async def pop_next(chat_id: str) -> None:
    """Called by the runner when a run ends: send the next queued prompt unless paused."""
    async with SessionLocal() as s:
        chat = await s.get(m.Chat, chat_id)
        if chat is None or chat.is_running or await is_paused(s, chat_id):
            return
        pending = await items(s, chat_id)
        if not pending:
            return
        try:
            await send_item(s, pending[0])
        except Exception:
            log.exception("pop_next failed for %s", chat_id)
