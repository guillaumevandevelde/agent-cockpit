"""The ``subscription_prefs`` EAV -> singleton shape migration.

Timeline of the breakage this covers:

- 2026-07-08 (e7d5387) created ``subscription_prefs`` as an EAV table:
  ``(provider_id, key) -> value``, holding e.g. ``('anthropic','plan_tier','pro')``.
- 2026-07-17 (08ac8e2) reshaped the model to a wide singleton row with
  ``anthropic_plan_tier`` / ``anthropic_custom_limit_tokens`` columns — with no
  migration.

``Base.metadata.create_all`` only creates *missing* tables; it never alters one
that already exists. So every registry DB created before 2026-07-17 kept the old
EAV table, and every backend startup since then raised::

    sqlite3.OperationalError: no such column: subscription_prefs.anthropic_plan_tier

from ``sync_anthropic_provider_registration`` in the lifespan hook.

Dropping the registry DB (CLAUDE.md's blunt "no migration system" recipe) is not
an acceptable repair here: that store also holds MCP servers, commands,
permissions and plugin state. It would also silently discard the user's plan
tier, which resolves to a real 5h token budget (``pro`` -> 44_000), so losing it
changes rate-limit accounting rather than merely resetting a cosmetic pref.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

OLD_SHAPE_DDL = """
CREATE TABLE subscription_prefs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    provider_id VARCHAR NOT NULL,
    key VARCHAR NOT NULL,
    value VARCHAR NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uix_subscription_prefs_provider_key UNIQUE (provider_id, key)
)
"""


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}")
    yield eng
    await eng.dispose()


async def _columns(conn, table: str) -> set[str]:
    rows = (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


@pytest.mark.asyncio
async def test_migrates_old_eav_table_and_carries_plan_tier(engine):
    from app.database import _migrate_subscription_prefs_shape

    async with engine.begin() as conn:
        await conn.exec_driver_sql(OLD_SHAPE_DDL)
        await conn.exec_driver_sql(
            "INSERT INTO subscription_prefs (provider_id, key, value, updated_at) "
            "VALUES ('anthropic', 'plan_tier', 'pro', '2026-07-08 21:26:27.310346')"
        )

    async with engine.begin() as conn:
        await _migrate_subscription_prefs_shape(conn)

    async with engine.begin() as conn:
        cols = await _columns(conn, "subscription_prefs")
        assert "anthropic_plan_tier" in cols
        assert "provider_id" not in cols

        row = (
            await conn.exec_driver_sql(
                "SELECT id, anthropic_plan_tier, anthropic_custom_limit_tokens "
                "FROM subscription_prefs"
            )
        ).fetchall()
        # The user's real setting survives as the singleton row.
        assert row == [(1, "pro", None)]

        # No legacy leftovers.
        tables = {
            r[0]
            for r in (
                await conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ).fetchall()
        }
        assert not [t for t in tables if "legacy" in t]


@pytest.mark.asyncio
async def test_carries_custom_tier_limit(engine):
    from app.database import _migrate_subscription_prefs_shape

    async with engine.begin() as conn:
        await conn.exec_driver_sql(OLD_SHAPE_DDL)
        for key, value in (
            ("plan_tier", "custom"),
            ("custom_limit_tokens", "123456"),
        ):
            await conn.exec_driver_sql(
                "INSERT INTO subscription_prefs (provider_id, key, value, updated_at) "
                f"VALUES ('anthropic', '{key}', '{value}', '2026-07-08 21:26:27')"
            )

    async with engine.begin() as conn:
        await _migrate_subscription_prefs_shape(conn)

    async with engine.begin() as conn:
        row = (
            await conn.exec_driver_sql(
                "SELECT anthropic_plan_tier, anthropic_custom_limit_tokens "
                "FROM subscription_prefs"
            )
        ).fetchone()
        assert row == ("custom", 123456)


@pytest.mark.asyncio
async def test_is_idempotent_and_leaves_new_shape_untouched(engine):
    """Running against an already-migrated DB must not clobber the stored row."""
    from app.database import _migrate_subscription_prefs_shape

    async with engine.begin() as conn:
        await conn.exec_driver_sql(OLD_SHAPE_DDL)
        await conn.exec_driver_sql(
            "INSERT INTO subscription_prefs (provider_id, key, value, updated_at) "
            "VALUES ('anthropic', 'plan_tier', 'max_20x', '2026-07-08 21:26:27')"
        )

    async with engine.begin() as conn:
        await _migrate_subscription_prefs_shape(conn)
    # Second and third passes are no-ops.
    async with engine.begin() as conn:
        await _migrate_subscription_prefs_shape(conn)
    async with engine.begin() as conn:
        await _migrate_subscription_prefs_shape(conn)

    async with engine.begin() as conn:
        rows = (
            await conn.exec_driver_sql(
                "SELECT id, anthropic_plan_tier FROM subscription_prefs"
            )
        ).fetchall()
        assert rows == [(1, "max_20x")]


@pytest.mark.asyncio
async def test_no_table_is_a_noop(engine):
    """Fresh install: create_all makes the right shape, migration must stand down."""
    from app.database import _migrate_subscription_prefs_shape

    async with engine.begin() as conn:
        await _migrate_subscription_prefs_shape(conn)  # must not raise

    async with engine.begin() as conn:
        tables = {
            r[0]
            for r in (
                await conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ).fetchall()
        }
        assert "subscription_prefs" not in tables
