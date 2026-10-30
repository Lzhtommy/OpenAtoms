"""Single source of truth for wire shapes (CONTRACT §1.4). Used by REST routes and agent event emission."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from oa.config import settings
from oa.db import models as m


def iso(dt: datetime | None) -> str | None:
    """ISO8601 with explicit UTC offset (SQLite hands back naive datetimes that are UTC by construction)."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def message(msg: m.Message, attempts: int | None = None, replies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": msg.id,
        "uuid": msg.uuid,
        "chat_id": msg.chat_id,
        "role": msg.role,
        "name": msg.name,
        "type": msg.type,
        "content": msg.content,
        "refer_id": msg.refer_id,
        "round_id": msg.round_id,
        "attempt": msg.attempt,
        "attempts": attempts if attempts is not None else msg.attempt + 1,
        "prev_round_id": msg.prev_round_id,
        "prev_attempt": msg.prev_attempt,
        "is_finished": msg.is_finished,
        "metadata": msg.metadata_ or {},
        "user_id": msg.user_id,
        "version": msg.version,
        "created_at": iso(msg.created_at),
        "updated_at": iso(msg.updated_at),
        "reply_messages": replies or [],
    }


def action_data(ad: m.ActionData) -> dict[str, Any]:
    return {
        "id": ad.id,
        "uuid": ad.uuid,
        "chat_id": ad.chat_id,
        "message_uuid": ad.message_uuid,
        "command_name": ad.command_name,
        "block_type": ad.block_type,
        "file": ad.file,
        "src_path": ad.src_path,
        "extra": ad.extra or {},
        "status": ad.status,
        "timeline_index": ad.timeline_index,
        "created_at": iso(ad.created_at),
        "updated_at": iso(ad.updated_at),
    }


def timeline(t: m.BlockTimeline, redact: bool = False) -> dict[str, Any]:
    return {
        "id": t.id,
        "chat_id": t.chat_id,
        "index": t.index,
        "block_type": t.block_type,
        "block_owner": t.block_owner,
        "command_name": t.command_name,
        "file": None if (redact and t.has_secret) else t.file,
        "src_path": t.src_path,
        "content": "[redacted]" if (redact and t.has_secret) else t.content,
        "is_running": t.is_running,
        "extra_data": t.extra_data,
        "version": t.version,
        "created_at": iso(t.created_at),
    }


def version(v: m.Version) -> dict[str, Any]:
    return {
        "version": v.version,
        "number": v.number,
        "summary": v.summary,
        "cover": v.cover,
        "link": v.link,
        "commit": v.commit,
        "status": v.status,
        "created_at": iso(v.created_at),
    }


def deployment(d: m.Deployment) -> dict[str, Any]:
    return {
        "chat_id": d.chat_id,
        "version": d.version,
        "is_always_latest": d.is_always_latest,
        "access_mode": d.access_mode or "public",
        "status": d.status,
        "url": d.url,
        "seq": d.seq,
        "kind": d.kind,
        "updated_at": iso(d.updated_at),
    }


def queue_item(q: m.PromptQueueItem) -> dict[str, Any]:
    return {
        "id": q.id,
        "position": q.position,
        "content": q.content,
        "metadata": q.metadata_ or {},
        "status": q.status,
        "created_at": iso(q.created_at),
    }


def user(u: m.User) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "avatar": u.avatar,
        "plan": u.plan,
        "email_verified": u.email_verified,
        "current_workspace_id": u.current_workspace_id,
        "created_at": iso(u.created_at),
    }


def chat(
    c: m.Chat,
    ver: m.Version | None = None,
    deploy: m.Deployment | None = None,
    is_liked: bool = False,
) -> dict[str, Any]:
    cfg = {"agent_mode": "full", "template_name": "react-shadcn-vite", "framework": "react", "plan_mode": False,
           "cloud_enabled": False, **(c.config or {})}
    return {
        "chat_id": c.id,
        "title": c.title,
        "workspace_id": c.workspace_id,
        "user_id": c.user_id,
        "status": c.status,
        "access_mode": c.access_mode,
        "folder_id": c.folder_id,
        "is_starred": c.is_starred,
        "fork_from": c.fork_from,
        "parent_chat_id": c.parent_chat_id,
        "created_at": iso(c.created_at),
        "updated_at": iso(c.updated_at),
        "config": cfg,
        "llm": {"default_model": settings.default_model, **(c.llm or {})},
        "metadata": {"status": c.runtime_status, "is_running": c.is_running, "preview_port": 5173},
        "uris": {"dev": settings.preview_url(c.id), "prod": deploy.url if deploy else None},
        "version": (
            {"version": ver.version, "summary": ver.summary, "cover": ver.cover, "link": ver.link} if ver else None
        ),
        "stats": {"likes": c.likes, "views": c.views},
        "is_liked": is_liked,
    }
