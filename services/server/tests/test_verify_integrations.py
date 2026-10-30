"""Integrations against fake provider servers speaking the exact HTTP APIs we call (no mocking of our code).

A real uvicorn server impersonates GitHub (OAuth + REST), Supabase (management API + OAuth), Stripe
(Checkout) and Google (OAuth token + userinfo); a real `git http-backend` serves smart-HTTP for push/pull.
Our API is driven end-to-end: connect → callback → encrypted tokens → repo create/push/pull round trip,
Supabase link writes .env, Stripe checkout → signed webhook → ledger/plan (idempotent), Google login.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import shutil
import socket
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from oa.config import settings
from oa.db import models as m
from oa.db.session import session_scope
from oa.domain import gitops

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

GH_TOKEN = "gho_" + "x" * 36
SB_TOKEN = "sbp_" + "y" * 40
STRIPE_KEY = "sk_test_" + "z" * 24
WEBHOOK_SECRET = "whsec_test_secret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ fake providers
class Fake:
    def __init__(self, git_root: Path) -> None:
        self.git_root = git_root
        self.stripe_sessions: dict[str, dict] = {}
        self.google_users: dict[str, dict] = {}
        self.calls: list[str] = []


def build_fake(f: Fake) -> FastAPI:
    app = FastAPI()

    def bearer(req: Request) -> str:
        return req.headers.get("authorization", "").removeprefix("Bearer ").strip()

    @app.middleware("http")
    async def log(req: Request, call_next):
        f.calls.append(f"{req.method} {req.url.path}")
        return await call_next(req)

    # GitHub
    @app.post("/login/oauth/access_token")
    async def gh_token(req: Request):
        form = dict(parse_qs((await req.body()).decode()))
        if form.get("code") == ["gh-code"] and form.get("client_id") == ["gh-client"] and form.get("client_secret") == ["gh-secret"]:
            return {"access_token": GH_TOKEN, "token_type": "bearer", "scope": "repo"}
        return {"error": "bad_verification_code"}  # GitHub answers 200 with an error body

    @app.get("/user")
    async def gh_user(req: Request):
        if bearer(req) != GH_TOKEN:
            return JSONResponse({"message": "Bad credentials"}, 401)
        return {"login": "octo", "name": "Octo Cat", "avatar_url": "https://avatars.example/octo"}

    @app.post("/user/repos")
    async def gh_create(req: Request):
        if bearer(req) != GH_TOKEN:
            return JSONResponse({"message": "Bad credentials"}, 401)
        body = await req.json()
        bare = f.git_root / "octo" / f"{body['name']}.git"
        if bare.exists():
            return JSONResponse({"message": "name already exists on this account"}, 422)
        bare.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        subprocess.run(["git", "--git-dir", str(bare), "config", "http.receivepack", "true"], check=True)
        return JSONResponse({"full_name": f"octo/{body['name']}", "private": body.get("private", True),
                             "html_url": f"https://github.example/octo/{body['name']}"}, 201)

    # Supabase
    @app.post("/v1/oauth/token")
    async def sb_token(req: Request):
        auth = base64.b64decode(req.headers.get("authorization", "Basic ").split(" ", 1)[1] or b"").decode()
        form = dict(parse_qs((await req.body()).decode()))
        if auth != "sb-client:sb-secret" or form.get("code") != ["sb-code"]:
            return JSONResponse({"error": "invalid_grant"}, 400)
        return {"access_token": SB_TOKEN, "refresh_token": "r", "expires_in": 3600}

    @app.get("/v1/organizations")
    async def sb_orgs(req: Request):
        if bearer(req) != SB_TOKEN:
            return JSONResponse({"message": "Unauthorized"}, 401)
        return [{"id": "org_1", "name": "Acme"}]

    @app.get("/v1/projects/{ref}/api-keys")
    async def sb_keys(ref: str, req: Request):
        if bearer(req) != SB_TOKEN:
            return JSONResponse({"message": "Unauthorized"}, 401)
        return [{"name": "anon", "api_key": f"anon-{ref}"}, {"name": "service_role", "api_key": f"service-{ref}"}]

    # Stripe
    @app.post("/v1/checkout/sessions")
    async def stripe_session(req: Request):
        auth = base64.b64decode(req.headers.get("authorization", "Basic ").split(" ", 1)[1] or b"").decode()
        if auth != f"{STRIPE_KEY}:":
            return JSONResponse({"error": {"message": "Invalid API Key"}}, 401)
        form = {k: v[0] for k, v in parse_qs((await req.body()).decode()).items()}
        assert form["mode"] == "payment" and int(form["line_items[0][price_data][unit_amount]"]) > 0
        sid = f"cs_test_{uuid.uuid4().hex[:16]}"
        sess = {"id": sid, "object": "checkout.session", "url": f"https://checkout.stripe.example/c/pay/{sid}",
                "amount_total": int(form["line_items[0][price_data][unit_amount]"]), "payment_status": "unpaid",
                "metadata": {k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")},
                "client_reference_id": form.get("client_reference_id")}
        f.stripe_sessions[sid] = sess
        return sess

    # Google
    @app.post("/google/token")
    async def g_token(req: Request):
        form = {k: v[0] for k, v in parse_qs((await req.body()).decode()).items()}
        user = f.google_users.get(form.get("code", ""))
        if not user or form.get("client_secret") != "g-secret" or form.get("grant_type") != "authorization_code":
            return JSONResponse({"error": "invalid_grant"}, 400)
        return {"access_token": f"ya29.{form['code']}", "token_type": "Bearer", "expires_in": 3599}

    @app.get("/google/userinfo")
    async def g_userinfo(req: Request):
        code = bearer(req).removeprefix("ya29.")
        if code not in f.google_users:
            return JSONResponse({"error": "invalid_token"}, 401)
        return f.google_users[code]

    return app


class GitHTTP(BaseHTTPRequestHandler):
    """Smart-HTTP via `git http-backend` with Basic auth (x-access-token:<token>), like github.com."""

    root: Path

    def _serve(self) -> None:
        want = "Basic " + base64.b64encode(f"x-access-token:{GH_TOKEN}".encode()).decode()
        if self.headers.get("Authorization") != want:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="fake-github"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        url = urlparse(self.path)
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            body = b""
            while True:
                size = int(self.rfile.readline().strip(), 16)
                if size == 0:
                    self.rfile.readline()
                    break
                body += self.rfile.read(size)
                self.rfile.readline()
        else:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_PROJECT_ROOT": str(self.root),
               "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": url.path, "QUERY_STRING": url.query,
               "REQUEST_METHOD": self.command, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
               "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": "octo", "REMOTE_ADDR": "127.0.0.1",
               "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
               "GIT_PROTOCOL": self.headers.get("Git-Protocol", "")}
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status = 200
        headers = []
        for line in head.decode().split("\r\n"):
            k, _, v = line.partition(": ")
            if k.lower() == "status":
                status = int(v.split()[0])
            elif k:
                headers.append((k, v))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _serve

    def log_message(self, *a) -> None:
        pass


@pytest.fixture(scope="module")
def providers(tmp_path_factory):
    root = tmp_path_factory.mktemp("git-remote")
    fake = Fake(root)
    api_port = _free_port()
    server = uvicorn.Server(uvicorn.Config(build_fake(fake), host="127.0.0.1", port=api_port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    handler = type("H", (GitHTTP,), {"root": root})
    git_srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=git_srv.serve_forever, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    fake.base = f"http://127.0.0.1:{api_port}"
    fake.git_base = f"http://127.0.0.1:{git_srv.server_address[1]}"
    yield fake
    server.should_exit = True
    git_srv.shutdown()


@pytest.fixture
def configured(providers, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for k, v in {
        "github_client_id": "gh-client", "github_client_secret": "gh-secret",
        "github_api_url": providers.base, "github_oauth_url": providers.base, "github_git_url": providers.git_base,
        "supabase_client_id": "sb-client", "supabase_client_secret": "sb-secret", "supabase_api_url": providers.base,
        "stripe_secret": STRIPE_KEY, "stripe_webhook_secret": WEBHOOK_SECRET, "stripe_api_url": providers.base,
        "google_client_id": "g-client", "google_client_secret": "g-secret",
        "google_auth_url": providers.base + "/google/auth", "google_token_url": providers.base + "/google/token",
        "google_userinfo_url": providers.base + "/google/userinfo",
    }.items():
        monkeypatch.setattr(settings, k, v)
    return providers


async def _workspace_chat(client, h) -> str:
    cid = (await client.post("/api/v1/chats", headers=h, json={"title": "gh"})).json()["data"]["chat_id"]
    ws = settings.workspace_dir(cid)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "index.html").write_text("<h1>hello from OpenAtoms</h1>\n")
    await gitops.ensure_repo(ws)
    await gitops.commit_all(ws, "init")
    return cid


async def _oauth(client, h, provider: str, code: str) -> None:
    r = (await client.get(f"/api/v1/integrations/{provider}/connect", headers=h)).json()
    assert r["code"] == 0, r
    url = urlparse(r["data"]["url"])
    q = parse_qs(url.query)
    assert q["client_id"] and q["redirect_uri"][0].endswith(f"/api/v1/integrations/{provider}/callback")
    cb = await client.get(f"/api/v1/integrations/{provider}/callback", params={"code": code, "state": q["state"][0]})
    assert cb.status_code in (302, 307) and f"connected={provider}" in cb.headers["location"], cb.text
    # state is single-use
    again = await client.get(f"/api/v1/integrations/{provider}/callback", params={"code": code, "state": q["state"][0]})
    assert again.status_code >= 400


async def test_github_oauth_repo_push_pull_roundtrip(client, register, configured, tmp_path):
    u = await register()
    h = u["headers"]
    await _oauth(client, h, "github", "gh-code")
    integ = {i["provider"]: i for i in (await client.get("/api/v1/integrations", headers=h)).json()["data"]}
    assert integ["github"]["connected"] and integ["github"]["account"]["login"] == "octo"
    async with session_scope() as s:
        row = (await s.execute(select(m.Integration).where(m.Integration.provider == "github",
                                                           m.Integration.user_id == u["user"]["id"]))).scalar_one()
    assert GH_TOKEN not in row.tokens_enc  # encrypted at rest

    cid = await _workspace_chat(client, h)
    repo = (await client.post(f"/api/v1/chats/{cid}/github/repo", headers=h, json={"name": "my-app"})).json()
    assert repo["code"] == 0 and repo["data"]["repo"] == "octo/my-app", repo
    push = (await client.post(f"/api/v1/chats/{cid}/github/push", headers=h, json={"message": "first push"})).json()
    assert push["code"] == 0, push
    bare = configured.git_root / "octo" / "my-app.git"
    log = subprocess.run(["git", "--git-dir", str(bare), "log", "--format=%s", "main"], capture_output=True, text=True).stdout
    assert "init" in log
    show = subprocess.run(["git", "--git-dir", str(bare), "show", "main:index.html"], capture_output=True, text=True).stdout
    assert "hello from OpenAtoms" in show

    # someone pushes to GitHub directly → our pull brings it into the workspace
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    (clone / "README.md").write_text("# edited on GitHub\n")
    for cmd in (["add", "-A"], ["-c", "user.email=a@b.c", "-c", "user.name=A", "commit", "-qm", "remote edit"],
                ["push", "-q", "origin", "HEAD:main"]):
        subprocess.run(["git", "-C", str(clone), *cmd], check=True)
    pull = (await client.post(f"/api/v1/chats/{cid}/github/pull", headers=h)).json()
    assert pull["code"] == 0 and GH_TOKEN not in json.dumps(pull), pull
    assert (settings.workspace_dir(cid) / "README.md").read_text() == "# edited on GitHub\n"
    leaked = [str(p) for p in (settings.workspace_dir(cid) / ".git").rglob("*")
              if p.is_file() and GH_TOKEN.encode() in p.read_bytes()]
    assert not leaked, leaked  # credentials never persisted in the repo (config, FETCH_HEAD, reflogs)

    # bad token surfaces a clean error with the token redacted
    async with session_scope() as s:
        row = (await s.execute(select(m.Integration).where(m.Integration.id == row.id))).scalar_one()
        from oa.core import security

        row.tokens_enc = security.encrypt(json.dumps({"access_token": "gho_revoked"}))
    bad = (await client.post(f"/api/v1/chats/{cid}/github/push", headers=h, json={})).json()
    assert bad["code"] == 4400 and "gho_revoked" not in bad["message"]


async def test_github_manual_token_and_bad_code(client, register, configured):
    h = (await register())["headers"]
    r = (await client.post("/api/v1/integrations/github/token", headers=h, json={"token": "nope-nope"})).json()
    assert r["code"] == 4400
    r = (await client.post("/api/v1/integrations/github/token", headers=h, json={"token": GH_TOKEN})).json()
    assert r["code"] == 0 and r["data"]["account"]["login"] == "octo"
    st = parse_qs(urlparse((await client.get("/api/v1/integrations/github/connect", headers=h)).json()["data"]["url"]).query)
    cb = await client.get("/api/v1/integrations/github/callback", params={"code": "wrong", "state": st["state"][0]})
    assert cb.json()["code"] == 4400


async def test_supabase_oauth_and_link_writes_env(client, register, configured):
    h = (await register())["headers"]
    await _oauth(client, h, "supabase", "sb-code")
    integ = {i["provider"]: i for i in (await client.get("/api/v1/integrations", headers=h)).json()["data"]}
    assert integ["supabase"]["account"]["organizations"] == [{"id": "org_1", "name": "Acme"}]
    cid = await _workspace_chat(client, h)
    r = (await client.post(f"/api/v1/chats/{cid}/supabase/link", headers=h, json={"project_ref": "abcdefgh"})).json()
    assert r["code"] == 0 and r["data"]["url"] == "https://abcdefgh.supabase.co"
    env = (settings.workspace_dir(cid) / ".env").read_text()
    assert "VITE_SUPABASE_URL=https://abcdefgh.supabase.co" in env and "VITE_SUPABASE_ANON_KEY=anon-abcdefgh" in env
    assert "service-abcdefgh" not in env  # never leak the service role key into the client bundle
    chat = (await client.get(f"/api/v1/chats/{cid}", headers=h)).json()["data"]
    assert chat["config"]["supabase"]["project_ref"] == "abcdefgh"


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


async def test_stripe_checkout_signed_webhook(client, register, configured):
    u = await register()
    h = u["headers"]
    before = (await client.get("/api/v1/transaction/balance", headers=h)).json()["data"]
    co = (await client.post("/api/v1/billing/checkout", headers=h, json={"plan": "pro"})).json()
    assert co["code"] == 0 and co["data"]["url"].startswith("https://checkout.stripe.example/"), co
    sess = configured.stripe_sessions[co["data"]["session"]]
    assert sess["metadata"]["kind"] == "plan" and sess["metadata"]["item"] == "pro"
    # mock checkout is disabled once Stripe is configured
    assert (await client.post("/api/v1/billing/mock/complete", headers=h, json={"session": "mock_x"})).json()["code"] == 4403

    event = {"id": "evt_1", "type": "checkout.session.completed",
             "data": {"object": {**sess, "payment_status": "paid"}}}
    payload = json.dumps(event).encode()
    bad = await client.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sign(payload, "whsec_wrong")})
    assert bad.json()["code"] == 4400
    stale = await client.post("/api/v1/billing/webhook", content=payload,
                              headers={"Stripe-Signature": _sign(payload, ts=int(time.time()) - 3600)})
    assert stale.json()["code"] == 4400
    for _ in range(2):  # Stripe retries deliveries; fulfilment must be idempotent
        r = await client.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sign(payload)})
        assert r.json()["code"] == 0
    after = (await client.get("/api/v1/transaction/balance", headers=h)).json()["data"]
    assert after["plan"] == "pro"
    ledger = (await client.get("/api/v1/transaction/ledger", headers=h)).json()["data"]["items"]
    subs = [x for x in ledger if x["reason"] == "subscription" and x["ref"] == sess["id"]]
    assert len(subs) == 1 and after["credits"] == before["credits"] + subs[0]["delta"]


async def test_google_login_create_link_and_unverified(client, register, configured):
    async def login(code: str):
        r = (await client.get("/api/v1/user/google/login")).json()
        q = parse_qs(urlparse(r["data"]["url"]).query)
        assert r["data"]["url"].startswith(configured.base + "/google/auth") and q["client_id"] == ["g-client"]
        return await client.get("/api/v1/user/google/callback", params={"code": code, "state": q["state"][0]})

    configured.google_users["new"] = {"sub": "g-1", "email": "new.person@gmail.example", "email_verified": True,
                                      "name": "New Person", "picture": "https://pics.example/p.png"}
    r = await login("new")
    assert r.status_code in (302, 307)
    loc = urlparse(r.headers["location"])
    token = parse_qs(loc.query)["token"][0]
    assert loc.path == "/oauth/google" and "oa_refresh=" in r.headers.get("set-cookie", "")
    me = (await client.get("/api/v1/user/detail", headers={"Authorization": token})).json()["data"]
    assert me["email"] == "new.person@gmail.example" and me["email_verified"] and me["avatar"]

    existing = await register(email="has.password@gmail.example")
    configured.google_users["link"] = {"sub": "g-2", "email": "Has.Password@gmail.example", "email_verified": True}
    r = await login("link")
    token = parse_qs(urlparse(r.headers["location"]).query)["token"][0]
    me = (await client.get("/api/v1/user/detail", headers={"Authorization": token})).json()["data"]
    assert me["id"] == existing["user"]["id"]

    await register(email="victim@gmail.example")
    configured.google_users["evil"] = {"sub": "g-3", "email": "victim@gmail.example", "email_verified": False}
    r = await login("evil")
    assert r.status_code == 400 and r.json()["code"] == 4400  # no takeover via unverified Google email
    r = await login("unknown-code")
    assert r.json()["code"] == 4400


def _mailpit_api() -> str | None:
    import httpx

    for base in ("http://127.0.0.1:18025",):
        try:
            if httpx.get(base + "/api/v1/messages", timeout=2, trust_env=False).status_code == 200:
                return base
        except Exception:
            pass
    return None


@pytest.mark.skipif(_mailpit_api() is None, reason="mailpit not running on :18025 (docker run -p 18025:8025 -p 11025:1025 axllent/mailpit)")
async def test_smtp_delivery_via_mailpit(client, register, monkeypatch):
    import httpx

    api = _mailpit_api()
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", 11025)
    httpx.delete(api + "/api/v1/messages", trust_env=False)
    u = await register(email=f"mail-{uuid.uuid4().hex[:6]}@example.com")
    h = u["headers"]
    assert (await client.post("/api/v1/user/activation/resend", headers=h)).json()["code"] == 0
    assert (await client.post("/api/v1/user/pwd/forgot", json={"email": u["email"]})).json()["code"] == 0
    wid = u["user"]["current_workspace_id"]
    inv = (await client.post(f"/api/v1/workspaces/{wid}/invitations", headers=h,
                             json={"email": "teammate@example.com", "role": "member"})).json()
    assert inv["code"] == 0, inv
    for _ in range(50):
        msgs = httpx.get(api + "/api/v1/messages", trust_env=False).json()["messages"]
        if len(msgs) >= 3:
            break
        await asyncio.sleep(0.1)
    by_to: dict[str, list[str]] = {}
    for msg in msgs:
        for to in msg["To"]:
            by_to.setdefault(to["Address"], []).append(msg["Subject"])
    subjects = " | ".join(by_to.get(u["email"], []))
    assert "verify" in subjects.lower() and "password" in subjects.lower(), by_to
    assert by_to.get("teammate@example.com"), by_to
    # the reset link in the delivered mail actually works
    reset = next(x for x in msgs if "password" in x["Subject"].lower())
    body = httpx.get(f"{api}/api/v1/message/{reset['ID']}", trust_env=False).json()["Text"]
    import re

    tok = re.search(r"token=([A-Za-z0-9_\-]+)", body).group(1)
    r = (await client.post("/api/v1/user/pwd/reset", json={"token": tok, "password": "brand-new-pass1"})).json()
    assert r["code"] == 0
    login = (await client.post("/api/v1/user/login", json={"email": u["email"], "password": "brand-new-pass1"})).json()
    assert login["code"] == 0
