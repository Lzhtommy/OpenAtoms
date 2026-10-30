"""ASGI entrypoint: FastAPI REST (/api/v1, /internal, /apps/api/v1) + Socket.IO (/socket.io).

Run: uv run uvicorn oa.main:app --port 8100
`app` is the Socket.IO ASGI app wrapping the FastAPI app (`api`); non-socket traffic and lifespan pass through.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress

import socketio
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from oa.api import (
    auth,
    automations,
    billing,
    boost,
    chats,
    cloud,
    covers,
    files,
    integrations,
    internal,
    messages,
    versions,
    workspaces,
)
from oa.config import settings
from oa.core.envelope import (
    BAD_REQUEST,
    FORBIDDEN,
    INTERNAL,
    NOT_FOUND,
    QUOTA,
    SANDBOX_FULL,
    UNAUTHORIZED,
    ApiError,
    body,
    error_response,
)
from oa.core.events import bus
from oa.db.session import init_db
from oa.domain import hooks
from oa.realtime.server import sio

from oa.core import observability as obs
from oa.domain import quotas

obs.setup_logging()
obs.setup_sentry("api")
obs.register_collectors()
log = logging.getLogger("oa.main")


async def _periodic(fn, interval: float, name: str) -> None:
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s failed", name)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    bus.attach(sio)
    bus.add_listener(obs.on_event)
    tasks: list[asyncio.Task] = []
    tasks.append(asyncio.create_task(_periodic(obs.refresh_sandbox_counts, 15, "sandbox-counts"), name="sbx-counts"))
    tasks.append(asyncio.create_task(quotas.disk_monitor_loop(), name="disk-monitor"))
    if settings.queue == "inproc":  # with arq, the worker process owns the pool and abuse monitor
        from oa.sandbox import abuse, pool

        tasks.append(asyncio.create_task(pool.maintain_loop(), name="sandbox-pool"))
        tasks.append(asyncio.create_task(abuse.monitor_loop(), name="abuse-monitor"))
    mgr = hooks.manager()
    if mgr is not None and hasattr(mgr, "reaper_loop"):
        tasks.append(asyncio.create_task(mgr.reaper_loop(), name="sandbox-reaper"))
    if settings.queue == "inproc":  # with arq, the worker runs the automation scheduler (cron job)
        from oa.domain import automations as automations_domain

        tasks.append(asyncio.create_task(automations_domain.scheduler_loop(), name="automations"))
    with suppress(Exception):
        from oa.domain import boost as boost_domain

        await boost_domain.recover()
    runner = hooks.runner()
    if runner is not None and settings.queue == "inproc" and hasattr(runner, "recover_interrupted"):
        with suppress(Exception):
            await runner.recover_interrupted()
    log.info("OpenAtoms api ready (mode=%s, db=%s, sandbox=%s)", settings.mode, settings.db_url.split("://")[0],
             settings.sandbox_provider)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        from oa.sandbox import pool

        with suppress(Exception):
            await pool.shutdown()
        mgr = hooks.manager()
        if mgr is not None and hasattr(mgr, "shutdown"):
            with suppress(Exception):
                await mgr.shutdown()


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = next((v.decode() for k, v in scope["headers"] if k == b"x-request-id"), None) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        obs.request_id_var.set(rid)
        obs.set_span_attribute("oa.request_id", rid)

        async def send_wrapper(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                msg["headers"] = [*msg.get("headers", []), (b"x-request-id", rid.encode())]
            await send(msg)

        await self.app(scope, receive, send_wrapper)


api = FastAPI(title="OpenAtoms API", version="0.1.0", lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
# add_middleware wraps outward: the last one added runs first. Order (outer → inner):
# Metrics → CORS (so 429/413 rejections still carry CORS headers) → RequestId → BodySize → RateLimit → app
api.add_middleware(quotas.RateLimitMiddleware)
api.add_middleware(quotas.BodySizeMiddleware)
api.add_middleware(RequestIdMiddleware)
api.add_middleware(
    CORSMiddleware,
    allow_origins=list({settings.public_web_url, "http://localhost:3000", "http://127.0.0.1:3000"}),
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
)
api.add_middleware(obs.MetricsMiddleware)
obs.setup_tracing(api)


@api.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return error_response(exc)


@api.exception_handler(cloud.AppError)
async def _app_error(_: Request, exc: cloud.AppError):
    return JSONResponse({"error": exc.msg, "detail": exc.msg}, status_code=exc.status)


_HTTP_CODES = {400: BAD_REQUEST, 401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND, 402: QUOTA, 429: SANDBOX_FULL}


@api.exception_handler(HTTPException)
async def _http_error(_: Request, exc: HTTPException):
    code = _HTTP_CODES.get(exc.status_code, BAD_REQUEST if exc.status_code < 500 else INTERNAL)
    return JSONResponse(body(None, code, str(exc.detail)), status_code=exc.status_code)


@api.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    errs = exc.errors()
    first = errs[0] if errs else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    msg = f"{loc}: {first.get('msg', 'invalid')}" if loc else first.get("msg", "invalid request")
    return JSONResponse(body({"errors": [{"loc": e.get("loc"), "msg": e.get("msg")} for e in errs]}, BAD_REQUEST, msg),
                        status_code=422)


@api.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    if type(exc).__name__ == "SandboxFull":
        return JSONResponse(body(None, SANDBOX_FULL, "Too many running sandboxes in this workspace"), status_code=429)
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    obs.capture_exception(exc)
    return JSONResponse(body(None, INTERNAL, "Internal server error"), status_code=500)


@api.get("/api/v1/health")
async def health():
    from oa.core.envelope import ok

    return ok({"status": "ok", "mode": settings.mode})


@api.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    """Prometheus exposition. If OA_METRICS_TOKEN is set, require `Authorization: Bearer <token>`."""
    import os

    from fastapi.responses import Response

    tok = os.environ.get("OA_METRICS_TOKEN")
    if tok and request.headers.get("authorization", "") != f"Bearer {tok}":
        return Response(status_code=401)
    data, ctype = obs.metrics_payload()
    return Response(data, media_type=ctype)


for r in (auth.router, workspaces.router, chats.router, messages.router, versions.router, files.router,
          integrations.router, cloud.router, billing.router, covers.router, automations.router, boost.router):
    api.include_router(r, prefix="/api/v1")
from oa.api import edit_session  # noqa: E402  (visual editing, P9)

api.include_router(edit_session.router, prefix="/api/v1")
api.include_router(internal.router)
api.include_router(cloud.apps)

app = socketio.ASGIApp(sio, other_asgi_app=api, socketio_path="socket.io")
