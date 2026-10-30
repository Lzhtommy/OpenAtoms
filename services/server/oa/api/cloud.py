"""Cloud management (CONTRACT §1.7) and the generated-app BaaS API (CONTRACT §1.10, mounted at /apps/api/v1)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import mimetypes
import os
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.domain import serializers as ser
from oa.config import settings
from oa.core import security
from oa.core.deps import chat_guard, user_from_token
from oa.core.envelope import bad_request, not_found, ok
from oa.core.storage import get_storage
from oa.db import models as m
from oa.db.session import get_session
from oa.domain.cloud import ENVS, CloudDB, CloudError, check_env, check_name, ensure_cloud

router = APIRouter(tags=["cloud"])


# ------------------------------------------------------------------ management
@router.post("/chats/{chat_id}/cloud/enable")
async def enable(chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    await ensure_cloud(chat.id)
    chat.config = {**(chat.config or {}), "cloud_enabled": True, "db_mode": "double"}
    await session.commit()
    return ok({"cloud_enabled": True, "environments": list(ENVS)})


@router.get("/chats/{chat_id}/database/environments")
async def environments(chat: m.Chat = Depends(chat_guard("read"))):
    return ok([{"env": e, "ready": await CloudDB(chat.id, e).exists()} for e in ENVS])


def _db(chat: m.Chat, env: str) -> CloudDB:
    try:
        return CloudDB(chat.id, check_env(env))
    except CloudError as e:
        raise bad_request(str(e)) from e


@router.get("/chats/{chat_id}/database/{env}/tables")
async def tables(env: str, chat: m.Chat = Depends(chat_guard("read"))):
    return ok(await _db(chat, env).schemas())


async def _table(db: CloudDB, t: str) -> str:
    try:
        check_name(t)
    except CloudError as e:
        raise bad_request(str(e)) from e
    if not await db.has_table(t):
        raise not_found("Table not found")
    return t


@router.get("/chats/{chat_id}/database/{env}/tables/{t}/schema")
async def table_schema(env: str, t: str, chat: m.Chat = Depends(chat_guard("read"))):
    db = _db(chat, env)
    await _table(db, t)
    return ok(next(s for s in await db.schemas() if s["name"] == t))


@router.get("/chats/{chat_id}/database/{env}/tables/{t}/rows")
async def table_rows(env: str, t: str, cur_page: int = Query(1, ge=1), page_num: int = Query(50, ge=1, le=500),
                     chat: m.Chat = Depends(chat_guard("read"))):
    db = _db(chat, env)
    await _table(db, t)
    rows = await db.query(t, sort="-id")
    start = (cur_page - 1) * page_num
    return ok({"items": rows[start:start + page_num], "total": len(rows), "has_more": start + page_num < len(rows)})


@router.get("/chats/{chat_id}/database/{env}/tables/{t}/export")
async def export_csv(env: str, t: str, chat: m.Chat = Depends(chat_guard("read"))):
    db = _db(chat, env)
    await _table(db, t)
    rows = await db.query(t)
    cols: list[str] = ["id"]
    for r in rows:
        cols += [k for k in r if k not in cols]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in r.items()})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{t}-{env}.csv"'})


def _coerce(v: str) -> Any:
    if v == "":
        return None
    for fn in (int, float):
        try:
            return fn(v)
        except ValueError:
            pass
    if v in ("true", "false"):
        return v == "true"
    if v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            pass
    return v


@router.post("/chats/{chat_id}/database/{env}/tables/{t}/import")
async def import_csv(env: str, t: str, file: UploadFile = File(...), chat: m.Chat = Depends(chat_guard("write"))):
    db = _db(chat, env)
    try:
        check_name(t)
    except CloudError as e:
        raise bad_request(str(e)) from e
    text = (await file.read()).decode("utf-8-sig")
    rows = [{k: _coerce(v) for k, v in r.items() if k and k not in ("id",)} for r in csv.DictReader(io.StringIO(text))]
    inserted = await db.insert(t, rows)
    return ok({"inserted": len(inserted)})


# ------------------------------------------------------------------ app BaaS (no envelope; consumed by app-sdk)
apps = APIRouter(prefix="/apps/api/v1", tags=["apps"])


class AppCtx:
    def __init__(self, chat_id: str, env: str, user: dict | None) -> None:
        self.chat_id, self.env, self.user = chat_id, env, user

    @property
    def db(self) -> CloudDB:
        return CloudDB(self.chat_id, self.env)


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg, "detail": msg}, status_code=status)


class AppError(Exception):
    def __init__(self, status: int, msg: str) -> None:
        self.status, self.msg = status, msg


async def app_ctx(request: Request, session: AsyncSession = Depends(get_session)) -> AppCtx:
    chat_id = request.headers.get("x-oa-app-chat", "")
    env = request.headers.get("x-oa-app-env", "prod")
    if env not in ENVS:
        env = "prod"
    chat = await session.get(m.Chat, chat_id) if chat_id else None
    if chat is None or chat.deleted_at is not None:
        raise AppError(404, "unknown app")
    user = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        data = security.decode(auth[7:].strip(), "app", security.app_secret(chat_id))
        if data and data.get("chat_id") == chat_id and data.get("env") == env:
            user = {"id": data["app_user_id"], "email": data.get("email")}
    return AppCtx(chat_id, env, user)


def require_user(ctx: AppCtx) -> dict:
    if ctx.user is None:
        raise AppError(401, "not authenticated")
    return ctx.user


@apps.get("/config")
async def app_config(ctx: AppCtx = Depends(app_ctx)):
    return {"chat_id": ctx.chat_id, "env": ctx.env}


class Creds(BaseModel):
    email: str
    password: str


def _app_user_out(u: m.AppUser) -> dict:
    return {"id": u.id, "email": u.email, "created_at": ser.iso(u.created_at)}


@apps.post("/auth/register")
async def app_register(body: Creds, ctx: AppCtx = Depends(app_ctx), session: AsyncSession = Depends(get_session)):
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < 6:
        raise AppError(400, "valid email and a password of at least 6 characters are required")
    exists = (await session.execute(select(m.AppUser).where(m.AppUser.chat_id == ctx.chat_id, m.AppUser.env == ctx.env,
                                                            m.AppUser.email == email))).scalar_one_or_none()
    if exists:
        raise AppError(409, "email already registered")
    u = m.AppUser(chat_id=ctx.chat_id, env=ctx.env, email=email, password_hash=security.hash_password(body.password))
    session.add(u)
    await session.commit()
    return {"token": security.app_token(ctx.chat_id, ctx.env, u.id, email), "user": _app_user_out(u)}


@apps.post("/auth/login")
async def app_login(body: Creds, ctx: AppCtx = Depends(app_ctx), session: AsyncSession = Depends(get_session)):
    u = (await session.execute(select(m.AppUser).where(m.AppUser.chat_id == ctx.chat_id, m.AppUser.env == ctx.env,
                                                       m.AppUser.email == body.email.strip().lower()))).scalar_one_or_none()
    if u is None or not security.verify_password(body.password, u.password_hash):
        raise AppError(401, "invalid email or password")
    return {"token": security.app_token(ctx.chat_id, ctx.env, u.id, u.email), "user": _app_user_out(u)}


class Exchange(BaseModel):
    platform_token: str


@apps.post("/auth/token/exchange")
async def app_exchange(body: Exchange, ctx: AppCtx = Depends(app_ctx), session: AsyncSession = Depends(get_session)):
    """Platform (OpenAtoms account) SSO → app token."""
    pu = await user_from_token(session, body.platform_token)
    if pu is None:
        raise AppError(401, "invalid platform token")
    u = (await session.execute(select(m.AppUser).where(m.AppUser.chat_id == ctx.chat_id, m.AppUser.env == ctx.env,
                                                       m.AppUser.email == pu.email))).scalar_one_or_none()
    if u is None:
        u = m.AppUser(chat_id=ctx.chat_id, env=ctx.env, email=pu.email, platform_user_id=pu.id,
                      password_hash=security.hash_password(security.random_token()))
        session.add(u)
        await session.commit()
    return {"token": security.app_token(ctx.chat_id, ctx.env, u.id, u.email), "user": _app_user_out(u)}


@apps.get("/auth/me")
async def app_me(ctx: AppCtx = Depends(app_ctx), session: AsyncSession = Depends(get_session)):
    user = require_user(ctx)
    u = await session.get(m.AppUser, user["id"])
    if u is None:
        raise AppError(401, "user no longer exists")
    return _app_user_out(u)


@apps.post("/auth/logout")
async def app_logout():
    return {"ok": True}


def _entity(name: str) -> str:
    try:
        return check_name(name)
    except CloudError as e:
        raise AppError(400, str(e)) from e


@apps.get("/entities/{entity}")
@apps.get("/entities/{entity}/all")
async def ent_list(entity: str, query: str | None = None, sort: str | None = None, limit: int | None = Query(None, ge=1, le=1000),
                   skip: int = Query(0, ge=0), ctx: AppCtx = Depends(app_ctx)):
    try:
        q = json.loads(query) if query else {}
        if not isinstance(q, dict):
            raise ValueError
    except ValueError as e:
        raise AppError(400, "query must be a JSON object") from e
    return await ctx.db.query(_entity(entity), q, sort, limit, skip)


@apps.get("/entities/{entity}/{row_id}")
async def ent_get(entity: str, row_id: int, ctx: AppCtx = Depends(app_ctx)):
    row = await ctx.db.get(_entity(entity), row_id)
    if row is None:
        raise AppError(404, "not found")
    return row


@apps.post("/entities/{entity}")
async def ent_create(entity: str, request: Request, ctx: AppCtx = Depends(app_ctx)):
    data = await request.json()
    if not isinstance(data, dict):
        raise AppError(400, "body must be an object")
    owner = ctx.user["id"] if ctx.user else None
    return (await ctx.db.insert(_entity(entity), [data], owner))[0]


@apps.post("/entities/{entity}/batch")
async def ent_batch(entity: str, request: Request, ctx: AppCtx = Depends(app_ctx)):
    data = await request.json()
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise AppError(400, "body must be an array of objects")
    owner = ctx.user["id"] if ctx.user else None
    return await ctx.db.insert(_entity(entity), data, owner)


@apps.put("/entities/{entity}/{row_id}")
@apps.patch("/entities/{entity}/{row_id}")
async def ent_update(entity: str, row_id: int, request: Request, ctx: AppCtx = Depends(app_ctx)):
    data = await request.json()
    if not isinstance(data, dict):
        raise AppError(400, "body must be an object")
    row = await ctx.db.update(_entity(entity), row_id, data)
    if row is None:
        raise AppError(404, "not found")
    return row


@apps.delete("/entities/{entity}/{row_id}")
async def ent_delete(entity: str, row_id: int, ctx: AppCtx = Depends(app_ctx)):
    if not await ctx.db.delete(_entity(entity), row_id):
        raise AppError(404, "not found")
    return {"ok": True}


@apps.post("/storage/upload")
async def st_upload(file: UploadFile = File(...), ctx: AppCtx = Depends(app_ctx)):
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise AppError(413, "file too large")
    name = os.path.basename(file.filename or "file.bin") or "file.bin"
    rel = f"{uuid.uuid4().hex[:12]}/{name}"
    await get_storage().put_bytes(f"apps/{ctx.chat_id}/{ctx.env}/{rel}", data, file.content_type)
    return {"url": f"/api/v1/storage/files/{rel}", "key": rel, "size": len(data)}


@apps.get("/storage/files/{key:path}")
async def st_get(key: str, ctx: AppCtx = Depends(app_ctx)):
    if ".." in key.split("/"):
        raise AppError(400, "invalid key")
    data = await get_storage().get_bytes(f"apps/{ctx.chat_id}/{ctx.env}/{key}")
    if data is None:
        raise AppError(404, "not found")
    return Response(data, media_type=mimetypes.guess_type(key)[0] or "application/octet-stream")


class GenTxt(BaseModel):
    messages: list[dict[str, Any]]
    model: str | None = None
    stream: bool = True


async def _llm_stream(messages: list[dict[str, Any]], model: str):
    if model == "oa-mock" or not settings.llm_api_key:
        last = next((x.get("content") for x in reversed(messages) if x.get("role") == "user"), "")
        reply = f"(offline AI) You said: {last}"
        for i in range(0, len(reply), 8):
            yield reply[i:i + 8]
            await asyncio.sleep(0.01)
        return
    async with httpx.AsyncClient(timeout=120) as c:
        async with c.stream("POST", f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                            json={"model": model, "messages": messages, "stream": True}) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0]["delta"].get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


@apps.post("/aihub/gentxt")
async def gentxt(body: GenTxt, ctx: AppCtx = Depends(app_ctx)):
    model = body.model or settings.basic_model
    if not body.stream:
        return {"content": "".join([chunk async for chunk in _llm_stream(body.messages, model)])}

    async def gen():
        async for chunk in _llm_stream(body.messages, model):
            yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
