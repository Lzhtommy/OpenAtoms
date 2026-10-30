from __future__ import annotations

import hashlib
import hmac
import json
import time

from oa.config import settings
from oa.db import models as m
from oa.db.session import SessionLocal
from tests.conftest import register_user


async def test_cloud_entities_auth_storage_ai(client, fake_runtime):
    u = await register_user(client)
    h = u["headers"]
    cid = (await client.post("/api/v1/chats", headers=h, json={"title": "Board"})).json()["data"]["chat_id"]
    envs = (await client.get(f"/api/v1/chats/{cid}/database/environments", headers=h)).json()["data"]
    assert envs == [{"env": "dev", "ready": False}, {"env": "prod", "ready": False}]
    assert (await client.post(f"/api/v1/chats/{cid}/cloud/enable", headers=h)).json()["data"]["cloud_enabled"]

    from oa.domain import cloud

    await cloud.create_table(cid, "dev", "posts", [{"name": "title", "type": "text"}, {"name": "likes", "type": "integer"}])
    assert await cloud.insert_rows(cid, "dev", "posts", [{"title": "a", "likes": 3}, {"title": "b", "likes": 1}]) == 2
    schemas = await cloud.get_schemas(cid, "dev")
    assert schemas[0]["name"] == "posts" and schemas[0]["row_count"] == 2

    app = {"X-OA-App-Chat": cid, "X-OA-App-Env": "dev"}
    rows = (await client.get("/apps/api/v1/entities/posts", headers=app, params={"sort": "-likes"})).json()
    assert [r["title"] for r in rows] == ["a", "b"]
    rows = (await client.get("/apps/api/v1/entities/posts", headers=app,
                             params={"query": json.dumps({"likes": {"$gt": 2}})})).json()
    assert [r["title"] for r in rows] == ["a"]

    # app auth
    reg = (await client.post("/apps/api/v1/auth/register", headers=app, json={"email": "e@x.com", "password": "pw1234"})).json()
    tok = reg["token"]
    authed = {**app, "Authorization": f"Bearer {tok}"}
    assert (await client.get("/apps/api/v1/auth/me", headers=authed)).json()["email"] == "e@x.com"
    assert (await client.get("/apps/api/v1/auth/me", headers=app)).status_code == 401
    assert (await client.get("/apps/api/v1/auth/me", headers={**authed, "X-OA-App-Env": "prod"})).status_code == 401
    login = await client.post("/apps/api/v1/auth/login", headers=app, json={"email": "e@x.com", "password": "pw1234"})
    assert login.status_code == 200
    ex = (await client.post("/apps/api/v1/auth/token/exchange", headers=app, json={"platform_token": u["token"]})).json()
    assert ex["user"]["email"] == u["email"]

    # CRUD with owner + auto-create table
    created = (await client.post("/apps/api/v1/entities/comments", headers=authed, json={"text": "hi", "id": 999})).json()
    assert created["text"] == "hi" and created["owner_id"] == reg["user"]["id"] and created["id"] != 999
    upd = (await client.put(f"/apps/api/v1/entities/comments/{created['id']}", headers=authed, json={"text": "yo"})).json()
    assert upd["text"] == "yo"
    got = (await client.get(f"/apps/api/v1/entities/comments/{created['id']}", headers=app)).json()
    assert got["text"] == "yo"
    batch = (await client.post("/apps/api/v1/entities/comments/batch", headers=app, json=[{"text": "1"}, {"text": "2"}])).json()
    assert len(batch) == 2
    assert len((await client.get("/apps/api/v1/entities/comments/all", headers=app)).json()) == 3
    assert (await client.delete(f"/apps/api/v1/entities/comments/{created['id']}", headers=app)).json() == {"ok": True}
    assert (await client.get(f"/apps/api/v1/entities/comments/{created['id']}", headers=app)).status_code == 404
    assert (await client.get("/apps/api/v1/entities/bad-name", headers=app)).status_code == 400
    assert (await client.get("/apps/api/v1/entities/posts", headers={"X-OA-App-Chat": "f" * 32})).status_code == 404
    # prod is isolated from dev
    assert (await client.get("/apps/api/v1/entities/posts", headers={**app, "X-OA-App-Env": "prod"})).json() == []

    # storage
    up = (await client.post("/apps/api/v1/storage/upload", headers=app, files={"file": ("a.txt", b"hello", "text/plain")})).json()
    assert up["url"].startswith("/api/v1/storage/files/")
    key = up["url"].split("/api/v1/storage/files/")[1]
    assert (await client.get(f"/apps/api/v1/storage/files/{key}", headers=app)).content == b"hello"

    # ai (offline echo stream)
    r = await client.post("/apps/api/v1/aihub/gentxt", headers=app, json={"messages": [{"role": "user", "content": "ping"}]})
    assert "data: [DONE]" in r.text and "ping" in "".join(
        json.loads(line[5:])["content"] for line in r.text.splitlines() if line.startswith("data: {"))

    # management endpoints
    tables = (await client.get(f"/api/v1/chats/{cid}/database/dev/tables", headers=h)).json()["data"]
    assert {t["name"] for t in tables} == {"posts", "comments"}
    rows = (await client.get(f"/api/v1/chats/{cid}/database/dev/tables/posts/rows", headers=h)).json()["data"]
    assert rows["total"] == 2
    csv_text = (await client.get(f"/api/v1/chats/{cid}/database/dev/tables/posts/export", headers=h)).text
    assert csv_text.splitlines()[0].startswith("id,title,likes")
    imp = (await client.post(f"/api/v1/chats/{cid}/database/prod/tables/posts/import", headers=h,
                             files={"file": ("p.csv", b"title,likes\nz,7\n", "text/csv")})).json()["data"]
    assert imp["inserted"] == 1
    prod = (await client.get("/apps/api/v1/entities/posts", headers={**app, "X-OA-App-Env": "prod"})).json()
    assert prod[0]["likes"] == 7


async def test_billing_flow(client, fake_runtime):
    u = await register_user(client)
    h = u["headers"]
    bal = (await client.get("/api/v1/transaction/balance", headers=h)).json()["data"]
    assert bal["credits"] == settings.signup_credits and bal["plan"] == "free"
    plans = (await client.get("/api/v1/billing/plans")).json()["data"]
    assert [p["id"] for p in plans] == ["free", "pro", "max"]

    co = (await client.post("/api/v1/billing/checkout", headers=h, json={"plan": "pro"})).json()["data"]
    assert co["mock"] and "/billing/mock?session=" in co["url"]
    info = (await client.get(f"/api/v1/billing/mock/{co['session']}", headers=h)).json()["data"]
    assert info["status"] == "pending" and info["amount_usd"] == 20
    bal = (await client.post("/api/v1/billing/mock/complete", headers=h, json={"session": co["session"]})).json()["data"]
    assert bal["plan"] == "pro" and bal["credits"] == settings.signup_credits + 5000
    # idempotent
    bal = (await client.post("/api/v1/billing/mock/complete", headers=h, json={"session": co["session"]})).json()["data"]
    assert bal["credits"] == settings.signup_credits + 5000

    cid = (await client.post("/api/v1/chats", headers=h, json={"title": "c"})).json()["data"]["chat_id"]
    from oa.domain import billing

    await billing.charge_usage(cid, None, "Engineer", "oa-mock", 1500, 600)  # 3 credits
    await billing.charge_usage(cid, None, "Engineer", "claude-sonnet-4-5", 100_000, 10_000)  # $0.45 → 45 credits
    bal = (await client.get("/api/v1/transaction/balance", headers=h)).json()["data"]
    assert bal["credits"] == settings.signup_credits + 5000 - 48 and bal["used_this_month"] == 48
    led = (await client.get("/api/v1/transaction/ledger", headers=h)).json()["data"]
    assert [x["reason"] for x in led["items"]][:3] == ["llm", "llm", "subscription"]


async def test_stripe_webhook_signature(client, fake_runtime, monkeypatch):
    u = await register_user(client)
    wid = u["user"]["current_workspace_id"]
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_1", "payment_status": "paid", "amount_total": 1000,
        "metadata": {"workspace_id": str(wid), "kind": "pack", "item": "pack_1000"}}}}
    payload = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac.new(b"whsec_test", f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    bad = await client.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": f"t={ts},v1=deadbeef"})
    assert bad.json()["code"] == 4400
    ok = await client.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": f"t={ts},v1={sig}"})
    assert ok.json()["code"] == 0
    async with SessionLocal() as s:
        assert (await s.get(m.Workspace, wid)).credits == settings.signup_credits + 1000


async def test_agent_provisioning_tables_marks_project_cloud_enabled(client, fake_runtime):
    """An agent that creates tables itself must leave the project in the same state as "Enable Cloud"."""
    from oa.tools.data import _mark_cloud_enabled

    u = await register_user(client)
    h = u["headers"]
    cid = (await client.post("/api/v1/chats", headers=h, json={"title": "Guestbook"})).json()["data"]["chat_id"]
    assert (await client.get(f"/api/v1/chats/{cid}", headers=h)).json()["data"]["config"]["cloud_enabled"] is False
    await _mark_cloud_enabled(cid)
    await _mark_cloud_enabled(cid)  # idempotent
    cfg = (await client.get(f"/api/v1/chats/{cid}", headers=h)).json()["data"]["config"]
    assert cfg["cloud_enabled"] is True and cfg["db_mode"] == "double"
