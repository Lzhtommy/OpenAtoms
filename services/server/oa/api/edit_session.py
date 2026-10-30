"""Visual edit sessions (Atoms /chats/:id/edit-session[/commit]).

GET    /chats/:id/edit-session          → {edits, count}
POST   /chats/:id/edit-session          {edits:[{loc, add?, remove?, classes?, orig_classes?, text?, selector?, tag?}]}
                                        → stages (merging edits of the same element) → {edits, count}
DELETE /chats/:id/edit-session          → discard staged edits
POST   /chats/:id/edit-session/commit   → rewrite sources, record Editor blocks, create a version
                                        → {version, files:[path], edits}
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oa import visual_edit as ve
from oa.core.deps import chat_guard, current_user
from oa.core.envelope import ApiError, bad_request, conflict, ok
from oa.core.events import bus
from oa.db import models as m
from oa.domain import serializers as ser

router = APIRouter(tags=["visual-edit"])
_locks: dict[str, asyncio.Lock] = {}


class StageBody(BaseModel):
    edits: list[dict[str, Any]]


def _out(edits: list[ve.Edit]) -> dict[str, Any]:
    return {"edits": [e.to_json() for e in edits], "count": len(edits)}


async def _emit(chat_id: str, edits: list[ve.Edit]) -> None:
    await bus.emit(chat_id, "edit_session:update", _out(edits))


@router.get("/chats/{chat_id}/edit-session")
async def get_session_(chat: m.Chat = Depends(chat_guard("read"))):
    return ok(_out(ve.load(chat.id)))


@router.post("/chats/{chat_id}/edit-session")
async def stage(body: StageBody, chat: m.Chat = Depends(chat_guard("write"))):
    if not body.edits:
        raise bad_request("no edits")
    try:
        incoming = [ve.normalize(e) for e in body.edits]
    except ve.EditError as e:
        raise bad_request(str(e)) from e
    async with _locks.setdefault(chat.id, asyncio.Lock()):
        edits = ve.merge(ve.load(chat.id), incoming)
        ve.save(chat.id, edits)
    await _emit(chat.id, edits)
    return ok(_out(edits))


@router.delete("/chats/{chat_id}/edit-session")
async def discard(chat: m.Chat = Depends(chat_guard("write"))):
    ve.clear(chat.id)
    await _emit(chat.id, [])
    return ok(_out([]))


@router.post("/chats/{chat_id}/edit-session/commit")
async def commit(chat: m.Chat = Depends(chat_guard("write")), user: m.User = Depends(current_user)):
    if chat.is_running:
        raise conflict("The agents are working on this project; wait for the run to finish before saving visual edits")
    async with _locks.setdefault(chat.id, asyncio.Lock()):
        edits = ve.load(chat.id)
        if not edits:
            raise bad_request("nothing to commit")
        try:
            plan = await ve.plan_commit(chat.id, edits)
        except ve.EditError as e:
            raise bad_request(str(e)) from e
        if plan.errors:
            raise ApiError(4400, "Some edits could not be applied", {"errors": plan.errors}, status=400)
        await ve.apply_commit(chat.id, user.name or user.email, edits, plan)
        ve.clear(chat.id)
    await _emit(chat.id, [])
    from oa.domain.versions import create_version

    version = await create_version(chat.id, f"Visual edit: {ve.describe(edits)}"[:300])
    return ok({"version": ser.version(version), "files": sorted(plan.files), "edits": [e.to_json() for e in edits]})
