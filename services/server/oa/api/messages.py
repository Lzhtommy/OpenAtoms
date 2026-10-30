"""Messages, round state, stop, plan approval, questions, action-datas, block timelines, events replay, prompt queue."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.core.deps import chat_guard, current_user
from oa.core.envelope import bad_request, conflict, not_found, ok
from oa.core.events import bus
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import hooks, queue
from oa.domain import serializers as ser
from oa.domain.messages import (
    emit_content,
    get_round_state,
    history,
    new_attempt,
    normalize_content,
    post_user_message,
)

router = APIRouter(tags=["messages"])


class SendBody(BaseModel):
    content: Any
    type: str | None = "message"
    refer_id: int | None = None
    metadata: dict[str, Any] | None = None
    use_boost: bool = False  # Best-of-N: run boost_models in parallel candidate projects
    boost_models: list[str] | None = None


@router.get("/chats/{chat_id}/messages")
async def get_messages(chat: m.Chat = Depends(chat_guard("read", auth=False)), session: AsyncSession = Depends(get_session)):
    return ok(await history(session, chat.id))


@router.post("/chats/{chat_id}/messages")
async def send_message(body: SendBody, chat: m.Chat = Depends(chat_guard("write")), user: m.User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    if not normalize_content(body.content):
        raise bad_request("content is empty")
    if body.use_boost:
        from oa.domain import boost

        try:
            msg, b = await boost.start(chat, user, normalize_content(body.content), dict(body.metadata or {}),
                                       body.boost_models or [])
        except boost.BoostError as e:
            raise bad_request(str(e)) from e
        return ok({"message": ser.message(msg), "queued": False, "queue_item": None, "boost": boost.serialize(b)})
    msg, queued, item = await post_user_message(session, chat, user, body.model_dump())
    return ok({"message": ser.message(msg) if msg else None, "queued": queued,
               "queue_item": ser.queue_item(item) if item else None})


async def _user_message(session: AsyncSession, chat: m.Chat, mid: int) -> m.Message:
    msg = await session.get(m.Message, mid)
    if msg is None or msg.chat_id != chat.id:
        raise not_found("Message not found")
    if msg.role != "User":
        raise bad_request("Only user messages can be edited or retried")
    return msg


class EditBody(BaseModel):
    content: Any


@router.post("/chats/{chat_id}/messages/{mid}/edit")
async def edit_message(mid: int, body: EditBody, chat: m.Chat = Depends(chat_guard("write")),
                       user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    src = await _user_message(session, chat, mid)
    content = normalize_content(body.content)
    if not content:
        raise bad_request("content is empty")
    msg = await new_attempt(session, chat, user, src, content)
    return ok({"message": ser.message(msg)})


@router.post("/chats/{chat_id}/messages/{mid}/retry")
async def retry_message(mid: int, chat: m.Chat = Depends(chat_guard("write")), user: m.User = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    src = await _user_message(session, chat, mid)
    msg = await new_attempt(session, chat, user, src, None)
    return ok({"message": ser.message(msg)})


@router.get("/chats/{chat_id}/messages/{mid}/usage")
async def message_usage(mid: int, chat: m.Chat = Depends(chat_guard("read")), session: AsyncSession = Depends(get_session)):
    msg = await session.get(m.Message, mid)
    if msg is None or msg.chat_id != chat.id:
        raise not_found("Message not found")
    runs = (await session.execute(select(m.Run.id).where(m.Run.chat_id == chat.id, m.Run.round_id == msg.round_id,
                                                         m.Run.attempt == msg.attempt))).scalars().all()
    rows = (await session.execute(select(m.UsageEvent).where(m.UsageEvent.run_id.in_(runs or [-1])))).scalars().all()
    return ok({"input_tokens": sum(r.input_tokens for r in rows), "output_tokens": sum(r.output_tokens for r in rows),
               "credits": sum(r.credits for r in rows), "cost_usd": round(sum(r.cost_usd for r in rows), 6)})


@router.post("/chats/{chat_id}/stop")
async def stop(chat: m.Chat = Depends(chat_guard("write"))):
    runner = hooks.runner()
    cancelled = bool(runner and await runner.cancel(chat.id))
    from oa.domain import boost

    if await boost.abort(chat, running_only=True) is not None:  # Stop also ends a running Best-of-N run
        cancelled = True
    return ok({"cancelled": cancelled})


@router.get("/chats/{chat_id}/messages/search")
async def search(keyword: str = Query(min_length=1), chat: m.Chat = Depends(chat_guard("read")),
                 session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(m.Message).where(m.Message.chat_id == chat.id, cast(m.Message.content, String).ilike(f"%{keyword}%"))
        .order_by(m.Message.id.desc()).limit(50)
    )).scalars()
    return ok({"items": [ser.message(x) for x in rows]})


class RoundStateBody(BaseModel):
    active_attempts: dict[str, int]


@router.put("/chats/{chat_id}/round-state")
async def put_round_state(body: RoundStateBody, chat: m.Chat = Depends(chat_guard("write")),
                          session: AsyncSession = Depends(get_session)):
    rs = await get_round_state(session, chat.id)
    rs.active_attempts = {**(rs.active_attempts or {}), **{str(k): int(v) for k, v in body.active_attempts.items()}}
    await session.commit()
    return ok({"active_attempts": rs.active_attempts})


@router.get("/chats/{chat_id}/action-datas/{uuid}")
async def get_action_data(uuid: str, chat: m.Chat = Depends(chat_guard("read", auth=False)),
                          session: AsyncSession = Depends(get_session)):
    ad = (await session.execute(select(m.ActionData).where(m.ActionData.uuid == uuid, m.ActionData.chat_id == chat.id))).scalar_one_or_none()
    if ad is None:
        raise not_found("Action not found")
    return ok(ser.action_data(ad))


@router.get("/chats/{chat_id}/block-timelines")
async def block_timelines(version: str | None = None, chat: m.Chat = Depends(chat_guard("read", auth=False)),
                          session: AsyncSession = Depends(get_session)):
    q = select(m.BlockTimeline).where(m.BlockTimeline.chat_id == chat.id)
    if version:
        q = q.where(m.BlockTimeline.version == version)
    return ok([ser.timeline(t) for t in (await session.execute(q.order_by(m.BlockTimeline.index))).scalars()])


@router.get("/chats/{chat_id}/events")
async def events(after: int = 0, chat: m.Chat = Depends(chat_guard("read", auth=False))):
    evs, complete = await bus.replay(chat.id, after)
    return ok({"events": evs, "complete": complete})


# ------------------------------------------------------------------ plan mode / questions
async def _suspended(chat_id: str, reason: str) -> Any:
    runner = hooks.runner()
    run = await runner.suspended_run(chat_id) if runner else None
    if run is None or run.suspend_reason != reason:
        raise conflict(f"No run is waiting for {reason}")
    return runner, run


async def _set_card_status(session: AsyncSession, message_id: int | None, status: str, **extra: Any) -> None:
    if not message_id:
        return
    card = await session.get(m.Message, message_id)
    if card is None:
        return
    card.content = {**(card.content or {}), "status": status, **extra}
    await session.commit()
    await emit_content(card)


@router.post("/chats/{chat_id}/approve-plan")
async def approve_plan(chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    runner, run = await _suspended(chat.id, "plan_ready")
    await _set_card_status(session, run.suspend_message_id, "approved")
    await runner.resume(chat.id, "approve", run.suspend_message_id)
    return ok(None)


class DeclineBody(BaseModel):
    reason: str | None = None


@router.post("/chats/{chat_id}/decline-plan")
async def decline_plan(body: DeclineBody, chat: m.Chat = Depends(chat_guard("write")),
                       session: AsyncSession = Depends(get_session)):
    runner, run = await _suspended(chat.id, "plan_ready")
    await _set_card_status(session, run.suspend_message_id, "declined")
    await runner.resume(chat.id, "decline", run.suspend_message_id, {"reason": body.reason or ""})
    return ok(None)


class QuestionsBody(BaseModel):
    message_id: int
    answers: dict[str, Any]


@router.post("/chats/{chat_id}/submit-questions")
async def submit_questions(body: QuestionsBody, chat: m.Chat = Depends(chat_guard("write")),
                           session: AsyncSession = Depends(get_session)):
    card = await session.get(m.Message, body.message_id)
    if card is None or card.chat_id != chat.id or card.type != "questions_card":
        raise not_found("Questions card not found")
    await _set_card_status(session, card.id, "finished", answers=body.answers)
    runner = hooks.runner()
    run = await runner.suspended_run(chat.id) if runner else None
    if run is not None:
        await runner.resume(chat.id, "questions", card.id, {"answers": body.answers})
    return ok(None)


# ------------------------------------------------------------------ prompt queue
class QueueBody(BaseModel):
    content: Any
    metadata: dict[str, Any] | None = None


async def _item(session: AsyncSession, chat: m.Chat, item_id: int) -> m.PromptQueueItem:
    it = await session.get(m.PromptQueueItem, item_id)
    if it is None or it.chat_id != chat.id or it.status != "queued":
        raise not_found("Queue item not found")
    return it


@router.get("/chats/{chat_id}/prompt-queue")
async def get_queue(chat: m.Chat = Depends(chat_guard("read")), session: AsyncSession = Depends(get_session)):
    return ok(await queue.snapshot(session, chat.id))


@router.post("/chats/{chat_id}/prompt-queue")
async def add_queue(body: QueueBody, chat: m.Chat = Depends(chat_guard("write")), user: m.User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    content = normalize_content(body.content)
    if not content:
        raise bad_request("content is empty")
    item = await queue.enqueue(session, chat.id, content, body.metadata, user.id)
    if not chat.is_running and not await queue.is_paused(session, chat.id):
        await queue.pop_next(chat.id)
    return ok(ser.queue_item(item))


class QueuePatch(BaseModel):
    content: Any


@router.patch("/chats/{chat_id}/prompt-queue/{item_id}")
async def patch_queue(item_id: int, body: QueuePatch, chat: m.Chat = Depends(chat_guard("write")),
                      session: AsyncSession = Depends(get_session)):
    it = await _item(session, chat, item_id)
    it.content = normalize_content(body.content)
    await session.commit()
    await queue.broadcast(session, chat.id)
    return ok(ser.queue_item(it))


@router.delete("/chats/{chat_id}/prompt-queue/{item_id}")
async def delete_queue(item_id: int, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    it = await _item(session, chat, item_id)
    await session.delete(it)
    await session.commit()
    await queue.broadcast(session, chat.id)
    return ok(None)


class ReorderBody(BaseModel):
    ids: list[int]


@router.post("/chats/{chat_id}/prompt-queue/reorder")
async def reorder_queue(body: ReorderBody, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    await queue.reorder(session, chat.id, body.ids)
    return ok(await queue.snapshot(session, chat.id))


class PauseBody(BaseModel):
    paused: bool


@router.post("/chats/{chat_id}/prompt-queue/pause")
async def pause_queue(body: PauseBody, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    await queue.set_paused(session, chat.id, body.paused)
    await queue.broadcast(session, chat.id)
    if not body.paused and not chat.is_running:
        await queue.pop_next(chat.id)
    return ok(await queue.snapshot(session, chat.id))


@router.post("/chats/{chat_id}/prompt-queue/{item_id}/send")
async def send_queue_item(item_id: int, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    it = await _item(session, chat, item_id)
    if chat.is_running:
        await queue.move_to_front(session, chat.id, it.id)
        return ok({"sent": False})
    msg = await queue.send_item(session, it)
    return ok({"sent": msg is not None, "message": ser.message(msg) if msg else None})
