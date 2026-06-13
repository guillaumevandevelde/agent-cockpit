"""Single mutation pipeline + materialization.

apply_operation(): assign HLC -> append KanbanOp -> update materialized state.
All writes (REST and MCP) go through here. rematerialize() rebuilds the
materialized tables from the op-log (added in Task E5).
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func

from app.kanban.hlc import HLC, hlc_max
from app.kanban.models import KanbanCard, KanbanDeliverable, KanbanMeta, KanbanOp

# One in-process clock per backend. node_id is bound lazily to the device_id.
_clock: Optional[HLC] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_device_id(session) -> str:
    row = await session.get(KanbanMeta, "device_id")
    if row is None:
        row = KanbanMeta(key="device_id", value=uuid.uuid4().hex[:12])
        session.add(row)
        await session.flush()
    return row.value


async def _clock_for(session) -> HLC:
    global _clock
    device_id = await get_device_id(session)
    if _clock is None or _clock.node_id != device_id:
        _clock = HLC(node_id=device_id)
        # Seed past the highest HLC already stored so restarts stay monotonic.
        highest = (await session.execute(select(func.max(KanbanOp.hlc)))).scalar()
        if highest:
            _clock.update(highest)
    return _clock


async def _next_seq(session, device_id: str) -> int:
    n = (await session.execute(
        select(func.count()).select_from(KanbanOp).where(KanbanOp.device_id == device_id)
    )).scalar() or 0
    return n + 1


async def apply_operation(
    session, *, op_type: str, entity_type: str, project_key: str,
    entity_id: Optional[str], payload: dict,
) -> str:
    """Append an op and fold it into materialized state. Returns entity_id."""
    clock = await _clock_for(session)
    device_id = await get_device_id(session)
    hlc = clock.tick()
    entity_id = entity_id or uuid.uuid4().hex
    seq = await _next_seq(session, device_id)

    session.add(KanbanOp(
        op_id=f"{device_id}:{seq}", device_id=device_id, seq=seq, hlc=hlc,
        project_key=project_key, entity_type=entity_type, entity_id=entity_id,
        op_type=op_type, payload=payload,
    ))
    await session.flush()
    await _materialize(session, op_type=op_type, entity_type=entity_type,
                       project_key=project_key, entity_id=entity_id,
                       payload=payload, hlc=hlc)
    return entity_id


async def _materialize(session, *, op_type, entity_type, project_key,
                       entity_id, payload, hlc) -> None:
    if entity_type == "card" and op_type == "create":
        existing = await session.get(KanbanCard, entity_id)
        if existing is None:  # idempotent: re-applying create is a no-op
            session.add(KanbanCard(
                id=entity_id, project_key=project_key,
                title=payload.get("title", ""),
                description=payload.get("description", ""),
                column=payload.get("column", "Backlog"),
                rank=payload.get("rank", hlc),
                priority=payload.get("priority"), labels=payload.get("labels"),
                title_hlc=hlc, description_hlc=hlc, column_hlc=hlc, rank_hlc=hlc,
            ))
            await session.flush()
        return
    # other op types added in Tasks E2-E4
