"""Workspaces, members, invitations, RBAC batch check, folders, general configs, model list."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.config import settings
from oa.core import security
from oa.core.deps import (
    ROLE_RANK,
    WorkspaceCtx,
    chat_permission,
    current_user,
    current_workspace,
    has_role,
    member_role,
)
from oa.core.envelope import bad_request, forbidden, not_found, ok
from oa.core.events import bus
from oa.core.mail import send_mail
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import serializers as ser

router = APIRouter(tags=["workspaces"])


def ws_out(ws: m.Workspace, role: str) -> dict:
    return {"id": ws.id, "name": ws.name, "owner_id": ws.owner_id, "plan": ws.plan, "role": role,
            "credits": ws.credits, "created_at": ser.iso(ws.created_at)}


async def require_ws_role(session: AsyncSession, wid: int, user: m.User, minimum: str) -> tuple[m.Workspace, str]:
    ws = await session.get(m.Workspace, wid)
    role = await member_role(session, wid, user.id)
    if ws is None or role is None:
        raise not_found("Workspace not found")
    if not has_role(role, minimum):
        raise forbidden(f"Requires {minimum} role")
    return ws, role


@router.get("/workspaces")
async def list_workspaces(user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(m.Workspace, m.WorkspaceMember.role).join(m.WorkspaceMember, m.WorkspaceMember.workspace_id == m.Workspace.id)
        .where(m.WorkspaceMember.user_id == user.id).order_by(m.Workspace.id)
    )).all()
    return ok([ws_out(ws, role) for ws, role in rows])


class WsBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.post("/workspaces")
async def create_workspace(body: WsBody, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    ws = m.Workspace(name=body.name, owner_id=user.id, credits=0)
    session.add(ws)
    await session.flush()
    session.add(m.WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await session.commit()
    return ok(ws_out(ws, "owner"))


@router.patch("/workspaces/{wid}")
async def rename_workspace(wid: int, body: WsBody, user: m.User = Depends(current_user),
                           session: AsyncSession = Depends(get_session)):
    ws, role = await require_ws_role(session, wid, user, "admin")
    ws.name = body.name
    await session.commit()
    return ok(ws_out(ws, role))


@router.post("/workspaces/{wid}/switch")
async def switch_workspace(wid: int, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    ws, role = await require_ws_role(session, wid, user, "viewer")
    u = await session.get(m.User, user.id)
    u.current_workspace_id = wid
    await session.commit()
    return ok(ws_out(ws, role))


@router.get("/workspaces/{wid}/members")
async def members(wid: int, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await require_ws_role(session, wid, user, "viewer")
    rows = (await session.execute(
        select(m.User, m.WorkspaceMember.role).join(m.WorkspaceMember, m.WorkspaceMember.user_id == m.User.id)
        .where(m.WorkspaceMember.workspace_id == wid).order_by(m.WorkspaceMember.created_at)
    )).all()
    return ok([{"user_id": u.id, "email": u.email, "name": u.name, "avatar": u.avatar, "role": r} for u, r in rows])


class InviteBody(BaseModel):
    email: EmailStr
    role: str = "member"


@router.post("/workspaces/{wid}/invitations")
async def invite(wid: int, body: InviteBody, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    ws, _ = await require_ws_role(session, wid, user, "admin")
    if body.role not in ("admin", "member", "viewer"):
        raise bad_request("Invalid role")
    tok = security.random_token()
    session.add(m.Invitation(token=tok, workspace_id=wid, email=body.email.lower(), role=body.role, invited_by=user.id))
    await session.commit()
    url = f"{settings.public_web_url}/workspace/invite/{tok}"
    await send_mail(body.email, f"{user.name} invited you to {ws.name} on OpenAtoms", f"Join the workspace: {url}\n")
    return ok({"token": tok, "url": url})


@router.get("/workspaces/invitations/{token}")
async def invitation_info(token: str, session: AsyncSession = Depends(get_session)):
    inv = await session.get(m.Invitation, token)
    if inv is None:
        raise not_found("Invitation not found")
    ws = await session.get(m.Workspace, inv.workspace_id)
    inviter = await session.get(m.User, inv.invited_by)
    return ok({"workspace": {"id": ws.id, "name": ws.name}, "email": inv.email, "role": inv.role,
               "invited_by": inviter.name if inviter else "", "accepted": inv.accepted_at is not None})


@router.post("/workspaces/invitations/{token}/accept")
async def accept(token: str, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    inv = await session.get(m.Invitation, token)
    if inv is None or inv.accepted_at is not None:
        raise not_found("Invitation not found or already used")
    if datetime.now(UTC) - (inv.created_at if inv.created_at.tzinfo else inv.created_at.replace(tzinfo=UTC)) > timedelta(days=7):
        raise bad_request("Invitation expired")
    role = await member_role(session, inv.workspace_id, user.id)
    if role is None:
        session.add(m.WorkspaceMember(workspace_id=inv.workspace_id, user_id=user.id, role=inv.role))
        role = inv.role
    inv.accepted_at = datetime.now(UTC)
    u = await session.get(m.User, user.id)
    u.current_workspace_id = inv.workspace_id
    await session.commit()
    return ok(ws_out(await session.get(m.Workspace, inv.workspace_id), role))


class RoleBody(BaseModel):
    role: str


@router.patch("/workspaces/{wid}/members/{uid}")
async def change_role(wid: int, uid: int, body: RoleBody, user: m.User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    _, my_role = await require_ws_role(session, wid, user, "admin")
    row = await session.get(m.WorkspaceMember, (wid, uid))
    if row is None:
        raise not_found("Member not found")
    if body.role not in ("admin", "member", "viewer") or row.role == "owner":
        raise bad_request("Cannot assign that role")
    if ROLE_RANK[body.role] > ROLE_RANK[my_role]:
        raise forbidden()
    row.role = body.role
    await session.commit()
    return ok({"user_id": uid, "role": row.role})


@router.delete("/workspaces/{wid}/members/{uid}")
async def remove_member(wid: int, uid: int, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    row = await session.get(m.WorkspaceMember, (wid, uid))
    if row is None:
        raise not_found("Member not found")
    if uid != user.id:
        await require_ws_role(session, wid, user, "admin")
    if row.role == "owner":
        raise bad_request("The owner cannot be removed")
    await session.delete(row)
    target = await session.get(m.User, uid)
    if target and target.current_workspace_id == wid:
        target.current_workspace_id = None
    await session.commit()
    await bus.emit_user(uid, "workspace:member_removed", {"workspace_id": wid})
    return ok(None)


class Check(BaseModel):
    resource: str = "chat"
    id: str
    action: str = "read"


class BatchCheck(BaseModel):
    checks: list[Check]


@router.post("/rbac/batch-check")
async def batch_check(body: BatchCheck, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    results = []
    for c in body.checks:
        allowed = False
        if c.action in ("read", "write", "admin"):
            if c.resource == "chat":
                chat = await session.get(m.Chat, c.id)
                allowed = bool(chat and chat.deleted_at is None and await chat_permission(session, chat, user, c.action))
            elif c.resource == "workspace":
                try:
                    role = await member_role(session, int(c.id), user.id)
                except ValueError:
                    role = None
                allowed = has_role(role, {"read": "viewer", "write": "member", "admin": "admin"}[c.action])
        results.append(allowed)
    return ok({"results": results})


# ------------------------------------------------------------------ folders
class FolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.get("/folders")
async def list_folders(ctx: WorkspaceCtx = Depends(current_workspace), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(m.Folder).where(m.Folder.workspace_id == ctx.workspace.id).order_by(m.Folder.name))).scalars()
    return ok([{"id": f.id, "name": f.name, "created_at": ser.iso(f.created_at)} for f in rows])


@router.post("/folders")
async def create_folder(body: FolderBody, ctx: WorkspaceCtx = Depends(current_workspace), user: m.User = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    f = m.Folder(workspace_id=ctx.workspace.id, user_id=user.id, name=body.name)
    session.add(f)
    await session.commit()
    return ok({"id": f.id, "name": f.name, "created_at": ser.iso(f.created_at)})


async def _folder(session: AsyncSession, fid: int, ctx: WorkspaceCtx) -> m.Folder:
    f = await session.get(m.Folder, fid)
    if f is None or f.workspace_id != ctx.workspace.id:
        raise not_found("Folder not found")
    return f


@router.patch("/folders/{fid}")
async def rename_folder(fid: int, body: FolderBody, ctx: WorkspaceCtx = Depends(current_workspace),
                        session: AsyncSession = Depends(get_session)):
    f = await _folder(session, fid, ctx)
    f.name = body.name
    await session.commit()
    return ok({"id": f.id, "name": f.name})


@router.delete("/folders/{fid}")
async def delete_folder(fid: int, ctx: WorkspaceCtx = Depends(current_workspace), session: AsyncSession = Depends(get_session)):
    f = await _folder(session, fid, ctx)
    await session.delete(f)
    await session.commit()
    return ok(None)


# ------------------------------------------------------------------ configs / models
MODEL_INFO = {
    "oa-mock": ("OpenAtoms Mock", "openatoms", "Deterministic offline model for demos and tests"),
    "claude-sonnet-4-5": ("Claude Sonnet 4.5", "anthropic", "Best for coding agents"),
    "gpt-5": ("GPT-5", "openai", "Strong general reasoning"),
    "deepseek-chat": ("DeepSeek V3", "deepseek", "Fast and economical"),
    "deepseek-flash": ("DeepSeek V4.1 Flash", "deepseek", "Fast, 1M context, tool use"),
    "deepseek-v4-pro": ("DeepSeek V4 Pro", "deepseek", "Strongest DeepSeek model"),
    "kimi-k2": ("Kimi K2", "moonshot", "Long-context agentic model"),
}


def models_list() -> list[dict]:
    ids = list(dict.fromkeys([settings.default_model, "oa-mock", *settings.adv_model_list]))
    out = []
    for mid in ids:
        name, provider, desc = MODEL_INFO.get(mid, (mid, "custom", "OpenAI-compatible model via LLM gateway"))
        out.append({"id": mid, "name": name, "provider": provider, "description": desc})
    return out


@router.get("/configs/general")
async def configs_general(user: m.User = Depends(current_user)):
    return ok({"basic_model": settings.basic_model, "default_model": settings.default_model,
               "adv_models": settings.adv_model_list, "agent_mode": "full", "models": models_list()})


@router.get("/ai/models")
async def ai_models():
    return ok(models_list())
