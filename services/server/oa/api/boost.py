"""Boost / Best-of-N endpoints (Atoms names: /boost, /boost_abort). Starting a boost goes through
POST /chats/:id/messages with `use_boost: true, boost_models: [...]`."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oa.core.deps import chat_guard, current_user
from oa.core.envelope import bad_request, ok
from oa.db import models as m
from oa.domain import boost

router = APIRouter(tags=["boost"])


class SelectBody(BaseModel):
    index: int


@router.get("/chats/{chat_id}/boost")
async def get_boost(chat: m.Chat = Depends(chat_guard("read"))):
    return ok(await boost.latest(chat.id))


@router.post("/chats/{chat_id}/boost/select")
async def select(body: SelectBody, chat: m.Chat = Depends(chat_guard("write")), user: m.User = Depends(current_user)):
    try:
        return ok(await boost.select_candidate(chat, user, body.index))
    except boost.BoostError as e:
        raise bad_request(str(e)) from e


@router.post("/chats/{chat_id}/boost_abort")
async def abort(chat: m.Chat = Depends(chat_guard("write"))):
    return ok(await boost.abort(chat))


@router.delete("/chats/{chat_id}/boost")
async def discard(chat: m.Chat = Depends(chat_guard("write"))):
    return ok(await boost.discard(chat))
