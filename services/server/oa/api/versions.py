"""Versions, deployment, preview tickets, sandbox runtime control and dev-server log stream."""
from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.api.auth import create_one_time
from oa.config import settings
from oa.core.deps import chat_guard, current_user, load_chat
from oa.core.envelope import SANDBOX_FULL, ApiError, bad_request, not_found, ok
from oa.core.events import bus
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import hooks, versions
from oa.domain import serializers as ser
from oa.domain.deployments import deploy_url

router = APIRouter(tags=["versions"])


@router.get("/chats/{chat_id}/versions")
async def list_versions(chat: m.Chat = Depends(chat_guard("read", auth=False)), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(m.Version).where(m.Version.chat_id == chat.id).order_by(m.Version.number.desc()))).scalars()
    return ok([ser.version(v) for v in rows])


async def _version(session: AsyncSession, chat_id: str, v: str) -> m.Version:
    row = (await session.execute(select(m.Version).where(m.Version.chat_id == chat_id, m.Version.version == v))).scalar_one_or_none()
    if row is None:
        raise not_found("Version not found")
    return row


class VersionPatch(BaseModel):
    summary: str


@router.patch("/chats/{chat_id}/versions/{v}")
async def patch_version(v: str, body: VersionPatch, chat: m.Chat = Depends(chat_guard("write")),
                        session: AsyncSession = Depends(get_session)):
    row = await _version(session, chat.id, v)
    row.summary = body.summary[:2000]
    await session.commit()
    await bus.emit(chat.id, "version:update", {"version": ser.version(row)})
    return ok(ser.version(row))


@router.post("/chats/{chat_id}/versions/{v}/restore")
async def restore(v: str, chat: m.Chat = Depends(chat_guard("write")), user: m.User = Depends(current_user),
                  session: AsyncSession = Depends(get_session)):
    await _version(session, chat.id, v)
    if chat.is_running:
        raise ApiError(4409, "Stop the running agent before restoring")
    new = await versions.restore_version(chat.id, v, user.id)
    return ok(ser.version(new))


@router.post("/chats/{chat_id}/versions")
async def snapshot(chat: m.Chat = Depends(chat_guard("write"))):
    """Manually snapshot the current workspace as a new version."""
    new = await versions.create_version(chat.id, "Manual snapshot")
    return ok(ser.version(new))


# ------------------------------------------------------------------ deployment
@router.get("/chats/{chat_id}/deployment")
async def get_deployment(chat: m.Chat = Depends(chat_guard("read")), session: AsyncSession = Depends(get_session)):
    dep = await session.get(m.Deployment, chat.id)
    return ok(ser.deployment(dep) if dep and dep.status != "deleted" else None)


class DeployBody(BaseModel):
    version: str | None = None
    is_always_latest: bool = False
    access_mode: str | None = None


@router.put("/chats/{chat_id}/deployment")
async def put_deployment(body: DeployBody, chat: m.Chat = Depends(chat_guard("write")),
                         session: AsyncSession = Depends(get_session)):
    version = body.version or chat.current_version
    if not version:
        raise bad_request("No version to deploy yet")
    ver = await _version(session, chat.id, version)
    if ver.status != "ready":
        raise bad_request(f"Version {version} has no successful build")
    if body.access_mode is not None and body.access_mode not in ("private", "secret", "public"):
        raise bad_request("invalid access_mode")
    dep = await session.get(m.Deployment, chat.id)
    if dep is None:
        dep = m.Deployment(chat_id=chat.id, version=version, seq=1)
        session.add(dep)
    elif dep.status == "deleted":
        dep.seq = (dep.seq or 0) + 1
    if body.is_always_latest:
        version = chat.current_version or version
    dep.version = version
    dep.is_always_latest = body.is_always_latest
    dep.access_mode = body.access_mode or dep.access_mode or "public"
    dep.kind = "static"
    dep.status = "success"
    dep.url = deploy_url(chat, dep)
    await session.commit()
    out = ser.deployment(dep)
    await bus.emit(chat.id, "deploy:update", {"deployment": out})
    await bus.emit(chat.id, "chat:update", {"chat": {"uris": {"dev": settings.preview_url(chat.id), "prod": dep.url}}})
    return ok(out)


@router.delete("/chats/{chat_id}/deployment")
async def delete_deployment(chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    dep = await session.get(m.Deployment, chat.id)
    if dep is None or dep.status == "deleted":
        raise not_found("Not deployed")
    dep.status = "deleted"
    await session.commit()
    await bus.emit(chat.id, "deploy:update", {"deployment": None})
    await bus.emit(chat.id, "chat:update", {"chat": {"uris": {"dev": settings.preview_url(chat.id), "prod": None}}})
    return ok(None)


# ------------------------------------------------------------------ preview ticket
@router.post("/chats/{chat_id}/preview-ticket")
async def preview_ticket(chat: m.Chat = Depends(chat_guard("read")), user: m.User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    tok = await create_one_time(session, "preview_ticket", user.id, timedelta(minutes=5), {"chat_id": chat.id})
    await session.commit()
    url = f"{settings.preview_url(chat.id)}/__preview_auth?{urlencode({'ticket': tok, 'redirect': '/'})}"
    return ok({"ticket": tok, "url": url})


# ------------------------------------------------------------------ runtime
class HeartbeatBody(BaseModel):
    chat_id: str


def _manager():
    mgr = hooks.manager()
    if mgr is None:
        raise ApiError(5000, "Sandbox manager unavailable")
    return mgr


@router.post("/runtime/heartbeat")
@router.post("/mgx-runtime/heartbeat")
async def heartbeat(body: HeartbeatBody, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    chat = await load_chat(session, body.chat_id, user, "read")
    mgr = _manager()
    await mgr.heartbeat(chat.id)
    return ok({"status": chat.runtime_status})


@router.post("/chats/{chat_id}/runtime/start")
async def runtime_start(chat: m.Chat = Depends(chat_guard("write"))):
    mgr = _manager()
    try:
        started = await mgr.wake(chat.id)
    except Exception as e:
        if type(e).__name__ == "SandboxFull":
            raise ApiError(SANDBOX_FULL, "Too many running sandboxes in this workspace") from e
        raise
    return ok({"status": "activating" if started is not False else "boosting"})


@router.post("/chats/{chat_id}/runtime/stop")
async def runtime_stop(chat: m.Chat = Depends(chat_guard("write"))):
    await _manager().stop(chat.id)
    return ok({"status": "stopped"})


@router.get("/chats/{chat_id}/logs/stream")
@router.get("/log-files/chats/{chat_id}/stream")
async def logs_stream(chat: m.Chat = Depends(chat_guard("read"))):
    mgr = _manager()
    client = await mgr.get_client(chat.id)

    async def gen():
        if client is None:
            yield f"data: {json.dumps({'line': '[sandbox is not running]'})}\n\n"
            return
        try:
            async for line in client.process_logs("dev", follow=True):
                yield f"data: {json.dumps({'line': line})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'line': f'[log stream ended: {e}]'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
