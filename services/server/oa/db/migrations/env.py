"""Alembic env: uses settings.db_url (async engine). `uv run alembic upgrade head` / `alembic revision --autogenerate`."""
from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from oa.config import settings
from oa.db.models import Base

target_metadata = Base.metadata


def _run(conn) -> None:
    context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=conn.dialect.name == "sqlite",
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _online() -> None:
    engine = create_async_engine(settings.db_url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=settings.db_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_online())
