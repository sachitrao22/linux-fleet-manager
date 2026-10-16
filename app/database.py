from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

# SQLite: open a fresh connection per session (NullPool). Opening a local file is cheap,
# and pooled aiosqlite connections can't be shared safely across event loops.
# Postgres: the default connection pool (reuse connections — opening one costs a network round trip).
engine = create_async_engine(
    settings.database_url,
    echo=False,
    **({"poolclass": NullPool} if _is_sqlite else {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True}),
)

# expire_on_commit=False: objects keep their loaded values after commit,
# so reading device.hostname after a commit doesn't fire a surprise SELECT.
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


if _is_sqlite:
    # SQLite ignores foreign keys (and ON DELETE CASCADE) unless told otherwise.
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")   # readers don't block the writer
        cur.close()


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency: one session per HTTP request, always closed afterwards."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Create tables if they don't exist. (Use Alembic migrations once the schema stabilises.)"""
    from app import models  # noqa: F401  — registers every model on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
