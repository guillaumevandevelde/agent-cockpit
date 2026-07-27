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
            await _migrate_project_columns(conn)
            await _migrate_subscription_prefs_shape(conn)


async def _migrate_subscription_prefs_shape(conn) -> None:
    """Convert a pre-2026-07-17 EAV ``subscription_prefs`` to the singleton shape.

    The table was created on 2026-07-08 as ``(provider_id, key) -> value``; the
    model was reshaped on 2026-07-17 to a wide singleton row
    (``anthropic_plan_tier`` / ``anthropic_custom_limit_tokens``) with no
    migration. ``create_all`` only creates *missing* tables, so an existing DB
    kept the old shape and every startup since raised ``no such column:
    subscription_prefs.anthropic_plan_tier`` out of the lifespan hook.

    This is a table *reshape*, not an ``ADD COLUMN``, so unlike the sibling
    migrations above it rebuilds the table from the model and replays the old
    key/value pairs into the new row. Dropping the DB is not an option — the
    registry store also holds MCP servers, commands, permissions and plugin
    state, and the stored tier resolves to a real 5h token budget.

    Idempotent: stands down when the table is absent (fresh install, where
    ``create_all`` already produced the right shape) or is already migrated.
    """
    from app.models.database import SubscriptionPrefs

    rows = (
        await conn.exec_driver_sql("PRAGMA table_info(subscription_prefs)")
    ).fetchall()
    cols = {row[1] for row in rows}
    if not cols:
        return  # table absent — nothing to reshape
    if "provider_id" not in cols:
        return  # already the singleton shape

    legacy = (
        await conn.exec_driver_sql(
            "SELECT provider_id, key, value FROM subscription_prefs"
        )
    ).fetchall()
    prefs = {
        (provider_id, key): value
        for provider_id, key, value in legacy
    }

    tier = prefs.get(("anthropic", "plan_tier"))
    raw_limit = prefs.get(("anthropic", "custom_limit_tokens"))
    try:
        custom_limit = int(raw_limit) if raw_limit is not None else None
    except (TypeError, ValueError):
        # A non-numeric legacy value is not worth failing startup over; the
        # tier itself still carries the meaningful setting.
        custom_limit = None

    await conn.exec_driver_sql("DROP TABLE subscription_prefs")
    await conn.run_sync(SubscriptionPrefs.__table__.create)
    await conn.exec_driver_sql(
        "INSERT INTO subscription_prefs "
        "(id, anthropic_plan_tier, anthropic_custom_limit_tokens, updated_at) "
        "VALUES (1, ?, ?, CURRENT_TIMESTAMP)",
        (tier, custom_limit),
    )


async def _migrate_project_columns(conn) -> None:
    """Add the portfolio ``kind``/``priority`` columns to an existing ``projects``.

    ``create_all`` only creates missing tables — it never alters a table that
    already exists — so a DB from before these columns needs an in-place
    ``ALTER TABLE ... ADD COLUMN``. Mirrors the ``max_sessions`` pattern in
    ``app/kanban/db.py``. Idempotent: skips when the column is already present
    (fresh installs where ``create_all`` produced them, or a re-run).
    """
    rows = (await conn.exec_driver_sql("PRAGMA table_info(projects)")).fetchall()
    cols = {row[1] for row in rows}
    if not cols:
        return  # table not created yet (non-sqlite path or first run mid-flight)
    if "kind" not in cols:
        await conn.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN kind TEXT NOT NULL DEFAULT 'product'"
        )
    if "priority" not in cols:
        await conn.exec_driver_sql(
            "ALTER TABLE projects ADD COLUMN priority INTEGER"
        )


async def _migrate_terminology_columns(conn) -> None:
    """Rename tables and columns to align with the canonical terminology.

    Two waves:

    1. CLI-tool concept used to live under ``provider`` (e.g. ``provider`` on
       ``BridgeSessionAttachment``, ``RunGroup``, and ``MailAgentSession``);
       per ``docs/cockpit/terminology.md`` that concept is now ``cli``.
    2. The ``agent_teams``/``agent_team_members`` tables (and their
       ``lead_session_name``/``session_name`` columns) were renamed to
       ``run_groups``/``run_memberships``/``lead_run_name``/``run_name`` —
       "agent-as-team" collided with the persona definition of "Agent" (a
       subagent-persona from ``.claude/agents/*.md``); the running concept is
       now "Run".

    Both waves use SQLite's ``ALTER TABLE ... RENAME TO/COLUMN`` (3.22+/3.25+)
    so live data survives — the kanban DB lives in ``claude_registry.db`` and
    cannot be dropped.

    Idempotent: each entry skips when the source is absent or the target
    already exists (covers both fresh installs where ``create_all`` produced
    the new name and partial in-flight migrations).
    """
    column_renames: tuple[tuple[str, str, str], ...] = (
        ("bridge_session_attachments", "provider", "cli"),
        ("run_groups", "provider", "cli"),
        ("mail_agent_sessions", "provider", "cli"),
        ("run_groups", "lead_session_name", "lead_run_name"),
        ("run_memberships", "session_name", "run_name"),
    )
    table_renames: tuple[tuple[str, str], ...] = (
        ("agent_teams", "run_groups"),
        ("agent_team_members", "run_memberships"),
    )

    tables = (
        await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    ).fetchall()
    table_names = {row[0] for row in tables}

    for old_table, new_table in table_renames:
        if new_table in table_names:
            continue
        if old_table not in table_names:
            continue
        await conn.exec_driver_sql(
            f"ALTER TABLE {old_table} RENAME TO {new_table}"
        )
        table_names.discard(old_table)
        table_names.add(new_table)

    # column renames — re-fetch table list because table renames above
    for table, old_name, new_name in column_renames:
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
