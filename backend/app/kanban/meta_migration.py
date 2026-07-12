"""KanbanMeta key-migration helper.

When a project gains a GitHub remote (``gh repo create``) its identity changes
from the pre-remote ``slug:<name>`` key to the post-remote ``git:<host>/<path>``
key. Every per-project flag lives in ``KanbanMeta`` under a prefixed key
(``autodispatch:<project_key>``, ``shipmode:<project_key>``,
``skip_permissions:<project_key>``, ``transport:<project_key>``, …), so each one
must follow the project to its new key.

``migrate_project_keys`` renames the embedded ``old_key`` segment to ``new_key``
across every ``KanbanMeta`` row, atomically (single commit) and idempotently.

Matching is **segment-boundary aware**: ``old_key`` only matches where it sits
between key-segment boundaries (start-of-string or ``:`` before, end-of-string
or ``:`` after). This keeps a rename of ``slug:my-app`` from bleeding into the
sibling project ``slug:my-app-2`` — a plain ``LIKE 'slug:my-app%'`` would wrongly
catch it. The four META prefixes are coincidental: the helper is generic and
never hardcodes a prefix list.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kanban.models import KanbanMeta


@dataclass(frozen=True)
class MigrationResult:
    migrated: int = 0
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    deleted: int = 0


def _bounded_index(key: str, needle: str) -> int | None:
    """Index of the first segment-bounded occurrence of ``needle`` in ``key``.

    An occurrence counts only when it is preceded by start-of-string or ``:`` and
    followed by end-of-string or ``:``. Returns ``None`` when there is none.
    """
    if not needle:
        return None
    start = 0
    while True:
        idx = key.find(needle, start)
        if idx == -1:
            return None
        end = idx + len(needle)
        before_ok = idx == 0 or key[idx - 1] == ":"
        after_ok = end == len(key) or key[end] == ":"
        if before_ok and after_ok:
            return idx
        start = idx + 1


async def migrate_project_keys(
    session: AsyncSession, old_key: str, new_key: str
) -> MigrationResult:
    """Rename every ``KanbanMeta`` row whose key embeds ``old_key`` to ``new_key``.

    - ``old_key == new_key`` -> immediate no-op (idempotency).
    - A target key that already exists is a **conflict**: it is logged in
      ``MigrationResult.conflicts`` and skipped; neither the existing target nor
      the source row is destroyed (no data loss — a follow-up can reconcile).
    """
    if old_key == new_key:
        return MigrationResult()

    rows = (await session.execute(select(KanbanMeta))).scalars().all()
    existing_keys = {r.key for r in rows}

    conflicts: list[tuple[str, str]] = []
    planned: dict[KanbanMeta, str] = {}
    planned_new: set[str] = set()
    for row in rows:
        idx = _bounded_index(row.key, old_key)
        if idx is None:
            continue
        new_row_key = row.key[:idx] + new_key + row.key[idx + len(old_key):]
        if new_row_key in existing_keys or new_row_key in planned_new:
            conflicts.append((row.key, new_row_key))
            continue
        planned[row] = new_row_key
        planned_new.add(new_row_key)

    if not planned and not conflicts:
        return MigrationResult()

    for row, new_row_key in planned.items():
        session.add(KanbanMeta(key=new_row_key, value=row.value))
    await session.flush()
    for row in planned:
        await session.delete(row)
    await session.commit()

    return MigrationResult(
        migrated=len(planned), conflicts=conflicts, deleted=len(planned)
    )
