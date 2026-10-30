"""Socket.IO server (CONTRACT §2): JWT auth on connect, user room, join_chat/leave_chat with access check."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs

import socketio

from oa.config import settings
from oa.core import security
from oa.core.deps import chat_permission
from oa.db import models as m
from oa.db.session import SessionLocal

log = logging.getLogger("oa.realtime")

_manager = socketio.AsyncRedisManager(settings.redis_url) if settings.use_redis else None
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    client_manager=_manager,
    ping_interval=25,
    ping_timeout=20,
)


def _token(environ: dict[str, Any], auth: Any) -> str | None:
    if isinstance(auth, dict) and auth.get("token"):
        return str(auth["token"])
    qs = parse_qs(environ.get("QUERY_STRING", ""))
    if qs.get("token"):
        return qs["token"][0]
    hdr = environ.get("HTTP_AUTHORIZATION")
    if hdr:
        return hdr.split(" ", 1)[-1]
    return None


@sio.event
async def connect(sid: str, environ: dict[str, Any], auth: Any = None) -> bool:
    tok = _token(environ, auth)
    data = security.decode(tok, "access") if tok else None
    if not data:
        raise socketio.exceptions.ConnectionRefusedError("unauthorized")
    user_id = int(data["user_id"])
    await sio.save_session(sid, {"user_id": user_id})
    await sio.enter_room(sid, f"user:{user_id}")
    return True


@sio.event
async def disconnect(sid: str, *args: Any) -> None:
    pass


async def _chat_id(data: Any) -> str:
    if isinstance(data, dict):
        return str(data.get("chat_id") or "")
    return str(data or "")


@sio.event
async def join_chat(sid: str, data: Any) -> dict[str, Any]:
    chat_id = await _chat_id(data)
    sess = await sio.get_session(sid)
    code = 0
    async with SessionLocal() as s:
        chat = await s.get(m.Chat, chat_id) if chat_id else None
        user = await s.get(m.User, sess.get("user_id")) if sess else None
        if chat is None or chat.deleted_at is not None:
            code = 4404
        elif not await chat_permission(s, chat, user, "read"):
            code = 4403
    if code == 0:
        await sio.enter_room(sid, f"chat:{chat_id}")
    ack = {"chat_id": chat_id, "ok": code == 0, "code": code}
    await sio.emit("join_chat_ack", ack, to=sid)
    return ack


@sio.event
async def leave_chat(sid: str, data: Any) -> dict[str, Any]:
    chat_id = await _chat_id(data)
    await sio.leave_room(sid, f"chat:{chat_id}")
    return {"chat_id": chat_id, "ok": True}


@sio.on("message_delivered")
async def message_delivered(sid: str, data: Any) -> None:
    pass


@sio.on("message_enriched")
async def message_enriched(sid: str, data: Any) -> None:
    pass
