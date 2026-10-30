"""Static deployments: a domain label pointing at one version's built artifacts (zero-build switch)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from oa.config import settings
from oa.core.events import bus
from oa.db import models as m
from oa.domain import serializers as ser


def deploy_url(chat: m.Chat, dep: m.Deployment) -> str:
    label = "latest" if dep.is_always_latest else dep.version
    return settings.version_url(chat.user_id, chat.id, label, dep.seq)


async def point_to(session: AsyncSession, chat: m.Chat, dep: m.Deployment, version: str) -> m.Deployment:
    dep.version = version
    dep.status = "success"
    dep.url = deploy_url(chat, dep)
    await session.commit()
    await bus.emit(chat.id, "deploy:update", {"deployment": ser.deployment(dep)})
    return dep
