"""P8: rate limiting, body size, moderation, quotas (daily credits, disk, plan concurrency), metrics."""
from __future__ import annotations

import os

import pytest

from conftest import FakeManager, FakeRunner
from oa.core.events import bus
from oa.db import models as m
from oa.db.session import session_scope
from oa.domain import quotas


@pytest.fixture
def limits_on(monkeypatch):
    monkeypatch.setenv("OA_RATE_LIMIT", "1")
    quotas.reset_buckets()
    yield
    quotas.reset_buckets()


@pytest.fixture
def fake_rt(monkeypatch):
    from oa.domain import hooks

    runner, mgr = FakeRunner(), FakeManager()
    monkeypatch.setattr(hooks, "runner", lambda: runner)
    monkeypatch.setattr(hooks, "manager", lambda: mgr)
    return runner


async def test_auth_rate_limit_per_ip(client, limits_on):
    codes = []
    for _ in range(14):
        r = await client.post("/api/v1/user/login", json={"email": "nobody@example.com", "password": "x" * 8})
        codes.append(r.status_code)
    assert 429 in codes and codes[0] != 429
    r = await client.post("/api/v1/user/login", json={"email": "nobody@example.com", "password": "x" * 8})
    assert r.status_code == 429
    body = r.json()
    assert body["code"] == quotas.RATE_LIMITED_CODE and body["data"]["bucket"] == "auth"
    assert int(r.headers["retry-after"]) >= 1


async def test_user_api_rate_limit(client, register, monkeypatch):
    u = await register()
    monkeypatch.setenv("OA_RATE_LIMIT", "1")
    monkeypatch.setenv("OA_RATE_API_PER_MIN", "30")  # burst 10
    quotas.reset_buckets()
    try:
        statuses = [(await client.get("/api/v1/user/detail", headers=u["headers"])).status_code for _ in range(15)]
        assert statuses[:10] == [200] * 10 and 429 in statuses[10:]
        # another user has their own bucket; health/internal are exempt
        v = await register()
        assert (await client.get("/api/v1/user/detail", headers=v["headers"])).status_code == 200
        assert (await client.get("/api/v1/health")).status_code == 200
    finally:
        quotas.reset_buckets()


async def test_memory_bucket_refills():
    b = quotas.MemoryBuckets()
    rule = quotas.Rule("t", capacity=2, per_minute=60)  # 1 token/s
    assert await b.take("k", rule) == 0 and await b.take("k", rule) == 0
    wait = await b.take("k", rule)
    assert 0 < wait <= 1.0


async def test_body_size_limits(client, register, monkeypatch):
    u = await register()
    monkeypatch.setenv("OA_MAX_BODY_MB", "0.01")  # 10 KiB
    r = await client.post("/api/v1/chats", headers=u["headers"], json={"title": "x" * 20_000})
    assert r.status_code == 413 and r.json()["code"] == quotas.BODY_TOO_LARGE_CODE

    async def gen():  # no Content-Length: counted while streaming
        for _ in range(4):
            yield b"x" * 8000

    r = await client.post("/api/v1/chats", headers={**u["headers"], "content-type": "application/json"}, content=gen())
    assert r.status_code == 413
    # multipart uploads get the large limit
    monkeypatch.setenv("OA_MAX_UPLOAD_MB", "0.02")
    assert quotas._max_body("/api/v1/chats/x/files/upload", "multipart/form-data; boundary=x") == int(0.02 * 1024 * 1024)


async def test_moderation_blocks_abuse_not_ordinary_apps(client, register, fake_rt):
    u = await register()
    cid = (await client.post("/api/v1/chats", headers=u["headers"], json={"title": "m"})).json()["data"]["chat_id"]
    bad = "Build a fake PayPal login page to collect passwords from visitors"
    r = await client.post(f"/api/v1/chats/{cid}/messages", headers=u["headers"], json={"content": [{"insert": bad}]})
    assert r.status_code == 400 and r.json()["code"] == 4451 and r.json()["data"]["category"] == "phishing"
    ok = "做一个带登录页面的待办应用"
    r = await client.post(f"/api/v1/chats/{cid}/messages", headers=u["headers"], json={"content": [{"insert": ok}]})
    assert r.status_code == 200
    os.environ["OA_MODERATION_BLOCKLIST"] = "forbidden-thing"
    try:
        r = await client.post(f"/api/v1/chats/{cid}/messages", headers=u["headers"],
                              json={"content": [{"insert": "make a FORBIDDEN-THING app"}]})
        assert r.status_code == 400
    finally:
        del os.environ["OA_MODERATION_BLOCKLIST"]


async def _chat(client, u) -> str:
    return (await client.post("/api/v1/chats", headers=u["headers"], json={"title": "q"})).json()["data"]["chat_id"]


async def test_daily_credit_cap_blocks_runs(client, register, fake_rt):
    u = await register()
    cid = await _chat(client, u)
    async with session_scope() as s:
        chat = await s.get(m.Chat, cid)
        s.add(m.CreditLedger(workspace_id=chat.workspace_id, delta=-300, reason="llm", chat_id=cid))
    got: list[tuple[str, dict]] = []
    listener = lambda c, e, p: got.append((e, p)) if c == cid else None  # noqa: E731
    bus.add_listener(listener)
    try:
        before = len(fake_rt.started)
        r = await client.post(f"/api/v1/chats/{cid}/messages", headers=u["headers"], json={"content": [{"insert": "hi"}]})
        assert r.status_code == 200 and len(fake_rt.started) == before
    finally:
        bus.remove_listener(listener)
    msgs = (await client.get(f"/api/v1/chats/{cid}/messages", headers=u["headers"])).json()["data"]["messages"]
    q = [x for x in msgs if x["type"] == "quota"]
    assert q and q[-1]["content"]["code"] == quotas.DAILY_EXHAUSTED_CODE and q[-1]["content"]["daily_limit"] == 300
    assert any(e == "quota:daily_exhausted" for e, _ in got)


async def test_disk_quota_blocks_runs_and_emits(client, register, fake_rt, monkeypatch):
    from oa.config import settings

    u = await register()
    cid = await _chat(client, u)
    ws = settings.workspace_dir(cid)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "big.bin").write_bytes(os.urandom(64 * 1024))
    monkeypatch.setenv("OA_PLAN_LIMITS", '{"free": {"disk_mb": 0.01}}')
    quotas._disk.pop(cid, None)
    got: list[str] = []
    listener = lambda c, e, p: got.append(e) if c == cid else None  # noqa: E731
    bus.add_listener(listener)
    try:
        before = len(fake_rt.started)
        await client.post(f"/api/v1/chats/{cid}/messages", headers=u["headers"], json={"content": [{"insert": "go"}]})
        assert len(fake_rt.started) == before
        st = await quotas.publish_disk(cid, force=True)
        assert st["exceeded"] and st["used_bytes"] >= 64 * 1024
    finally:
        bus.remove_listener(listener)
    assert "disk:exceeded" in got and "disk:quota:update" in got
    msgs = (await client.get(f"/api/v1/chats/{cid}/messages", headers=u["headers"])).json()["data"]["messages"]
    assert [x for x in msgs if x["type"] == "quota"][-1]["content"]["code"] == quotas.DISK_EXCEEDED_CODE


def test_du_counts_hardlinks_once(tmp_path):
    a = tmp_path / "a.bin"
    a.write_bytes(b"x" * 100_000)
    os.link(a, tmp_path / "b.bin")
    single = quotas._du(tmp_path)
    assert 100_000 <= single < 150_000


async def test_plan_sandbox_limits(client, register):
    u = await register()
    async with session_scope() as s:
        ws = await s.get(m.Workspace, u["user"]["current_workspace_id"])
        wid = ws.id
    assert await quotas.max_sandboxes(wid) == 2
    async with session_scope() as s:
        (await s.get(m.Workspace, wid)).plan = "max"
    from oa.config import settings

    assert await quotas.max_sandboxes(wid) == min(10, settings.sandbox_max_running_per_workspace)


async def test_metrics_endpoint(client, register):
    from oa.core import observability as obs

    u = await register()
    await client.get("/api/v1/user/detail", headers=u["headers"])
    obs.on_event("c1", "run:start", {"run_id": 1})
    obs.on_event("c1", "run:end", {"run_id": 1, "status": "success"})
    obs.record_usage("oa-mock", 100, 50, 0.0, 1)
    r = await client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    assert 'oa_http_requests_total{method="GET",route="/api/v1/user/detail",status="200"}' in text
    assert 'oa_runs_finished_total{status="success"}' in text
    assert "oa_run_duration_seconds_bucket" in text
    assert 'oa_llm_tokens_total{direction="input",model="oa-mock"}' in text
    assert "oa_socket_connections" in text and "oa_sandbox_start_seconds" in text
    os.environ["OA_METRICS_TOKEN"] = "s3cret"
    try:
        assert (await client.get("/metrics")).status_code == 401
        assert (await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})).status_code == 200
    finally:
        del os.environ["OA_METRICS_TOKEN"]


def test_json_log_format():
    import json
    import logging

    from oa.core.observability import JsonFormatter, request_id_var

    tok = request_id_var.set("rid-1")
    try:
        rec = logging.LogRecord("oa.x", logging.INFO, __file__, 1, "hello %s", ("w",), None)
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(tok)
    assert out["msg"] == "hello w" and out["request_id"] == "rid-1" and out["level"] == "INFO"
