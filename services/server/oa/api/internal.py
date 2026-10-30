"""Gateway-facing internal API (CONTRACT §1.9). Requires X-Internal-Token."""
from __future__ import annotations

import mimetypes
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.config import settings
from oa.core import security
from oa.core.deps import require_internal
from oa.core.envelope import bad_request, forbidden
from oa.core.storage import get_storage
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import hooks

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal)])

PREVIEW_COOKIE_TTL = 7 * 86400


def _app_host() -> str:
    return settings.app_domain.split(":")[0].lower()


def _preview_re() -> re.Pattern[str]:
    return re.compile(rf"^(\d+)-([0-9a-f]{{32}})-preview\.{re.escape(_app_host())}$")


def _deploy_re() -> re.Pattern[str]:
    return re.compile(rf"^(\d+)-([0-9a-f]{{32}})-(\d+)-(latest|v\d+)\.{re.escape(_app_host())}$")


def parse_host(host: str) -> dict | None:
    h = host.split(":")[0].lower()
    if mt := _preview_re().match(h):
        return {"kind": "preview", "port": int(mt.group(1)), "chat_id": mt.group(2)}
    if mt := _deploy_re().match(h):
        return {"kind": "deploy", "user_id": int(mt.group(1)), "chat_id": mt.group(2), "seq": int(mt.group(3)),
                "label": mt.group(4)}
    return None


def cookie_ok(cookie: str | None, chat_id: str) -> bool:
    return bool(cookie) and security.unsign(cookie) == chat_id


def _static(chat_id: str, version: str) -> dict:
    return {"kind": "static", "base": f"{settings.public_api_url}/internal/static/{chat_id}/{version}",
            "chat_id": chat_id, "env": "prod", "version": version}


def both(d: dict) -> dict:
    """Bare JSON per CONTRACT §1.9, also mirrored under the standard envelope keys for envelope-aware clients."""
    return {**d, "code": 0, "message": "Success.", "data": dict(d)}


@router.get("/resolve")
async def resolve(host: str, path: str = "/", cookie: str | None = None, session: AsyncSession = Depends(get_session)):
    return both(await _resolve(session, host, cookie))


async def _resolve(session: AsyncSession, host: str, cookie: str | None) -> dict:
    info = parse_host(host)
    if info is None:
        return {"kind": "notfound"}
    chat = await session.get(m.Chat, info["chat_id"])
    if chat is None or chat.deleted_at is not None:
        return {"kind": "notfound"}

    if info["kind"] == "preview":
        if chat.access_mode == "private" and not cookie_ok(cookie, chat.id):
            return {"kind": "auth_required", "chat_id": chat.id}
        mgr = hooks.manager()
        if mgr is None:
            return {"kind": "notfound"}
        upstream = await mgr.resolve_port(chat.id, info["port"])
        if upstream:
            return {"kind": "sandbox", "upstream": upstream, "chat_id": chat.id, "env": "dev"}
        try:
            await mgr.wake(chat.id)
        except Exception:
            pass
        return {"kind": "waking", "chat_id": chat.id}

    if info["user_id"] != chat.user_id:
        return {"kind": "notfound"}
    if info["seq"] == 0:
        # immutable per-version preview link (shown in version history)
        if info["label"] == "latest":
            return {"kind": "notfound"}
        if chat.access_mode == "private" and not cookie_ok(cookie, chat.id):
            return {"kind": "auth_required", "chat_id": chat.id}
        ver = (await session.execute(select(m.Version).where(m.Version.chat_id == chat.id,
                                                             m.Version.version == info["label"]))).scalar_one_or_none()
        if ver is None or ver.status != "ready":
            return {"kind": "notfound"}
        return _static(chat.id, ver.version)

    dep = await session.get(m.Deployment, chat.id)
    if dep is None or dep.status == "deleted" or dep.seq != info["seq"]:
        return {"kind": "notfound"}
    # published sites follow the deployment's own access mode (public by default), not the project's
    if (dep.access_mode or "public") == "private" and not cookie_ok(cookie, chat.id):
        return {"kind": "auth_required", "chat_id": chat.id}
    if info["label"] == "latest":
        if not dep.is_always_latest:
            return {"kind": "notfound"}
        return _static(chat.id, dep.version)
    ver = (await session.execute(select(m.Version).where(m.Version.chat_id == chat.id,
                                                         m.Version.version == info["label"]))).scalar_one_or_none()
    if ver is None or ver.status != "ready":
        return {"kind": "notfound"}
    return _static(chat.id, ver.version)


@router.get("/static/{chat_id}/{version}/{path:path}")
@router.get("/static/{chat_id}/{version}")
async def static_file(chat_id: str, version: str, path: str = ""):
    if not re.fullmatch(r"[0-9a-f]{32}", chat_id) or not re.fullmatch(r"v\d+", version):
        raise bad_request("invalid path")
    path = path.strip("/") or "index.html"
    if ".." in path.split("/"):
        raise bad_request("invalid path")
    data = await get_storage().get_bytes(f"deploys/{chat_id}/{version}/{path}")
    if data is None:
        return Response(status_code=404, content=b"not found", media_type="text/plain")
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    cache = "no-cache" if path.endswith(".html") else "public, max-age=31536000, immutable" if "/assets/" in f"/{path}" else "public, max-age=300"
    return Response(content=data, media_type=ctype, headers={"Cache-Control": cache})


class PreviewAuthBody(BaseModel):
    ticket: str
    host: str


@router.post("/preview-auth")
async def preview_auth(body: PreviewAuthBody, session: AsyncSession = Depends(get_session)):
    row = await session.get(m.AuthToken, body.ticket)
    now = datetime.now(UTC)
    if row is None or row.kind != "preview_ticket" or row.used_at is not None:
        raise forbidden("invalid ticket")
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if exp < now:
        raise forbidden("ticket expired")
    chat_id = (row.data or {}).get("chat_id")
    info = parse_host(body.host)
    if not chat_id or info is None or info["chat_id"] != chat_id:
        raise forbidden("ticket does not match host")
    row.used_at = now
    await session.commit()
    return both({"cookie": security.sign(chat_id, PREVIEW_COOKIE_TTL), "max_age": PREVIEW_COOKIE_TTL})
