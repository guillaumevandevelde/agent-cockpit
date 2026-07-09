"""Separate SQLAlchemy store for the kanban board domain.

Intentionally independent from app.database: the board is portable and
sync-able, whereas app.database holds device-local data (tmux targets,
absolute paths, scheduled deliveries).
"""
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import PROJECT_ROOT, settings


class KanbanBase(DeclarativeBase):
    """Base for all kanban-domain models."""
    pass


kanban_engine = create_async_engine(settings.kanban_database_url, future=True)

if settings.kanban_database_url.startswith("sqlite"):
    @event.listens_for(kanban_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
        cur.close()

KanbanSessionLocal = async_sessionmaker(
    kanban_engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


def _migrate_legacy_sqlite(target_path: Path, legacy_path: Path) -> bool:
    """One-time, non-destructive copy of a legacy CWD-relative kanban.db into the
    new machine-global location. Returns True iff a copy happened.

    No-op if the target already exists (don't clobber a live board) or there is
    no legacy file. Uses SQLite's online backup API, which is WAL-safe and leaves
    the legacy file untouched.
    """
    import sqlite3

    if target_path.exists() or not legacy_path.exists():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # Connect normally (not mode=ro): legacy is WAL-mode and a read-only handle
    # may miss un-checkpointed WAL frames. The backup only reads the source.
    src = sqlite3.connect(str(legacy_path))
    try:
        dst = sqlite3.connect(str(target_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return True


def _sqlite_path(database_url: str) -> Path | None:
    """Filesystem path of a sqlite SQLAlchemy URL, or None for non-sqlite/memory."""
    if not database_url.startswith("sqlite"):
        return None
    db = make_url(database_url).database
    if not db or db == ":memory:":
        return None
    return Path(db)


async def init_kanban_db() -> None:
    """Create kanban tables. Import models so they register on KanbanBase."""
    from app.kanban import models  # noqa: F401

    target = _sqlite_path(settings.kanban_database_url)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_sqlite(target, PROJECT_ROOT / "backend" / "kanban.db")

    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.create_all)
        if settings.kanban_database_url.startswith("sqlite"):
            await _ensure_card_columns(conn)
            await _ensure_column_table(conn)


async def _ensure_card_columns(conn) -> None:
    """Additive, idempotent migration for columns introduced after a DB was first
    created (no migration framework here). create_all never alters existing tables."""
    rows = (await conn.exec_driver_sql("PRAGMA table_info(kanban_cards)")).fetchall()
    cols = {r[1] for r in rows}
    if "agent" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN agent VARCHAR(64)")
    if "transport" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN transport VARCHAR(16)")
    if "resume_session_id" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN resume_session_id VARCHAR(256)")
    if "resume_project_folder" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN resume_project_folder VARCHAR(512)")
    if "scheduled_at" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN scheduled_at VARCHAR(40)")
    if "dispatch_failures" not in cols:
        await conn.exec_driver_sql(
            "ALTER TABLE kanban_cards ADD COLUMN dispatch_failures INTEGER NOT NULL DEFAULT 0"
        )
    if "analyst_agent_id" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN analyst_agent_id VARCHAR(64)")
    if "executor_agent_id" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN executor_agent_id VARCHAR(64)")
    if "parent_card_id" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN parent_card_id VARCHAR(64)")
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_kanban_cards_parent_card_id ON kanban_cards (parent_card_id)"
        )
    if "analyst_run_id" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN analyst_run_id VARCHAR(64)")
    if "depends_on" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN depends_on JSON")
    if "work_type" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN work_type VARCHAR(16)")


async def _ensure_column_table(conn) -> None:
    """Create kanban_columns table if it doesn't exist."""
    tables = (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    table_names = {r[0] for r in tables}
    if "kanban_columns" not in table_names:
        await conn.exec_driver_sql("""
            CREATE TABLE kanban_columns (
                id VARCHAR(64) PRIMARY KEY,
                project_key VARCHAR(512) NOT NULL,
                name VARCHAR(128) NOT NULL,
                rank VARCHAR(64) DEFAULT '',
                default_agent VARCHAR(64),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)
        await conn.exec_driver_sql("CREATE INDEX ix_kanban_columns_project_key ON kanban_columns (project_key)")

    rows = (await conn.exec_driver_sql("PRAGMA table_info(kanban_columns)")).fetchall()
    cols = {r[1] for r in rows}
    if "default_platform" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_columns ADD COLUMN default_platform VARCHAR(16)")
