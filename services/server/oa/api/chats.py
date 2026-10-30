"""Projects (chats): CRUD, fork/remix, likes, discover, public share replay."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.config import settings
from oa.core.deps import WorkspaceCtx, chat_guard, current_user, current_workspace, load_chat, optional_user
from oa.core.envelope import bad_request, ok
from oa.core.events import bus
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import hooks
from oa.domain import serializers as ser
from oa.domain.messages import add_message, delta_text, history, post_user_message
from oa.domain.secrets import mask, secret_values

log = logging.getLogger("oa.chats")
router = APIRouter(tags=["chats"])

ACCESS_MODES = ("private", "secret", "public")


async def chat_out(session: AsyncSession, chat: m.Chat, user: m.User | None = None) -> dict[str, Any]:
    ver = None
    if chat.current_version:
        ver = (await session.execute(select(m.Version).where(m.Version.chat_id == chat.id,
                                                             m.Version.version == chat.current_version))).scalar_one_or_none()
    dep = await session.get(m.Deployment, chat.id)
    if dep is not None and dep.status == "deleted":
        dep = None
    liked = bool(user and await session.get(m.ChatLike, (chat.id, user.id)))
    return ser.chat(chat, ver, dep, liked)


async def emit_chat_update(chat: m.Chat, fields: dict[str, Any]) -> None:
    await bus.emit(chat.id, "chat:update", {"chat": {"chat_id": chat.id, **fields}})


class SendBody(BaseModel):
    content: Any
    type: str | None = "message"
    refer_id: int | None = None
    metadata: dict[str, Any] | None = None


class RemixBody(BaseModel):
    chat_id: str
    version: str | None = None


class CreateChatBody(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    first_message: SendBody | None = None
    llm: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    remix: RemixBody | None = None
    folder_id: int | None = None


@router.get("/chats")
async def list_chats(
    cur_page: int = Query(1, ge=1), page_num: int = Query(20, ge=1, le=100), is_starred: bool | None = None,
    folder_id: int | None = None, keyword: str | None = None,
    ctx: WorkspaceCtx = Depends(current_workspace), user: m.User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    q = select(m.Chat).where(m.Chat.workspace_id == ctx.workspace.id, m.Chat.deleted_at.is_(None),
                             m.Chat.parent_chat_id.is_(None))  # hide BoN candidate children
    if is_starred is not None:
        q = q.where(m.Chat.is_starred.is_(is_starred))
    if folder_id is not None:
        q = q.where(m.Chat.folder_id == folder_id)
    if keyword:
        q = q.where(m.Chat.title.ilike(f"%{keyword}%"))
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await session.execute(q.order_by(m.Chat.updated_at.desc()).offset((cur_page - 1) * page_num).limit(page_num))).scalars().all()
    items = [await chat_out(session, c, user) for c in rows]
    return ok({"items": items, "total": total, "has_more": cur_page * page_num < total})


def _title_from(body: CreateChatBody) -> str:
    if body.title:
        return body.title
    if body.first_message:
        text = delta_text(body.first_message.content)
        if text:
            return text[:30]
    return "New Project"


@router.post("/chats")
async def create_chat(body: CreateChatBody, ctx: WorkspaceCtx = Depends(current_workspace), user: m.User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    if ctx.role == "viewer":
        raise bad_request("Viewers cannot create projects")
    config = {"agent_mode": "full", "template_name": "react-shadcn-vite", "framework": "react", "plan_mode": False,
              "cloud_enabled": False, **(body.config or {})}
    if config.get("agent_mode") not in ("lite", "full"):
        config["agent_mode"] = "full"
    llm = {"default_model": settings.default_model, **(body.llm or {})}
    source: m.Chat | None = None
    if body.remix:
        source = await load_chat(session, body.remix.chat_id, user, "read")
    chat = m.Chat(workspace_id=ctx.workspace.id, user_id=user.id, title=_title_from(body), config=config, llm=llm,
                  folder_id=body.folder_id)
    if source is not None:
        chat.title = body.title or f"{source.title} (Remix)"
        chat.config = {**(source.config or {}), "github": None, "supabase": None}
        chat.llm = source.llm or llm
        chat.fork_from = {"chat_id": source.id, "version": body.remix.version or source.current_version}
        chat.status = "forking"
    session.add(chat)
    await session.commit()
    if source is not None:
        await _do_fork(session, chat, source, chat.fork_from["version"])
    if body.first_message is not None:
        await post_user_message(session, chat, user, body.first_message.model_dump())
        await session.refresh(chat)
    return ok(await chat_out(session, chat, user))


async def _do_fork(session: AsyncSession, chat: m.Chat, source: m.Chat, version: str | None) -> None:
    mgr = hooks.manager()
    try:
        if mgr is None:
            raise RuntimeError("sandbox manager unavailable")
        await mgr.init_workspace(chat.id, from_chat=source.id, version=version)
        chat.status = "ready"
    except Exception as e:
        log.exception("fork failed")
        chat.status = "failed"
        await session.commit()
        await add_message(session, chat.id, role="System", type="error", content={"message": f"Remix failed: {e}"})
        return
    await session.commit()
    await add_message(session, chat.id, role="System", type="remix",
                      content={"chat_id": source.id, "title": source.title, "version": version})


class ForkBody(BaseModel):
    version: str | None = None
    title: str | None = None


@router.post("/chats/{chat_id}/fork")
async def fork_chat(chat_id: str, body: ForkBody, ctx: WorkspaceCtx = Depends(current_workspace),
                    user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await create_chat(CreateChatBody(title=body.title, remix=RemixBody(chat_id=chat_id, version=body.version)),
                             ctx, user, session)


@router.get("/chats/{chat_id}")
async def get_chat(chat: m.Chat = Depends(chat_guard("read", auth=False)), user: m.User | None = Depends(optional_user),
                   session: AsyncSession = Depends(get_session)):
    return ok(await chat_out(session, chat, user))


class PatchChat(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    llm: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    access_mode: str | None = None
    is_starred: bool | None = None
    folder_id: int | None = None


@router.patch("/chats/{chat_id}")
async def patch_chat(body: PatchChat, chat: m.Chat = Depends(chat_guard("write")), user: m.User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    fields = body.model_dump(exclude_unset=True)
    if "title" in fields and body.title:
        chat.title = body.title
    if body.llm is not None:
        chat.llm = {**(chat.llm or {}), **body.llm}
    if body.config is not None:
        cfg = {**(chat.config or {}), **body.config}
        if cfg.get("agent_mode") not in (None, "lite", "full"):
            raise bad_request("agent_mode must be lite or full")
        chat.config = cfg
    if body.access_mode is not None:
        if body.access_mode not in ACCESS_MODES:
            raise bad_request("invalid access_mode")
        chat.access_mode = body.access_mode
    if body.is_starred is not None:
        chat.is_starred = body.is_starred
    if "folder_id" in fields:
        if body.folder_id is not None:
            f = await session.get(m.Folder, body.folder_id)
            if f is None or f.workspace_id != chat.workspace_id:
                raise bad_request("invalid folder")
        chat.folder_id = body.folder_id
    chat.updated_at = datetime.now(UTC)
    await session.commit()
    out = await chat_out(session, chat, user)
    await emit_chat_update(chat, {k: out[k] for k in ("title", "llm", "config", "access_mode", "is_starred", "folder_id")})
    return ok(out)


@router.delete("/chats/{chat_id}")
async def delete_chat(chat: m.Chat = Depends(chat_guard("admin")), session: AsyncSession = Depends(get_session)):
    runner, mgr = hooks.runner(), hooks.manager()
    if chat.is_running and runner:
        await runner.cancel(chat.id)
    if mgr:
        try:
            await mgr.stop(chat.id)
        except Exception:
            log.exception("stop sandbox failed")
    chat.deleted_at = datetime.now(UTC)
    await session.commit()
    return ok(None)


@router.post("/chats/{chat_id}/like")
async def like(chat: m.Chat = Depends(chat_guard("read")), user: m.User = Depends(current_user),
               session: AsyncSession = Depends(get_session)):
    if await session.get(m.ChatLike, (chat.id, user.id)) is None:
        session.add(m.ChatLike(chat_id=chat.id, user_id=user.id))
        chat.likes = (chat.likes or 0) + 1
        await session.commit()
    return ok({"likes": chat.likes, "is_liked": True})


@router.delete("/chats/{chat_id}/like")
async def unlike(chat: m.Chat = Depends(chat_guard("read")), user: m.User = Depends(current_user),
                 session: AsyncSession = Depends(get_session)):
    row = await session.get(m.ChatLike, (chat.id, user.id))
    if row is not None:
        await session.delete(row)
        chat.likes = max(0, (chat.likes or 0) - 1)
        await session.commit()
    return ok({"likes": chat.likes, "is_liked": False})


@router.get("/discover")
async def discover(sort: str = "hot", cur_page: int = Query(1, ge=1), page_num: int = Query(24, ge=1, le=100),
                   keyword: str | None = None, session: AsyncSession = Depends(get_session)):
    q = (select(m.Chat, m.User, m.Version)
         .join(m.User, m.User.id == m.Chat.user_id)
         .join(m.Version, (m.Version.chat_id == m.Chat.id) & (m.Version.version == m.Chat.current_version))
         .where(m.Chat.access_mode == "public", m.Chat.deleted_at.is_(None), m.Chat.parent_chat_id.is_(None),
                m.Version.status == "ready"))
    if keyword:
        q = q.where(or_(m.Chat.title.ilike(f"%{keyword}%")))
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    order = m.Chat.created_at.desc() if sort == "new" else (m.Chat.likes * 10 + m.Chat.views).desc()
    rows = (await session.execute(q.order_by(order, m.Chat.created_at.desc()).offset((cur_page - 1) * page_num).limit(page_num))).all()
    items = [{"chat_id": c.id, "title": c.title, "cover": v.cover, "link": v.link, "version": v.version,
              "likes": c.likes, "views": c.views, "created_at": ser.iso(c.created_at),
              "author": {"id": u.id, "name": u.name, "avatar": u.avatar}} for c, u, v in rows]
    return ok({"items": items, "total": total, "has_more": cur_page * page_num < total})


@router.get("/share/{chat_id}")
async def share(chat_id: str, version: str | None = None, user: m.User | None = Depends(optional_user),
                session: AsyncSession = Depends(get_session)):
    chat = await load_chat(session, chat_id, user, "read")
    chat.views = (chat.views or 0) + 1
    await session.commit()
    hist = await history(session, chat_id)
    messages = hist["messages"]
    if version:
        cut = next((i for i, x in enumerate(messages) if x["type"] == "version" and (x["content"] or {}).get("version") == version), None)
        if cut is not None:
            messages = messages[: cut + 1]
    secrets = await secret_values(chat_id)
    if secrets:
        masked, _ = mask(json.dumps(messages, ensure_ascii=False), secrets)
        messages = json.loads(masked)
    tq = select(m.BlockTimeline).where(m.BlockTimeline.chat_id == chat_id).order_by(m.BlockTimeline.index)
    timelines = [ser.timeline(t, redact=True) for t in (await session.execute(tq)).scalars()]
    if messages:
        last = messages[-1]["created_at"]
        timelines = [t for t in timelines if t["created_at"] <= last] if version else timelines
    uuids = {a["message_uuid"] for a in hist["action_datas"]} & {x["uuid"] for x in messages}
    versions = (await session.execute(select(m.Version).where(m.Version.chat_id == chat_id).order_by(m.Version.number))).scalars()
    owner = await session.get(m.User, chat.user_id)
    out_chat = await chat_out(session, chat, user)
    out_chat["author"] = {"id": owner.id, "name": owner.name, "avatar": owner.avatar} if owner else None
    return ok({"chat": out_chat, "messages": messages,
               "action_datas": [a for a in hist["action_datas"] if a["message_uuid"] in uuids],
               "timelines": timelines, "versions": [ser.version(v) for v in versions]})
