"""P8: tracing attributes, Sentry wiring (env-gated, optional extra)."""
from __future__ import annotations

import pytest

otel = pytest.importorskip("opentelemetry.sdk.trace")


async def test_request_id_tagged_on_active_span(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from oa.core import observability as obs
    from oa.main import RequestIdMiddleware

    mem = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(mem))
    monkeypatch.setattr(obs, "_tracing_on", True)

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = RequestIdMiddleware(inner)
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b""}

    with provider.get_tracer("t").start_as_current_span("server"):
        await mw({"type": "http", "headers": [(b"x-request-id", b"rid-42")], "path": "/x", "method": "GET"},
                 receive, send)
    span = mem.get_finished_spans()[0]
    assert span.attributes["oa.request_id"] == "rid-42"
    assert (b"x-request-id", b"rid-42") in sent[0]["headers"]


def test_tracing_and_sentry_off_without_env(monkeypatch):
    from oa.core import observability as obs

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert obs.setup_tracing(None) is False
    assert obs.setup_sentry("api") is False
    obs.capture_exception(RuntimeError("no-op without sentry"))  # must not raise
