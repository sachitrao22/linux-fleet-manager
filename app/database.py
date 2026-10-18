from typing import Any, Dict, Tuple

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings


def normalize_database_url(raw: str) -> Tuple[URL, Dict[str, Any]]:
    """
    Accept the URL a hosting provider hands you, as-is.

    Neon / Render / Supabase give `postgres://…?sslmode=require&channel_binding=require`.
    SQLAlchemy needs the async driver named (`postgresql+asyncpg://`), and asyncpg takes
    `ssl=` instead of libpq's `sslmode` (it rejects `sslmode` and `channel_binding`).
    Behind a transaction pooler (PgBouncer, e.g. Neon's "-pooler" host) asyncpg's
    prepared-statement cache must be off.
    """
    url = make_url(raw)
    connect_args: Dict[str, Any] = {}
    if url.drivername in ("postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg"):
        url = url.set(drivername="postgresql+asyncpg")
    if url.drivername.startswith("postgresql+asyncpg"):
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if sslmode in ("require", "prefer", "allow"):
            connect_args["ssl"] = "require"
        elif sslmode in ("verify-ca", "verify-full"):
            connect_args["ssl"] = True          # verify the server certificate
        if url.host and "pooler" in url.host:
            connect_args["statement_cache_size"] = 0
            query["prepared_statement_cache_size"] = "0"
        url = url.set(query=query)
    return url, connect_args


DB_URL, CONNECT_ARGS = normalize_database_url(settings.database_url)
_is_sqlite = DB_URL.drivername.startswith("sqlite")

# SQLite: open a fresh connection per session (NullPool). Opening a local file is cheap,
# and pooled aiosqlite connections can't be shared safely across event loops.
# Postgres: a connection pool (reuse connections — opening one costs a network round trip).
engine = create_async_engine(
    DB_URL,
    echo=False,
    connect_args=CONNECT_ARGS,
    **({"poolclass": NullPool} if _is_sqlite else {"pool_size": 5, "max_overflow": 10, "pool_pre_ping": True}),
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


def _add_missing_columns(sync_conn) -> None:
    """
    create_all() never alters existing tables. This adds columns introduced after a
    database was first created, so older fleet.db files keep working.
    (A real project would use Alembic migrations; this covers the one column added so far.)
    """
    insp = inspect(sync_conn)
    if "devices" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("devices")}
        if "workspace_id" not in cols:
            sync_conn.execute(text("ALTER TABLE devices ADD COLUMN workspace_id CHAR(32)"
                                   if _is_sqlite else
                                   "ALTER TABLE devices ADD COLUMN workspace_id UUID"))


async def init_db():
    """Create tables if they don't exist, and add any newer columns."""
    from app import models  # noqa: F401  — registers every model on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)
        await conn.run_sync(Base.metadata.create_all)
