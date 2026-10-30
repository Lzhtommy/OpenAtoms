"""Observability (ARCHITECTURE §15 / PLAN P8): Prometheus metrics, structured logs, OpenTelemetry, Sentry.

- Metrics are always on (prometheus_client, process-local registry). api serves them at GET /metrics;
  the arq worker exposes them on OA_WORKER_METRICS_PORT.
- OpenTelemetry tracing is enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is set (optional extra `observability`).
- Sentry is enabled only when SENTRY_DSN is set.
- OA_LOG_FORMAT=json switches logs to one JSON object per line, with request_id / run_id / chat_id context.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import REGISTRY, GaugeMetricFamily
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("oa.obs")

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("oa_request_id", default=None)
run_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("oa_run_id", default=None)
chat_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("oa_chat_id", default=None)

# ---------------------------------------------------------------- metrics
_LAT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_RUN_BUCKETS = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 3600)
_START_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 20, 30, 60)

HTTP_REQUESTS = Counter("oa_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("oa_http_request_duration_seconds", "HTTP request latency", ["method", "route"],
                         buckets=_LAT_BUCKETS)
RUNS_STARTED = Counter("oa_runs_started_total", "Agent runs started (resumes excluded)")
RUNS_FINISHED = Counter("oa_runs_finished_total", "Agent runs finished", ["status"])
RUN_DURATION = Histogram("oa_run_duration_seconds", "Agent run wall time (start → end/suspend)", ["status"],
                         buckets=_RUN_BUCKETS)
SANDBOX_START = Histogram("oa_sandbox_start_seconds", "Sandbox start latency (until sandboxd healthy + dev started)",
                          ["provider", "kind"], buckets=_START_BUCKETS)
SANDBOX_START_FAILURES = Counter("oa_sandbox_start_failures_total", "Sandbox start failures", ["provider"])
POOL_EVENTS = Counter("oa_sandbox_pool_events_total", "Warm pool events", ["event"])
LLM_TOKENS = Counter("oa_llm_tokens_total", "LLM tokens", ["model", "direction"])
LLM_COST = Counter("oa_llm_cost_usd_total", "LLM cost in USD", ["model"])
CREDITS = Counter("oa_credits_charged_total", "Credits charged", ["reason"])
RATE_LIMITED = Counter("oa_rate_limited_total", "Requests rejected by rate limiting", ["bucket"])
QUOTA_BLOCKS = Counter("oa_quota_blocks_total", "Runs blocked by quotas", ["reason"])
ABUSE_ACTIONS = Counter("oa_abuse_actions_total", "Sandbox abuse monitor actions", ["reason", "action"])
MODERATION_BLOCKS = Counter("oa_moderation_blocks_total", "Prompts blocked by moderation", ["category"])
EVENTS_EMITTED = Counter("oa_events_emitted_total", "Realtime events emitted", ["event"])
DISK_BYTES = Gauge("oa_workspace_disk_bytes_max", "Largest measured workspace size (bytes)")

_run_started_at: dict[tuple[str, Any], float] = {}


class _LiveCollector:
    """Gauges computed at scrape time from in-process state."""

    def collect(self):
        g = GaugeMetricFamily("oa_socket_connections", "Connected Socket.IO clients (this process)")
        try:
            from oa.realtime.server import sio

            g.add_metric([], len(getattr(sio.eio, "sockets", {}) or {}))
        except Exception:
            g.add_metric([], 0)
        yield g
        s = GaugeMetricFamily("oa_sandboxes", "Sandboxes by status (last refresh)", labels=["status"])
        for status, n in _sandbox_counts.items():
            s.add_metric([status], n)
        yield s
        p = GaugeMetricFamily("oa_sandbox_pool_ready", "Ready warm-pool slots (this process' view)")
        try:
            from oa.sandbox import pool

            p.add_metric([], pool.ready_count_cached())
        except Exception:
            p.add_metric([], 0)
        yield p
        r = GaugeMetricFamily("oa_runs_active", "Agent runs currently executing (this process)")
        r.add_metric([], len(_run_started_at))
        yield r


_sandbox_counts: dict[str, int] = {}
_collector_registered = False


def register_collectors() -> None:
    global _collector_registered
    if not _collector_registered:
        REGISTRY.register(_LiveCollector())
        _collector_registered = True


async def refresh_sandbox_counts() -> None:
    from sqlalchemy import func, select

    from oa.db.models import Sandbox
    from oa.db.session import SessionLocal

    async with SessionLocal() as s:
        rows = (await s.execute(select(Sandbox.status, func.count()).group_by(Sandbox.status))).all()
    _sandbox_counts.clear()
    _sandbox_counts.update({st: int(n) for st, n in rows})


def on_event(chat_id: str, event: str, payload: dict[str, Any]) -> None:
    """EventBus listener: derive run metrics from run:start / run:end without touching the runner."""
    EVENTS_EMITTED.labels(event).inc()
    key = (chat_id, payload.get("run_id"))
    if event == "run:start":
        if not payload.get("resumed"):
            RUNS_STARTED.inc()
        _run_started_at[key] = time.monotonic()
    elif event == "run:end":
        status = str(payload.get("status") or "unknown")
        RUNS_FINISHED.labels(status).inc()
        t0 = _run_started_at.pop(key, None)
        if t0 is None:  # tolerate run:start without run_id
            t0 = _run_started_at.pop((chat_id, None), None)
        if t0 is not None:
            RUN_DURATION.labels(status).observe(time.monotonic() - t0)


def record_usage(model: str, input_tokens: int, output_tokens: int, cost_usd: float, credits: int) -> None:
    LLM_TOKENS.labels(model, "input").inc(max(0, input_tokens))
    LLM_TOKENS.labels(model, "output").inc(max(0, output_tokens))
    LLM_COST.labels(model).inc(max(0.0, cost_usd))
    CREDITS.labels("llm").inc(max(0, credits))


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


class MetricsMiddleware:
    """Per-route latency/status. Route = matched path template (bounded cardinality)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
            await send(msg)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = _route_template(scope)
            if route not in ("/metrics",):
                HTTP_LATENCY.labels(scope["method"], route).observe(time.perf_counter() - t0)
                HTTP_REQUESTS.labels(scope["method"], route, str(status["code"])).inc()


def _route_template(scope: Scope) -> str:
    """/api/v1/chats/abc/messages → /api/v1/chats/{chat_id}/messages (bounded label cardinality)."""
    path = scope.get("path", "")
    if scope.get("route") is None:
        return _bucket_path(path)
    for k, v in (scope.get("path_params") or {}).items():
        sv = str(v)
        if sv:
            path = path.replace("/" + sv, "/{" + k + "}", 1)
    return path


def _bucket_path(path: str) -> str:
    # unmatched routes: keep cardinality bounded
    for prefix in ("/apps/api/", "/internal/", "/api/v1/", "/socket.io"):
        if path.startswith(prefix):
            return prefix + "*"
    return "other"


# ---------------------------------------------------------------- logging
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, var in (("request_id", request_id_var), ("run_id", run_id_var), ("chat_id", chat_id_var)):
            v = var.get()
            if v is not None:
                out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging() -> None:
    fmt = os.environ.get("OA_LOG_FORMAT", "text").lower()
    root = logging.getLogger()
    if fmt == "json":
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        root.handlers[:] = [h]
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "arq"):
            lg = logging.getLogger(name)
            lg.handlers[:] = []
            lg.propagate = True
    elif not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(os.environ.get("OA_LOG_LEVEL", "INFO").upper())


# ---------------------------------------------------------------- tracing / error reporting
_tracing_on = False


def setup_tracing(app: Any = None, service: str = "openatoms-api") -> bool:
    """Enable OTel when OTEL_EXPORTER_OTLP_ENDPOINT is set. Returns True if enabled."""
    global _tracing_on
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL_EXPORTER_OTLP_ENDPOINT set but opentelemetry is not installed (extra 'observability')")
        return False
    if not _tracing_on:
        provider = TracerProvider(resource=Resource.create({
            "service.name": os.environ.get("OTEL_SERVICE_NAME", service),
            "deployment.environment": os.environ.get("OA_ENV", "dev"),
        }))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        HTTPXClientInstrumentor().instrument()
        _tracing_on = True
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics,/api/v1/health",
                                           server_request_hook=_otel_request_hook)
    log.info("OpenTelemetry tracing enabled → %s", os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])
    return True


def _otel_request_hook(span: Any, scope: dict[str, Any]) -> None:
    if span is None or not span.is_recording():
        return
    rid = (scope.get("state") or {}).get("request_id")
    if rid:
        span.set_attribute("oa.request_id", rid)
    path = scope.get("path", "")
    if path.startswith("/api/v1/chats/"):
        parts = path.split("/")
        if len(parts) > 4:
            span.set_attribute("oa.chat_id", parts[4])


def set_span_attribute(key: str, value: Any) -> None:
    """Tag the active span (e.g. from RequestIdMiddleware, which runs inside the OTel ASGI middleware)."""
    if not _tracing_on:
        return
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute(key, value)
    except Exception:
        pass


def tracer():
    """Tracer usable unconditionally (no-op when OTel is off or missing)."""
    try:
        from opentelemetry import trace

        return trace.get_tracer("oa")
    except ImportError:
        return _NoopTracer()


class _NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def set_attribute(self, *a: object) -> None:
        return None

    def record_exception(self, *a: object) -> None:
        return None


class _NoopTracer:
    def start_as_current_span(self, *a: object, **k: object) -> _NoopSpan:
        return _NoopSpan()


def setup_sentry(component: str = "api") -> bool:
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning("SENTRY_DSN set but sentry-sdk is not installed (extra 'observability')")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("OA_ENV", "dev"),
        release=os.environ.get("OA_RELEASE"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
        send_default_pii=False,
    )
    sentry_sdk.set_tag("component", component)
    log.info("Sentry enabled (%s)", component)
    return True


def capture_exception(exc: BaseException) -> None:
    try:
        import sentry_sdk

        if sentry_sdk.is_initialized():
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass
