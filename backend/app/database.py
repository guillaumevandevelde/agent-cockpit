"""Database setup with SQLAlchemy async."""
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
)


# For SQLite: enable WAL so readers don't block writers (and vice versa).
# Without this, any write (usage ingest, presence event, etc.) stalls
# concurrent chart/page reads and can surface "database is locked" under
# load. WAL is a one-time pragma that persists in the DB header.
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
        cur.close()

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """Dependency for getting async database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if settings.database_url.startswith("sqlite"):
        async with engine.begin() as conn:
            await _migrate_terminology_columns(conn)


async def _migrate_terminology_columns(conn) -> None:
    """Rename columns to align with the canonical terminology.

    The CLI-tool concept used to live under ``provider`` (e.g. ``provider`` on
    ``BridgeSessionAttachment``, ``AgentTeam``, and ``MailAgentSession``);
    per ``docs/cockpit/terminology.md`` that concept is now ``cli``. SQLite's
    ``ALTER TABLE ... RENAME COLUMN`` (3.25+) does this in place — no data
    loss, no need to drop the live kanban DB that lives in ``claude_registry.db``.

    Idempotent: skips when the source column is absent or the target column is
    already present (covers both fresh installs where ``create_all`` produced
    the new name and partial in-flight migrations).
    """
    renames: tuple[tuple[str, str, str], ...] = (
        ("bridge_session_attachments", "provider", "cli"),
        ("agent_teams", "provider", "cli"),
        ("mail_agent_sessions", "provider", "cli"),
    )
    for table, old_name, new_name in renames:
        tables = (
            await conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ).fetchall()
        table_names = {row[0] for row in tables}
        if table not in table_names:
            continue
        rows = (
            await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        ).fetchall()
        cols = {row[1] for row in rows}
        if new_name in cols:
            continue
        if old_name not in cols:
            continue
        await conn.exec_driver_sql(
            f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}"
        )
