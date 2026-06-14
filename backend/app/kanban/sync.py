"""Sync seam (not activated in v1). The op-log is append-only, so a sync
transport only needs: pull foreign ops, push local ops. Conflict logic lives
in materialization (operations.py), not here.
"""
from typing import Protocol

from sqlalchemy import select

from app.kanban.hlc import HLC
from app.kanban.models import KanbanOp
from app.kanban.operations import _clock_for, rematerialize  # reuse the clock


async def ops_since(session, cursor: str | None):
    """Return ops with hlc strictly greater than cursor, in hlc order."""
    stmt = select(KanbanOp).order_by(KanbanOp.hlc.asc())
    if cursor is not None:
        stmt = stmt.where(KanbanOp.hlc > cursor)
    return (await session.execute(stmt)).scalars().all()


async def ingest_ops(session, ops: list[dict]) -> int:
    """Insert foreign ops idempotently (by op_id), advance the clock past
    them, then rebuild materialized state. Returns count of newly inserted.
    """
    clock: HLC = await _clock_for(session)
    inserted = 0
    for op in ops:
        if await session.get(KanbanOp, op["op_id"]) is not None:
            continue
        session.add(KanbanOp(
            op_id=op["op_id"], device_id=op["device_id"], seq=op["seq"],
            hlc=op["hlc"], project_key=op["project_key"],
            entity_type=op["entity_type"], entity_id=op["entity_id"],
            op_type=op["op_type"], payload=op["payload"],
        ))
        clock.update(op["hlc"])
        inserted += 1
    await session.flush()
    if inserted:
        await rematerialize(session)
    return inserted


class SyncTransport(Protocol):
    async def pull(self, cursor: str | None) -> list[dict]: ...
    async def push(self, ops: list[dict]) -> None: ...


class LocalNoopTransport:
    """Default transport in v1: no remote. pull returns nothing, push drops."""
    async def pull(self, cursor: str | None) -> list[dict]:
        return []

    async def push(self, ops: list[dict]) -> None:
        return None
