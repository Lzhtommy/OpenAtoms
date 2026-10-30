"""Secrets (project/workspace), GitHub and Supabase integrations (ARCHITECTURE §11, PLAN P6)."""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.api.auth import consume_one_time, create_one_time
from oa.api.workspaces import require_ws_role
from oa.config import settings
from oa.core import security
from oa.core.deps import WorkspaceCtx, chat_guard, current_user, current_workspace, has_role
from oa.core.envelope import bad_request, forbidden, not_found, ok
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import gitops
from oa.domain import serializers as ser
from oa.domain.secrets import KEY_RE, masked

log = logging.getLogger("oa.integrations")
router = APIRouter(tags=["integrations"])
PROVIDERS = ("github", "supabase")


def _gh_api() -> str:
    return settings.github_api_url.rstrip("/")


def _sb_api() -> str:
    return settings.supabase_api_url.rstrip("/")


# ------------------------------------------------------------------ secrets
class SecretBody(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=20000)
    env: str = "dev"


def secret_out(s: m.Secret) -> dict:
    return {"id": s.id, "key": s.key, "value": masked(security.decrypt(s.value_enc)), "env": s.env, "scope": s.scope,
            "updated_at": ser.iso(s.updated_at)}


def _validate(body: SecretBody) -> None:
    if not KEY_RE.match(body.key):
        raise bad_request("Secret key must be letters, digits and underscores")
    if body.env not in ("dev", "prod"):
        raise bad_request("env must be dev or prod")


async def _upsert(session: AsyncSession, where, scope: str, body: SecretBody, **owner) -> m.Secret:
    row = (await session.execute(select(m.Secret).where(where, m.Secret.key == body.key, m.Secret.env == body.env))).scalar_one_or_none()
    if row is None:
        row = m.Secret(scope=scope, key=body.key, env=body.env, value_enc="", **owner)
        session.add(row)
    row.value_enc = security.encrypt(body.value)
    await session.commit()
    return row


@router.get("/chats/{chat_id}/secrets")
async def chat_secrets(chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(m.Secret).where(m.Secret.scope == "chat", m.Secret.chat_id == chat.id).order_by(m.Secret.key))).scalars()
    return ok([secret_out(s) for s in rows])


@router.post("/chats/{chat_id}/secrets")
async def add_chat_secret(body: SecretBody, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    _validate(body)
    row = await _upsert(session, (m.Secret.scope == "chat") & (m.Secret.chat_id == chat.id), "chat", body, chat_id=chat.id)
    await _sync_env_file(chat.id)
    return ok(secret_out(row))


@router.delete("/chats/{chat_id}/secrets/{sid}")
async def del_chat_secret(sid: int, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    row = await session.get(m.Secret, sid)
    if row is None or row.chat_id != chat.id:
        raise not_found("Secret not found")
    await session.delete(row)
    await session.commit()
    await _sync_env_file(chat.id)
    return ok(None)


async def _sync_env_file(chat_id: str) -> None:
    """Materialize dev secrets into workspace/.env.local so dev servers and builds pick them up."""
    from oa.domain.secrets import secrets_env

    ws = settings.workspace_dir(chat_id)
    if not ws.exists():
        return
    env = await secrets_env(chat_id, "dev")
    lines = ["# Managed by OpenAtoms secrets. Do not edit."] + [f"{k}={json.dumps(v)}" for k, v in sorted(env.items())]
    (ws / ".env.local").write_text("\n".join(lines) + "\n")


@router.get("/workspaces/{wid}/secrets")
async def ws_secrets(wid: int, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await require_ws_role(session, wid, user, "admin")
    rows = (await session.execute(select(m.Secret).where(m.Secret.scope == "workspace", m.Secret.workspace_id == wid).order_by(m.Secret.key))).scalars()
    return ok([secret_out(s) for s in rows])


@router.post("/workspaces/{wid}/secrets")
async def add_ws_secret(wid: int, body: SecretBody, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await require_ws_role(session, wid, user, "admin")
    _validate(body)
    row = await _upsert(session, (m.Secret.scope == "workspace") & (m.Secret.workspace_id == wid), "workspace", body, workspace_id=wid)
    return ok(secret_out(row))


@router.delete("/workspaces/{wid}/secrets/{sid}")
async def del_ws_secret(wid: int, sid: int, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await require_ws_role(session, wid, user, "admin")
    row = await session.get(m.Secret, sid)
    if row is None or row.workspace_id != wid:
        raise not_found("Secret not found")
    await session.delete(row)
    await session.commit()
    return ok(None)


# ------------------------------------------------------------------ integrations
def _redirect(provider: str) -> str:
    return f"{settings.public_web_url}/api/v1/integrations/{provider}/callback"


def _configured(provider: str) -> bool:
    return bool(settings.github_client_id if provider == "github" else settings.supabase_client_id)


async def get_integration(session: AsyncSession, workspace_id: int, provider: str) -> m.Integration | None:
    return (await session.execute(select(m.Integration).where(m.Integration.workspace_id == workspace_id,
                                                              m.Integration.provider == provider))).scalar_one_or_none()


def tokens(integ: m.Integration) -> dict:
    try:
        return json.loads(security.decrypt(integ.tokens_enc) or "{}")
    except ValueError:
        return {}


async def _verify_token(provider: str, token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        if provider == "github":
            r = await c.get(f"{_gh_api()}/user", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
            if r.status_code != 200:
                raise bad_request("GitHub token rejected")
            u = r.json()
            return {"login": u.get("login"), "name": u.get("name"), "avatar": u.get("avatar_url")}
        r = await c.get(f"{_sb_api()}/v1/organizations", headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            raise bad_request("Supabase token rejected")
        orgs = r.json()
        return {"organizations": [{"id": o.get("id"), "name": o.get("name")} for o in orgs]}


async def _store(session: AsyncSession, ctx_ws: int, user_id: int, provider: str, account: dict, tok: dict) -> m.Integration:
    integ = await get_integration(session, ctx_ws, provider)
    if integ is None:
        integ = m.Integration(workspace_id=ctx_ws, user_id=user_id, provider=provider)
        session.add(integ)
    integ.account = account
    integ.tokens_enc = security.encrypt(json.dumps(tok))
    await session.commit()
    return integ


def _check_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise not_found("Unknown provider")


@router.get("/integrations")
async def list_integrations(ctx: WorkspaceCtx = Depends(current_workspace), session: AsyncSession = Depends(get_session)):
    out = []
    for p in PROVIDERS:
        integ = await get_integration(session, ctx.workspace.id, p)
        out.append({"provider": p, "connected": integ is not None, "account": integ.account if integ else None,
                    "oauth_configured": _configured(p)})
    return ok(out)


@router.get("/integrations/{provider}/connect")
async def connect(provider: str, ctx: WorkspaceCtx = Depends(current_workspace), user: m.User = Depends(current_user),
                  session: AsyncSession = Depends(get_session)):
    _check_provider(provider)
    if not _configured(provider):
        raise bad_request(f"{provider} OAuth is not configured; paste an access token instead")
    state = await create_one_time(session, "oauth_state", user.id, timedelta(minutes=10),
                                  {"provider": provider, "workspace_id": ctx.workspace.id})
    await session.commit()
    if provider == "github":
        q = urlencode({"client_id": settings.github_client_id, "redirect_uri": _redirect(provider), "scope": "repo",
                       "state": state})
        return ok({"url": f"{settings.github_oauth_url.rstrip('/')}/login/oauth/authorize?{q}"})
    q = urlencode({"client_id": settings.supabase_client_id, "redirect_uri": _redirect(provider), "response_type": "code",
                   "state": state})
    return ok({"url": f"{_sb_api()}/v1/oauth/authorize?{q}"})


@router.get("/integrations/{provider}/callback")
async def callback(provider: str, code: str, state: str, session: AsyncSession = Depends(get_session)):
    _check_provider(provider)
    st = await consume_one_time(session, state, "oauth_state")
    data = st.data or {}
    if data.get("provider") != provider:
        raise bad_request("state mismatch")
    async with httpx.AsyncClient(timeout=15) as c:
        if provider == "github":
            r = await c.post(f"{settings.github_oauth_url.rstrip('/')}/login/oauth/access_token",
                             headers={"Accept": "application/json"},
                             data={"client_id": settings.github_client_id, "client_secret": settings.github_client_secret,
                                   "code": code, "redirect_uri": _redirect(provider)})
        else:
            r = await c.post(f"{_sb_api()}/v1/oauth/token", auth=(settings.supabase_client_id, settings.supabase_client_secret),
                             data={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect(provider)})
    tok = r.json() if r.status_code == 200 else {}
    access = tok.get("access_token")
    if not access:
        raise bad_request(f"{provider} token exchange failed")
    account = await _verify_token(provider, access)
    await _store(session, int(data["workspace_id"]), int(st.user_id), provider, account, tok)
    return RedirectResponse(f"{settings.public_web_url}/settings?tab=integrations&connected={provider}")


class TokenBody(BaseModel):
    token: str = Field(min_length=4)


@router.post("/integrations/{provider}/token")
async def manual_token(provider: str, body: TokenBody, ctx: WorkspaceCtx = Depends(current_workspace),
                       user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    _check_provider(provider)
    if not has_role(ctx.role, "member"):
        raise forbidden()
    account = await _verify_token(provider, body.token)
    integ = await _store(session, ctx.workspace.id, user.id, provider, account, {"access_token": body.token})
    return ok({"provider": provider, "connected": True, "account": integ.account})


@router.delete("/integrations/{provider}")
async def disconnect(provider: str, ctx: WorkspaceCtx = Depends(current_workspace), session: AsyncSession = Depends(get_session)):
    _check_provider(provider)
    integ = await get_integration(session, ctx.workspace.id, provider)
    if integ is not None:
        await session.delete(integ)
        await session.commit()
    return ok(None)


async def _require(session: AsyncSession, chat: m.Chat, provider: str) -> str:
    integ = await get_integration(session, chat.workspace_id, provider)
    tok = tokens(integ).get("access_token") if integ else None
    if not tok:
        raise bad_request(f"{provider} is not connected")
    return tok


# ------------------------------------------------------------------ GitHub repo sync
class RepoBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    private: bool = True


def _gh_url(repo: str) -> str:
    return f"{settings.github_git_url.rstrip('/')}/{repo}.git"


def _gh_env(tok: str) -> dict[str, str]:
    return gitops.http_auth_env("x-access-token", tok)


@router.post("/chats/{chat_id}/github/repo")
async def gh_repo(body: RepoBody, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    tok = await _require(session, chat, "github")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", body.name):
        raise bad_request("invalid repository name")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{_gh_api()}/user/repos", json={"name": body.name, "private": body.private, "auto_init": False},
                         headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
    if r.status_code not in (200, 201):
        raise bad_request(f"GitHub: {r.json().get('message', r.text)}")
    repo = r.json()["full_name"]
    chat.config = {**(chat.config or {}), "github": {"repo": repo, "branch": "main", "url": r.json().get("html_url")},
                   "github_status": "connected"}
    await session.commit()
    return ok(chat.config["github"])


class PushBody(BaseModel):
    message: str = "Update from OpenAtoms"


def _linked_repo(chat: m.Chat) -> dict:
    gh = (chat.config or {}).get("github")
    if not gh or not gh.get("repo"):
        raise bad_request("No GitHub repository linked")
    return gh


@router.post("/chats/{chat_id}/github/push")
async def gh_push(body: PushBody, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    tok = await _require(session, chat, "github")
    gh = _linked_repo(chat)
    ws = settings.workspace_dir(chat.id)
    commit = await gitops.commit_all(ws, body.message)
    code, out = await gitops.run(ws, "push", _gh_url(gh["repo"]), f"HEAD:{gh.get('branch', 'main')}", check=False,
                                 timeout=180, env=_gh_env(tok))
    if code != 0:
        raise bad_request("git push failed: " + out.replace(tok, "***")[-500:])
    return ok({"commit": commit, "repo": gh["repo"]})


@router.post("/chats/{chat_id}/github/pull")
async def gh_pull(chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    tok = await _require(session, chat, "github")
    gh = _linked_repo(chat)
    ws = settings.workspace_dir(chat.id)
    await gitops.commit_all(ws, "Local changes before pull")
    code, out = await gitops.run(ws, "pull", "--no-rebase", "--no-edit", _gh_url(gh["repo"]), gh.get("branch", "main"),
                                 check=False, timeout=180, env=_gh_env(tok))
    if code != 0:
        raise bad_request("git pull failed: " + out.replace(tok, "***")[-500:])
    return ok({"output": out.replace(tok, "***")[-2000:]})


# ------------------------------------------------------------------ Supabase
class LinkBody(BaseModel):
    project_ref: str = Field(min_length=3, max_length=64)


def _set_env(path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    keys = set(values)
    lines = [ln for ln in lines if ln.split("=", 1)[0].strip() not in keys]
    lines += [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n")


@router.post("/chats/{chat_id}/supabase/link")
async def sb_link(body: LinkBody, chat: m.Chat = Depends(chat_guard("write")), session: AsyncSession = Depends(get_session)):
    tok = await _require(session, chat, "supabase")
    if not re.fullmatch(r"[a-z0-9]+", body.project_ref):
        raise bad_request("invalid project ref")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{_sb_api()}/v1/projects/{body.project_ref}/api-keys", headers={"Authorization": f"Bearer {tok}"})
    if r.status_code != 200:
        raise bad_request("Could not read Supabase project keys")
    anon = next((k["api_key"] for k in r.json() if k.get("name") == "anon"), None)
    if not anon:
        raise bad_request("Project has no anon key")
    url = f"https://{body.project_ref}.supabase.co"
    ws = settings.workspace_dir(chat.id)
    ws.mkdir(parents=True, exist_ok=True)
    _set_env(ws / ".env", {"VITE_SUPABASE_URL": url, "VITE_SUPABASE_ANON_KEY": anon})
    chat.config = {**(chat.config or {}), "supabase": {"project_ref": body.project_ref, "url": url}}
    await session.commit()
    return ok(chat.config["supabase"])
