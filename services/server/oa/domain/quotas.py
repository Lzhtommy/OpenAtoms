"""Quotas & limits (PLAN P8): per-plan sandbox concurrency, workspace disk, daily credits, API rate limits,
request body size.

Plan limits (overridable per deployment with OA_PLAN_LIMITS='{"free": {"disk_mb": 2048}}'):

| plan | concurrent sandboxes | disk per workspace | daily credits | API req/min |
|------|----------------------|--------------------|---------------|-------------|
| free | 2                    | 1 GiB              | 300           | 300         |
| pro  | 5                    | 5 GiB              | 3000          | 1200        |
| max  | 10                   | 20 GiB             | 20000         | 3000        |
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from oa.config import settings
from oa.core.events import bus
from oa.core.observability import DISK_BYTES, QUOTA_BLOCKS, RATE_LIMITED
from oa.db import models as m
from oa.db.session import SessionLocal

log = logging.getLogger("oa.quotas")

RATE_LIMITED_CODE = 4291
DISK_EXCEEDED_CODE = 4292
DAILY_EXHAUSTED_CODE = 4293
BODY_TOO_LARGE_CODE = 4413

_DEFAULT_LIMITS: dict[str, dict[str, int]] = {
    "free": {"sandboxes": 2, "disk_mb": 1024, "daily_credits": 300, "rpm": 300},
    "pro": {"sandboxes": 5, "disk_mb": 5120, "daily_credits": 3000, "rpm": 1200},
    "max": {"sandboxes": 10, "disk_mb": 20480, "daily_credits": 20000, "rpm": 3000},
}


def plan_limits(plan: str | None) -> dict[str, int]:
    base = dict(_DEFAULT_LIMITS.get(plan or "free", _DEFAULT_LIMITS["free"]))
    raw = os.environ.get("OA_PLAN_LIMITS")
    if raw:
        with contextlib.suppress(Exception):
            base.update(json.loads(raw).get(plan or "free", {}))
    return base


# ---------------------------------------------------------------- sandbox concurrency
async def max_sandboxes(workspace_id: int) -> int:
    async with SessionLocal() as s:
        ws = await s.get(m.Workspace, workspace_id)
    lim = plan_limits(ws.plan if ws else "free")["sandboxes"]
    # settings.sandbox_max_running_per_workspace is the operator's global hard cap
    return min(lim, settings.sandbox_max_running_per_workspace) if settings.sandbox_max_running_per_workspace else lim


# ---------------------------------------------------------------- disk
@dataclass
class DiskUsage:
    bytes: int
    measured_at: float


_disk: dict[str, DiskUsage] = {}
DISK_TTL = 60.0


def _du(path: Path) -> int:
    total = 0
    seen: set[tuple[int, int]] = set()
    for root, dirs, files in os.walk(path, followlinks=False):
        # .git is part of the project (versions); count everything incl. node_modules (it occupies the volume)
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if st.st_nlink > 1:
                if key in seen:  # pnpm hard links into the shared store: count once per workspace
                    continue
                seen.add(key)
            total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    return total


async def measure_disk(chat_id: str, *, force: bool = False) -> int:
    cached = _disk.get(chat_id)
    if cached and not force and time.monotonic() - cached.measured_at < DISK_TTL:
        return cached.bytes
    ws = settings.workspace_dir(chat_id)
    target = ws.resolve() if ws.exists() else None
    n = await asyncio.to_thread(_du, target) if target else 0
    _disk[chat_id] = DiskUsage(n, time.monotonic())
    DISK_BYTES.set(max(u.bytes for u in _disk.values()))
    return n


async def disk_status(chat_id: str, *, force: bool = False) -> dict[str, Any]:
    async with SessionLocal() as s:
        chat = await s.get(m.Chat, chat_id)
        ws = await s.get(m.Workspace, chat.workspace_id) if chat else None
    limit = plan_limits(ws.plan if ws else "free")["disk_mb"] * 1024 * 1024
    used = await measure_disk(chat_id, force=force)
    return {"used_bytes": used, "limit_bytes": limit, "exceeded": used > limit,
            "percent": round(100 * used / limit, 1) if limit else 0}


async def publish_disk(chat_id: str, *, force: bool = False) -> dict[str, Any]:
    st = await disk_status(chat_id, force=force)
    await bus.emit(chat_id, "disk:quota:update", st)
    if st["exceeded"]:
        await bus.emit(chat_id, "disk:exceeded", st)
    return st


async def disk_monitor_loop(interval: float = 60.0) -> None:
    """Measure running/recently-active workspaces and push disk:* events."""
    while True:
        try:
            async with SessionLocal() as s:
                ids = (await s.execute(select(m.Sandbox.chat_id).where(m.Sandbox.status == "running"))).scalars().all()
            for cid in ids:
                await publish_disk(cid, force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("disk monitor iteration failed")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------- daily credits
async def daily_used(workspace_id: int) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with SessionLocal() as s:
        used = (await s.execute(
            select(func.coalesce(func.sum(m.CreditLedger.delta), 0)).where(
                m.CreditLedger.workspace_id == workspace_id, m.CreditLedger.delta < 0,
                m.CreditLedger.reason.in_(("llm", "sandbox")), m.CreditLedger.created_at >= start)
        )).scalar()
    return -int(used or 0)


@dataclass
class Block:
    code: int
    reason: str
    message: str
    data: dict[str, Any]


async def check_run_allowed(chat_id: str) -> Block | None:
    """Pre-run gate (besides the credit balance check): disk quota and daily credit cap."""
    async with SessionLocal() as s:
        chat = await s.get(m.Chat, chat_id)
        if chat is None:
            return None
        ws = await s.get(m.Workspace, chat.workspace_id)
        members = (await s.execute(select(m.WorkspaceMember.user_id).where(
            m.WorkspaceMember.workspace_id == chat.workspace_id))).scalars().all()
    lim = plan_limits(ws.plan if ws else "free")

    disk = await disk_status(chat_id)
    if disk["exceeded"]:
        QUOTA_BLOCKS.labels("disk").inc()
        await bus.emit(chat_id, "disk:exceeded", disk)
        mb = disk["limit_bytes"] // (1024 * 1024)
        return Block(DISK_EXCEEDED_CODE, "disk", f"This project uses more than its {mb} MB disk quota. "
                     "Delete large files (e.g. datasets, build artifacts) or upgrade your plan to continue.", disk)

    used = await daily_used(chat.workspace_id)
    if used >= lim["daily_credits"]:
        QUOTA_BLOCKS.labels("daily_credits").inc()
        data = {"used_today": used, "daily_limit": lim["daily_credits"], "plan": ws.plan if ws else "free"}
        for uid in members:
            await bus.emit_user(uid, "quota:daily_exhausted", {**data, "workspace_id": chat.workspace_id})
        await bus.emit(chat_id, "quota:daily_exhausted", data)
        return Block(DAILY_EXHAUSTED_CODE, "daily_credits",
                     f"Daily credit limit reached ({used}/{lim['daily_credits']}). It resets at 00:00 UTC; "
                     "upgrade your plan for a higher limit.", data)
    return None


# ---------------------------------------------------------------- rate limiting
@dataclass
class Rule:
    name: str
    capacity: int  # burst
    per_minute: float  # refill rate


AUTH_RULE = Rule("auth", capacity=10, per_minute=20)  # per IP: login/register/password
SEND_RULE = Rule("send", capacity=10, per_minute=30)  # per user: new agent rounds
APP_RULE = Rule("app", capacity=200, per_minute=1200)  # per generated-app host (Cloud BaaS)


def _env_rule(name: str, default: Rule) -> Rule:
    v = os.environ.get(f"OA_RATE_{name.upper()}_PER_MIN")
    if not v:
        return default
    per = float(v)
    return Rule(default.name, capacity=max(1, int(per / 3)), per_minute=per)


class MemoryBuckets:
    def __init__(self) -> None:
        self._b: dict[str, tuple[float, float]] = {}

    async def take(self, key: str, rule: Rule, cost: float = 1.0) -> float:
        """Returns 0 if allowed, else seconds until a token is available."""
        now = time.monotonic()
        tokens, ts = self._b.get(key, (float(rule.capacity), now))
        rate = rule.per_minute / 60.0
        tokens = min(rule.capacity, tokens + (now - ts) * rate)
        if tokens >= cost:
            self._b[key] = (tokens - cost, now)
            if len(self._b) > 100_000:  # bound memory: drop the oldest half
                for k in list(self._b)[: len(self._b) // 2]:
                    self._b.pop(k, None)
            return 0.0
        self._b[key] = (tokens, now)
        return (cost - tokens) / rate if rate > 0 else 60.0


_LUA = """
local key = KEYS[1]
local cap = tonumber(ARGV[1]); local rate = tonumber(ARGV[2]); local now = tonumber(ARGV[3]); local cost = tonumber(ARGV[4])
local v = redis.call('HMGET', key, 't', 'ts')
local tokens = tonumber(v[1]) or cap; local ts = tonumber(v[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)
local wait = 0
if tokens >= cost then tokens = tokens - cost else wait = (cost - tokens) / rate end
redis.call('HSET', key, 't', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(cap / rate) + 60)
return tostring(wait)
"""


class RedisBuckets:
    def __init__(self) -> None:
        import redis.asyncio as aioredis

        self.r = aioredis.from_url(settings.redis_url)
        self.script = self.r.register_script(_LUA)

    async def take(self, key: str, rule: Rule, cost: float = 1.0) -> float:
        rate = rule.per_minute / 60.0
        res = await self.script(keys=[f"oa:rl:{key}"], args=[rule.capacity, rate, time.time(), cost])
        return float(res)


_buckets: MemoryBuckets | RedisBuckets | None = None


def buckets() -> MemoryBuckets | RedisBuckets:
    global _buckets
    if _buckets is None:
        _buckets = RedisBuckets() if settings.use_redis else MemoryBuckets()
    return _buckets


def reset_buckets() -> None:
    global _buckets
    _buckets = None


def _client_ip(scope: Scope) -> str:
    headers = dict(scope.get("headers") or [])
    xff = headers.get(b"x-forwarded-for")
    if xff and os.environ.get("OA_TRUST_PROXY", "0") == "1":
        return xff.decode().split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _user_from_auth(scope: Scope) -> tuple[int | None, str | None]:
    headers = dict(scope.get("headers") or [])
    raw = headers.get(b"authorization", b"").decode()
    tok = raw.split(" ", 1)[1] if raw.lower().startswith("bearer ") else raw
    if not tok:
        return None, None
    try:
        from oa.core import security

        payload = security.decode(tok, "access")
        return (int(payload["user_id"]), None) if payload else (None, None)
    except Exception:
        return None, None


_AUTH_PATHS = ("/api/v1/user/login", "/api/v1/user/register", "/api/v1/user/pwd/", "/api/v1/user/refresh",
               "/apps/api/v1/auth/login", "/apps/api/v1/auth/register")
_EXEMPT = ("/internal/", "/metrics", "/api/v1/health", "/socket.io")


def _is_send(method: str, path: str) -> bool:
    if method != "POST" or not path.startswith("/api/v1/chats"):
        return False
    return path.rstrip("/") == "/api/v1/chats" or path.endswith("/messages") or path.endswith("/retry") \
        or path.endswith("/edit")


async def _user_rule(user_id: int) -> Rule:
    # plan-aware general limit; plan lookup is cached per user for 5 minutes
    hit = _plan_cache.get(user_id)
    if hit and time.monotonic() - hit[1] < 300:
        plan = hit[0]
    else:
        async with SessionLocal() as s:
            u = await s.get(m.User, user_id)
            plan = u.plan if u else "free"
        _plan_cache[user_id] = (plan, time.monotonic())
    rpm = plan_limits(plan)["rpm"]
    return _env_rule("api", Rule("api", capacity=max(20, rpm // 4), per_minute=rpm))


_plan_cache: dict[int, tuple[str, float]] = {}


class RateLimitMiddleware:
    """Token buckets: per-IP on auth endpoints, per-user (plan-aware) on the API, per-user on new runs,
    per-app-host on generated-app traffic. Rejections: HTTP 429, code 4291, Retry-After."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or os.environ.get("OA_RATE_LIMIT", "1") == "0":
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path", ""), scope.get("method", "GET")
        if method == "OPTIONS" or any(path.startswith(p) for p in _EXEMPT):
            await self.app(scope, receive, send)
            return
        checks: list[tuple[str, Rule]] = []
        ip = _client_ip(scope)
        if any(path.startswith(p) for p in _AUTH_PATHS):
            checks.append((f"auth:{ip}", _env_rule("auth", AUTH_RULE)))
        if path.startswith("/apps/api/"):
            headers = dict(scope.get("headers") or [])
            app_chat = headers.get(b"x-oa-app-chat", b"").decode() or ip
            checks.append((f"app:{app_chat}", _env_rule("app", APP_RULE)))
        elif path.startswith("/api/"):
            uid, _ = _user_from_auth(scope)
            if uid is not None:
                checks.append((f"api:u{uid}", await _user_rule(uid)))
                if _is_send(method, path):
                    checks.append((f"send:u{uid}", _env_rule("send", SEND_RULE)))
            else:
                checks.append((f"api:ip:{ip}", _env_rule("anon", Rule("anon", capacity=60, per_minute=300))))
        for key, rule in checks:
            try:
                wait = await buckets().take(key, rule)
            except Exception:  # never fail closed because the limiter backend is down
                log.warning("rate limiter backend error", exc_info=True)
                wait = 0.0
            if wait > 0:
                RATE_LIMITED.labels(rule.name).inc()
                await _reject(send, 429, RATE_LIMITED_CODE,
                              f"Too many requests ({rule.name}); retry in {math.ceil(wait)}s",
                              {"retry_after": math.ceil(wait), "bucket": rule.name},
                              [(b"retry-after", str(math.ceil(wait)).encode())])
                return
        await self.app(scope, receive, send)


# ---------------------------------------------------------------- body size
def _max_body(path: str, content_type: str) -> int:
    big = int(float(os.environ.get("OA_MAX_UPLOAD_MB", "100")) * 1024 * 1024)
    small = int(float(os.environ.get("OA_MAX_BODY_MB", "5")) * 1024 * 1024)
    if content_type.startswith("multipart/") or path.endswith("/import") or "/storage/" in path \
            or path.endswith("/webhook"):
        return big
    return small


class BodySizeMiddleware:
    """413 for oversized requests: checks Content-Length up front and counts streamed bytes otherwise."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        limit = _max_body(scope.get("path", ""), headers.get(b"content-type", b"").decode().lower())
        cl = headers.get(b"content-length")
        if cl is not None:
            try:
                if int(cl) > limit:
                    await _too_large(send, limit)
                    return
            except ValueError:
                pass
        if cl is None and scope.get("method") in ("POST", "PUT", "PATCH"):
            # unknown length (chunked): buffer up to the limit, then replay to the app
            chunks: list[Message] = []
            seen = 0
            while True:
                msg = await receive()
                if msg["type"] != "http.request":
                    chunks.append(msg)
                    break
                seen += len(msg.get("body", b""))
                if seen > limit:
                    await _too_large(send, limit)
                    return
                chunks.append(msg)
                if not msg.get("more_body"):
                    break

            async def replay() -> Message:
                return chunks.pop(0) if chunks else await receive()

            await self.app(scope, replay, send)
            return
        await self.app(scope, receive, send)


async def _too_large(send: Send, limit: int) -> None:
    await _reject(send, 413, BODY_TOO_LARGE_CODE, f"Request body too large (max {limit // (1024 * 1024)} MB)",
                  {"max_bytes": limit})


async def _reject(send: Send, status: int, code: int, message: str, data: dict[str, Any],
                  extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    payload = json.dumps({"code": code, "message": message, "data": data, "now_ts": time.time()}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()),
                            *(extra_headers or [])]})
    await send({"type": "http.response.body", "body": payload})
