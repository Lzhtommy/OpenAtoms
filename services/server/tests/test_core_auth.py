from __future__ import annotations

from tests.conftest import register_user


async def test_register_login_refresh_detail(client):
    u = await register_user(client, name="Ada")
    assert u["user"]["current_workspace_id"]
    r = await client.get("/api/v1/user/detail", headers=u["headers"])
    body = r.json()
    assert body["code"] == 0 and body["data"]["name"] == "Ada" and "now_ts" in body

    # Bearer prefix accepted as well
    r = await client.get("/api/v1/user/detail", headers={"Authorization": f"Bearer {u['token']}"})
    assert r.status_code == 200

    r = await client.post("/api/v1/user/login", json={"email": u["email"], "password": u["password"]})
    assert r.status_code == 200 and r.json()["data"]["access_token"]
    assert "oa_refresh" in r.cookies or "oa_refresh" in client.cookies

    r = await client.post("/api/v1/user/refresh")
    assert r.status_code == 200 and r.json()["data"]["access_token"]

    r = await client.patch("/api/v1/user/detail", headers=u["headers"], json={"name": "Ada L"})
    assert r.json()["data"]["name"] == "Ada L"


async def test_auth_errors(client):
    u = await register_user(client)
    r = await client.post("/api/v1/user/register", json={"email": u["email"], "password": "secret123"})
    assert r.status_code == 409 and r.json()["code"] == 4409
    r = await client.post("/api/v1/user/login", json={"email": u["email"], "password": "wrong-pass"})
    assert r.status_code == 401
    r = await client.get("/api/v1/user/detail")
    assert r.status_code == 401 and r.json()["code"] == 4401
    r = await client.get("/api/v1/user/detail", headers={"Authorization": "garbage"})
    assert r.json()["code"] == 4401
    r = await client.post("/api/v1/user/register", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422 and r.json()["code"] == 4400


async def test_password_reset_and_verification(client, data_dir):
    u = await register_user(client)
    # verification mail landed in the outbox
    assert any(data_dir.joinpath("outbox").iterdir())
    from sqlalchemy import select

    from oa.db import models as m
    from oa.db.session import SessionLocal

    async with SessionLocal() as s:
        tok = (await s.execute(select(m.AuthToken).where(m.AuthToken.kind == "verify_email",
                                                         m.AuthToken.user_id == u["user"]["id"]))).scalar_one()
    r = await client.post("/api/v1/user/activation", json={"token": tok.token})
    assert r.json()["data"]["email_verified"] is True
    r = await client.post("/api/v1/user/activation", json={"token": tok.token})
    assert r.json()["code"] == 4400  # single use

    r = await client.post("/api/v1/user/pwd/forgot", json={"email": u["email"]})
    dev_token = r.json()["data"]["dev_token"]
    r = await client.post("/api/v1/user/pwd/reset", json={"token": dev_token, "password": "newpass123"})
    assert r.json()["code"] == 0
    r = await client.post("/api/v1/user/login", json={"email": u["email"], "password": "newpass123"})
    assert r.status_code == 200
    # unknown email does not leak
    r = await client.post("/api/v1/user/pwd/forgot", json={"email": "nobody@example.com"})
    assert r.json()["code"] == 0 and "dev_token" not in r.json()["data"]


async def test_google_not_configured(client):
    r = await client.get("/api/v1/user/google/login")
    assert r.json()["code"] == 4400


async def test_request_id_echo(client):
    r = await client.get("/api/v1/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["x-request-id"] == "abc123"
