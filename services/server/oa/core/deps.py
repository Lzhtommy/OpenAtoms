"""Request dependencies: authentication, workspace resolution, chat access guard."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.core import security
from oa.core.envelope import FORBIDDEN, ApiError, forbidden, not_found, unauthorized
from oa.db import models as m
from oa.db.session import get_session

ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
ACTION_MIN = {"read": "viewer", "write": "member", "admin": "admin"}


def bearer(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw or None


async def user_from_token(session: AsyncSession, token: str | None) -> m.User | None:
    if not token:
        return None
    data = security.decode(token, "access")
    if not data:
        return None
    return await session.get(m.User, data["user_id"])


def request_token(request: Request) -> str | None:
    """Authorization header, or ?token= for EventSource / download links that cannot set headers."""
    return bearer(request.headers.get("authorization")) or request.query_params.get("token")


async def optional_user(request: Request, session: AsyncSession = Depends(get_session)) -> m.User | None:
    return await user_from_token(session, request_token(request))


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> m.User:
    user = await user_from_token(session, request_token(request))
    if user is None:
        raise unauthorized()
    return user


async def member_role(session: AsyncSession, workspace_id: int, user_id: int) -> str | None:
    row = await session.get(m.WorkspaceMember, (workspace_id, user_id))
    return row.role if row else None


@dataclass
class WorkspaceCtx:
    workspace: m.Workspace
    role: str


async def current_workspace(
    request: Request, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> WorkspaceCtx:
    raw = request.headers.get("x-workspace-id")
    wid: int | None = None
    if raw:
        try:
            wid = int(raw)
        except ValueError:
            wid = None
    if wid is None:
        wid = user.current_workspace_id
    role = await member_role(session, wid, user.id) if wid else None
    if role is None:
        # fall back to any workspace the user belongs to
        row = (
            await session.execute(select(m.WorkspaceMember).where(m.WorkspaceMember.user_id == user.id).limit(1))
        ).scalar_one_or_none()
        if row is None:
            raise forbidden("No workspace")
        wid, role = row.workspace_id, row.role
    ws = await session.get(m.Workspace, wid)
    if ws is None:
        raise not_found("Workspace not found")
    return WorkspaceCtx(ws, role)


def has_role(role: str | None, minimum: str) -> bool:
    return role is not None and ROLE_RANK.get(role, -1) >= ROLE_RANK[minimum]


async def chat_permission(session: AsyncSession, chat: m.Chat, user: m.User | None, action: str) -> bool:
    if user is not None:
        if chat.user_id == user.id:
            return True
        role = await member_role(session, chat.workspace_id, user.id)
        if has_role(role, ACTION_MIN[action]):
            return True
    return action == "read" and chat.access_mode in ("public", "secret")


async def load_chat(session: AsyncSession, chat_id: str, user: m.User | None, action: str = "read") -> m.Chat:
    chat = await session.get(m.Chat, chat_id)
    if chat is None or chat.deleted_at is not None:
        raise not_found("Chat not found")
    if not await chat_permission(session, chat, user, action):
        if user is None:
            raise unauthorized()
        raise ApiError(FORBIDDEN, "No permission for this project")
    return chat


def chat_guard(action: str = "read", auth: bool = True):
    """Dependency factory: resolves path param `chat_id` and checks access."""

    if auth:

        async def dep(chat_id: str, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
            return await load_chat(session, chat_id, user, action)

    else:

        async def dep(  # type: ignore[misc]
            chat_id: str, user: m.User | None = Depends(optional_user), session: AsyncSession = Depends(get_session)
        ):
            return await load_chat(session, chat_id, user, action)

    return dep


def require_internal(request: Request) -> None:
    from oa.config import settings

    if request.headers.get("x-internal-token") != settings.internal_token:
        raise forbidden("internal only")
