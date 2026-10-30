"""Encrypted project/workspace secrets, env injection and output masking (ARCHITECTURE §13)."""
from __future__ import annotations

import re

from sqlalchemy import select

from oa.core import security
from oa.db import models as m
from oa.db.session import SessionLocal

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,199}$")
MASK = "••••••"


def masked(value: str) -> str:
    return "••••" + value[-4:] if len(value) > 4 else "••••"


async def secrets_env(chat_id: str, env: str = "dev") -> dict[str, str]:
    """Workspace secrets overridden by project secrets, for the given env."""
    async with SessionLocal() as s:
        chat = await s.get(m.Chat, chat_id)
        if chat is None:
            return {}
        rows = (
            await s.execute(
                select(m.Secret).where(
                    m.Secret.env == env,
                    ((m.Secret.scope == "workspace") & (m.Secret.workspace_id == chat.workspace_id))
                    | ((m.Secret.scope == "chat") & (m.Secret.chat_id == chat_id)),
                )
            )
        ).scalars().all()
    out: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: 0 if r.scope == "workspace" else 1):
        out[r.key] = security.decrypt(r.value_enc)
    return out


async def secret_values(chat_id: str) -> list[str]:
    vals: set[str] = set()
    for env in ("dev", "prod"):
        vals.update(v for v in (await secrets_env(chat_id, env)).values() if v)
    return sorted(vals, key=len, reverse=True)


def mask(text: str, values: list[str]) -> tuple[str, bool]:
    changed = False
    for v in values:
        if len(v) >= 6 and v in text:
            text = text.replace(v, MASK)
            changed = True
    return text, changed
