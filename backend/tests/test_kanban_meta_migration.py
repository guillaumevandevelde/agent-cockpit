"""Tests for the KanbanMeta key-migration helper (``migrate_project_keys``).

A project moves from a pre-remote ``slug:<name>`` key to a post-remote
``git:<host>/<path>`` key after ``gh repo create``. Every per-project flag row
in ``KanbanMeta`` (``autodispatch:<key>``, ``shipmode:<key>``,
``skip_permissions:<key>``, ``transport:<key>``, …) must follow the project to
the new key. ``migrate_project_keys`` renames the embedded project-key segment
atomically and idempotently.

The four META prefixes are *coincidental* — the helper is a generic,
segment-boundary-aware rename, not a hardcoded prefix list. Matching respects
key-segment (``:``) boundaries so a rename of ``slug:my-app`` never bleeds into
the sibling project ``slug:my-app-2``.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.kanban.meta_migration import MigrationResult, migrate_project_keys
from app.kanban.models import KanbanMeta
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

META_PREFIXES = ("autodispatch:", "shipmode:", "skip_permissions:", "transport:")


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _seed(session, rows: dict[str, str]) -> None:
    for key, value in rows.items():
        session.add(KanbanMeta(key=key, value=value))
    await session.commit()


async def _all_keys(session) -> dict[str, str]:
    rows = (await session.execute(select(KanbanMeta))).scalars().all()
    return {r.key: r.value for r in rows}


@pytest.mark.asyncio
async def test_migrate_keys_happy_path():
    """Every per-project flag row (all four coincidental prefixes) follows the
    project from ``slug:my-app`` to ``git:h/p``."""
    async with KanbanSessionLocal() as s:
        await _seed(s, {p + "slug:my-app": "1" for p in META_PREFIXES})

    async with KanbanSessionLocal() as s:
        result = await migrate_project_keys(s, "slug:my-app", "git:h/p")

    assert result.migrated == 4
    assert result.conflicts == []
    assert result.deleted == 4

    async with KanbanSessionLocal() as s:
        keys = await _all_keys(s)
    assert set(keys) == {p + "git:h/p" for p in META_PREFIXES}
    assert all(v == "1" for v in keys.values())


@pytest.mark.asyncio
async def test_migrate_keys_no_matches():
    """No row carries the old key -> empty result, no raise, nothing touched."""
    async with KanbanSessionLocal() as s:
        await _seed(s, {"autodispatch:slug:other": "1", "device_id": "abc123"})

    async with KanbanSessionLocal() as s:
        result = await migrate_project_keys(s, "slug:my-app", "git:h/p")

    assert result == MigrationResult(migrated=0, conflicts=[], deleted=0)

    async with KanbanSessionLocal() as s:
        keys = await _all_keys(s)
    assert keys == {"autodispatch:slug:other": "1", "device_id": "abc123"}


@pytest.mark.asyncio
async def test_migrate_keys_idempotent():
    """``old_key == new_key`` is an immediate no-op (no query, no mutation)."""
    async with KanbanSessionLocal() as s:
        await _seed(s, {"autodispatch:slug:my-app": "1"})

    async with KanbanSessionLocal() as s:
        result = await migrate_project_keys(s, "slug:my-app", "slug:my-app")

    assert result == MigrationResult(migrated=0, conflicts=[], deleted=0)

    async with KanbanSessionLocal() as s:
        keys = await _all_keys(s)
    assert keys == {"autodispatch:slug:my-app": "1"}


@pytest.mark.asyncio
async def test_migrate_keys_partial_overlap_does_not_match_substrings():
    """Renaming ``autodispatch:slug:my-app`` must not touch the sibling
    project's ``autodispatch:slug:my-app-2`` — segment-boundary awareness."""
    async with KanbanSessionLocal() as s:
        await _seed(s, {
            "autodispatch:slug:my-app": "1",
            "autodispatch:slug:my-app-2": "1",
        })

    async with KanbanSessionLocal() as s:
        result = await migrate_project_keys(
            s, "autodispatch:slug:my-app", "git:h/p"
        )

    assert result.migrated == 1
    assert result.conflicts == []
    assert result.deleted == 1

    async with KanbanSessionLocal() as s:
        keys = await _all_keys(s)
    assert keys == {"git:h/p": "1", "autodispatch:slug:my-app-2": "1"}


@pytest.mark.asyncio
async def test_migrate_keys_conflict_keeps_existing():
    """When the target key already exists with a different value, skip + log;
    neither the existing target nor the source row is destroyed."""
    async with KanbanSessionLocal() as s:
        await _seed(s, {
            "autodispatch:slug:old": "1",
            "autodispatch:git:new": "0",  # pre-existing target, different value
        })

    async with KanbanSessionLocal() as s:
        result = await migrate_project_keys(s, "slug:old", "git:new")

    assert result.migrated == 0
    assert result.deleted == 0
    assert result.conflicts == [("autodispatch:slug:old", "autodispatch:git:new")]

    async with KanbanSessionLocal() as s:
        keys = await _all_keys(s)
    # No data loss: both rows survive untouched.
    assert keys == {"autodispatch:slug:old": "1", "autodispatch:git:new": "0"}


@pytest.mark.asyncio
async def test_migrate_keys_generic_unknown_prefix():
    """The helper is generic: an arbitrary (non-META) prefix that embeds the
    project key is migrated just the same."""
    async with KanbanSessionLocal() as s:
        await _seed(s, {"some-future-flag:slug:my-app": "x"})

    async with KanbanSessionLocal() as s:
        result = await migrate_project_keys(s, "slug:my-app", "git:h/p")

    assert result.migrated == 1

    async with KanbanSessionLocal() as s:
        keys = await _all_keys(s)
    assert keys == {"some-future-flag:git:h/p": "x"}
