"""arq worker (OA_QUEUE=arq): `uv run arq oa.worker.WorkerSettings`.

The API enqueues `run_job(run_id)`; events reach browsers through socketio.AsyncRedisManager (write-only)
which oa.core.events.bus uses automatically in full mode. Cancellation is a Redis flag polled by the run.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from oa.config import settings

log = logging.getLogger("oa.worker")


async def run_job(ctx: dict[str, Any], run_id: int) -> None:
    from oa.agents.runner import execute_run

    await execute_run(run_id)


async def reap_job(ctx: dict[str, Any]) -> None:
    from oa.sandbox.manager import reap_idle_once

    await reap_idle_once()


async def startup(ctx: dict[str, Any]) -> None:
    import os

    from oa.core import observability as obs
    from oa.core.events import bus
    from oa.db.session import init_db
    from oa.sandbox import abuse, pool

    obs.setup_logging()
    obs.setup_sentry("worker")
    obs.setup_tracing(None, service="openatoms-worker")
    obs.register_collectors()
    port = int(os.environ.get("OA_WORKER_METRICS_PORT", "9108"))
    if port:
        from prometheus_client import start_http_server

        start_http_server(port)  # /metrics for this worker process
    await init_db()
    import oa.tools  # noqa: F401  register tools

    bus.add_listener(obs.on_event)
    ctx["reaper"] = asyncio.create_task(_reaper())
    # with OA_QUEUE=arq the worker owns sandbox lifecycle helpers: warm pool + abuse monitor
    ctx["pool"] = asyncio.create_task(pool.maintain_loop())
    ctx["abuse"] = asyncio.create_task(abuse.monitor_loop())


async def _reaper() -> None:
    from oa.sandbox.manager import reaper_loop

    await reaper_loop()


async def shutdown(ctx: dict[str, Any]) -> None:
    for name in ("reaper", "pool", "abuse"):
        t = ctx.get(name)
        if t:
            t.cancel()


async def automations_job(ctx: dict[str, Any]) -> None:
    """Fire due automations (compare-and-set claims make concurrent workers safe)."""
    from oa.domain.automations import tick

    await tick()


class WorkerSettings:
    functions = [run_job, reap_job]
    cron_jobs = [cron(automations_job, second={0, 30}, run_at_startup=True, unique=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 20
    job_timeout = 3 * 3600
    keep_result = 60
    allow_abort_jobs = True
