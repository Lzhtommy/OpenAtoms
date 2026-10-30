from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from oa.config import settings
from oa.db.models import Base

_is_sqlite = settings.db_url.startswith("sqlite")
if _is_sqlite:
    settings.data_dir.mkdir(parents=True, exist_ok=True)

# Pool sizing: request handlers hold a session and some domain helpers open a second one (nested checkout), and
# every concurrent agent run holds sessions while it works. With the SQLAlchemy default (5 + 10 overflow) ~15
# concurrent sends deadlocked until the 30s pool timeout (found by scripts/loadtest.py @40 users). SQLite
# connections are cheap (one aiosqlite thread each); for Postgres keep pool_size+overflow × processes below
# max_connections (or put PgBouncer in front).
_pool_size = int(os.environ.get("OA_DB_POOL_SIZE", "20" if _is_sqlite else "10"))
_max_overflow = int(os.environ.get("OA_DB_MAX_OVERFLOW", "100" if _is_sqlite else "30"))
engine = create_async_engine(
    settings.db_url,
    pool_pre_ping=not _is_sqlite,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=float(os.environ.get("OA_DB_POOL_TIMEOUT", "60")),
    connect_args={"timeout": 30} if _is_sqlite else {},
)

if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Unit of work for background code (agents, worker). Commits on success."""
    async with SessionLocal() as s:
        try:
            yield s
            await s.commit()
        except BaseException:
            await s.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionLocal() as s:
        yield s


async def init_db() -> None:
    """lite (SQLite): create_all. full (Postgres): `alembic upgrade head`. api replicas and workers start
    concurrently, so the upgrade is serialized with a session-level advisory lock."""
    if _is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(724001)"))
        try:
            await asyncio.to_thread(command.upgrade, cfg, "head")
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(724001)"))
