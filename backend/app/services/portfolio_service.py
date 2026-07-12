"""Read-only portfolio aggregation across all registered projects.

One answer instead of N separate ``GET /cards`` calls: per project the kanban
column counts, a 24h done-rate, plus last-activity / last-dispatch timestamps,
and a portfolio-wide sum. Purely additive and read-only — no writes, no
dispatch governance (see docs/cockpit/portfolio-orchestratie.md §4 option 1).

Query budget is flat, never per-project (no N+1):
  * one query over the main-DB ``projects`` table,
  * one bulk query over every ``KanbanCard``,
  * one bulk query over the ``KanbanOp`` log,
  * one bulk query over ``KanbanMeta`` (autodispatch flags).
Everything else is Python aggregation.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kanban.models import KanbanCard, KanbanMeta, KanbanOp
from app.kanban.project_key import resolve_project_key
from app.kanban.stats import AGENT_CLAIM_PREFIX
from app.models.database import Project

# Device-local autodispatch flag, stored in KanbanMeta as
# ``autodispatch:<project_key>`` = "1" | "0". Mirrors dispatch.META_PREFIX;
# duplicated here to avoid importing the heavy dispatch module for one string.
_AUTODISPATCH_PREFIX = "autodispatch:"

# Cards moved to Done within this window count toward ``done_24h``.
_DONE_WINDOW = timedelta(hours=24)


class PortfolioTotals(BaseModel):
    backlog: int = 0
    todo: int = 0
    doing: int = 0
    impediment: int = 0
    done_24h: int = 0


class PortfolioProject(BaseModel):
    id: int | None
    name: str
    kind: str
    project_key: str
    autodispatch_enabled: bool
    totals: PortfolioTotals
    last_activity: str | None
    last_dispatch: str | None


class PortfolioOverview(BaseModel):
    projects: list[PortfolioProject]
    totals: PortfolioTotals


def _bucket(column: str | None) -> str | None:
    """Map a card's column name to a portfolio bucket.

    The board's fixed columns are intake/Backlog/Impediment/Done/To Resume plus
    per-persona agent columns. Agent columns (and any custom column) count as
    ``doing`` — a card sits on its agent's column while being worked. Done is
    tracked separately via the op-log (``done_24h``), so it maps to None here.
    """
    low = (column or "").lower()
    if low == "backlog":
        return "backlog"
    if low == "impediment":
        return "impediment"
    if low == "done":
        return None
    if low in ("intake", "to resume", "todo", "to do"):
        return "todo"
    return "doing"


def _aware(dt: datetime) -> datetime:
    """Coerce a naive DB timestamp to UTC so it compares with ``now(UTC)``.

    SQLite drops tzinfo on write, so ``DateTime(timezone=True)`` rows read back
    naive — mirror dispatch.py's ``replace(tzinfo=UTC)`` guard.
    """
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


class PortfolioService:
    """Aggregate every project's kanban stats into one read-only overview."""

    def __init__(self, db: AsyncSession, kanban: AsyncSession):
        self.db = db
        self.kanban = kanban

    async def aggregate(self, *, key_resolver=None) -> PortfolioOverview:
        resolver = key_resolver or resolve_project_key
        projects = list((await self.db.execute(select(Project))).scalars().all())
        project_by_key: dict[str, Project] = {}
        for p in projects:
            project_by_key.setdefault(resolver(p.path), p)

        card_rows = (
            await self.kanban.execute(
                select(KanbanCard.id, KanbanCard.project_key, KanbanCard.column)
            )
        ).all()
        key_by_card: dict[str, str] = {}
        totals_by_key: dict[str, PortfolioTotals] = defaultdict(PortfolioTotals)
        keys_with_cards: set[str] = set()
        for card_id, project_key, column in card_rows:
            key_by_card[card_id] = project_key
            keys_with_cards.add(project_key)
            bucket = _bucket(column)
            if bucket is not None:
                totals = totals_by_key[project_key]
                setattr(totals, bucket, getattr(totals, bucket) + 1)

        op_rows = (
            await self.kanban.execute(
                select(
                    KanbanOp.entity_id,
                    KanbanOp.op_type,
                    KanbanOp.created_at,
                    KanbanOp.payload,
                )
            )
        ).all()
        cutoff = datetime.now(UTC) - _DONE_WINDOW
        last_activity: dict[str, datetime] = {}
        last_dispatch: dict[str, datetime] = {}
        done_recent: dict[str, set[str]] = defaultdict(set)
        for entity_id, op_type, created_at, payload in op_rows:
            key = key_by_card.get(entity_id)
            if key is None:
                continue
            created_at = _aware(created_at)
            if last_activity.get(key) is None or created_at > last_activity[key]:
                last_activity[key] = created_at
            payload = payload or {}
            if op_type == "claim":
                claimed_by = payload.get("claimed_by") or ""
                if claimed_by.startswith(AGENT_CLAIM_PREFIX) and (
                    last_dispatch.get(key) is None or created_at > last_dispatch[key]
                ):
                    last_dispatch[key] = created_at
            elif op_type == "move" and payload.get("column") == "Done" and created_at >= cutoff:
                done_recent[key].add(entity_id)

        meta_rows = (await self.kanban.execute(select(KanbanMeta))).scalars().all()
        enabled_keys = {
            r.key[len(_AUTODISPATCH_PREFIX):]
            for r in meta_rows
            if r.key.startswith(_AUTODISPATCH_PREFIX) and r.value == "1"
        }

        all_keys = set(project_by_key) | keys_with_cards
        rows: list[PortfolioProject] = []
        grand = PortfolioTotals()
        for key in all_keys:
            proj = project_by_key.get(key)
            totals = totals_by_key.get(key, PortfolioTotals())
            totals.done_24h = len(done_recent.get(key, ()))
            rows.append(
                PortfolioProject(
                    id=proj.id if proj else None,
                    name=proj.name if proj else key,
                    kind=proj.kind if proj else "unknown",
                    project_key=key,
                    autodispatch_enabled=key in enabled_keys,
                    totals=totals,
                    last_activity=_iso(last_activity.get(key)),
                    last_dispatch=_iso(last_dispatch.get(key)),
                )
            )
            for field in ("backlog", "todo", "doing", "impediment", "done_24h"):
                setattr(grand, field, getattr(grand, field) + getattr(totals, field))

        rows.sort(key=lambda r: (r.name.lower(), r.project_key))
        return PortfolioOverview(projects=rows, totals=grand)
