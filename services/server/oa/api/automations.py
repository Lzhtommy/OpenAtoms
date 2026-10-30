"""Automations (定时任务) REST: CRUD, presets, run-now, history."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.core.deps import WorkspaceCtx, current_user, current_workspace, load_chat
from oa.core.envelope import bad_request, not_found, ok
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import automations as auto
from oa.domain import serializers as ser

router = APIRouter(tags=["automations"])


class AutomationBody(BaseModel):
    chat_id: str
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20_000)
    cron: str = Field(min_length=1, max_length=100)
    timezone: str = "UTC"
    enabled: bool = True


class AutomationPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    prompt: str | None = Field(default=None, max_length=20_000)
    cron: str | None = None
    timezone: str | None = None
    enabled: bool | None = None


def _schedule(cron: str, tz: str) -> datetime:
    try:
        return auto.next_fire(cron, tz)
    except auto.CronError as e:
        raise bad_request(str(e)) from e


async def _get(session: AsyncSession, aid: int, user: m.User) -> m.Automation:
    a = await session.get(m.Automation, aid)
    if a is None:
        raise not_found("Automation not found")
    await load_chat(session, a.chat_id, user, "write")
    return a


async def _titles(session: AsyncSession, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = await session.execute(select(m.Chat.id, m.Chat.title).where(m.Chat.id.in_(ids)))
    return dict(rows.all())


@router.get("/automations/presets")
async def presets():
    return ok(auto.PRESETS)


@router.get("/automations")
async def list_automations(chat_id: str | None = Query(None), ws: WorkspaceCtx = Depends(current_workspace),
                           user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    q = select(m.Automation).where(m.Automation.workspace_id == ws.workspace.id).order_by(m.Automation.id.desc())
    if chat_id:
        await load_chat(session, chat_id, user, "read")
        q = q.where(m.Automation.chat_id == chat_id)
    rows = list((await session.execute(q)).scalars())
    titles = await _titles(session, {a.chat_id for a in rows})
    return ok([auto.serialize(a, titles.get(a.chat_id)) for a in rows])


@router.post("/automations")
async def create_automation(body: AutomationBody, user: m.User = Depends(current_user),
                            session: AsyncSession = Depends(get_session)):
    chat = await load_chat(session, body.chat_id, user, "write")
    nxt = _schedule(body.cron, body.timezone)
    a = m.Automation(chat_id=chat.id, workspace_id=chat.workspace_id, user_id=user.id, name=body.name.strip(),
                     prompt=body.prompt.strip(), cron=body.cron.strip(), timezone=body.timezone,
                     enabled=body.enabled, next_run_at=nxt if body.enabled else None)
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return ok(auto.serialize(a, chat.title))


@router.patch("/automations/{aid}")
async def update_automation(aid: int, body: AutomationPatch, user: m.User = Depends(current_user),
                            session: AsyncSession = Depends(get_session)):
    a = await _get(session, aid, user)
    for f in ("name", "prompt", "cron", "timezone", "enabled"):
        v = getattr(body, f)
        if v is not None:
            setattr(a, f, v.strip() if isinstance(v, str) else v)
    a.next_run_at = _schedule(a.cron, a.timezone) if a.enabled else None
    await session.commit()
    await session.refresh(a)
    return ok(auto.serialize(a, (await _titles(session, {a.chat_id})).get(a.chat_id)))


@router.delete("/automations/{aid}")
async def delete_automation(aid: int, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    a = await _get(session, aid, user)
    await session.delete(a)
    await session.commit()
    return ok({"deleted": aid})


@router.post("/automations/{aid}/run")
async def run_automation(aid: int, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await _get(session, aid, user)
    await session.commit()
    return ok(await auto.fire(aid, manual=True))


@router.get("/automations/{aid}/history")
async def automation_history(aid: int, user: m.User = Depends(current_user),
                             session: AsyncSession = Depends(get_session)):
    a = await _get(session, aid, user)
    msgs = (await session.execute(
        select(m.Message).where(m.Message.chat_id == a.chat_id, m.Message.role == "User")
        .order_by(m.Message.id.desc()).limit(200))).scalars()
    out: list[dict[str, Any]] = [ser.message(x) for x in msgs
                                 if ((x.metadata_ or {}).get("automation") or {}).get("id") == a.id]
    return ok(out[:50])
