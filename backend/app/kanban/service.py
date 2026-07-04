"""Read-side queries over the materialized state + op-log activity feed."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban.models import KanbanCard, KanbanColumn, KanbanGate, KanbanOp


async def list_cards(session, project_key: str, column: str | None = None):
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.project_key == project_key)
        .options(selectinload(KanbanCard.deliverables))
        .order_by(KanbanCard.rank.asc())
    )
    if column is not None:
        stmt = stmt.where(KanbanCard.column == column)
    return (await session.execute(stmt)).scalars().all()


async def get_card(session, card_id: str):
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.id == card_id)
        .options(selectinload(KanbanCard.deliverables))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def card_activity(session, card_id: str):
    stmt = (
        select(KanbanOp)
        .where(KanbanOp.entity_id == card_id)
        .order_by(KanbanOp.hlc.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def list_project_ops(session, project_key: str):
    """All op-log entries for a project's cards. Ops carry project_key="" for
    move/claim/comment (set by the router), so we join by card id instead."""
    cards = await list_cards(session, project_key)
    ids = [c.id for c in cards]
    if not ids:
        return cards, []
    stmt = select(KanbanOp).where(KanbanOp.entity_id.in_(ids))
    ops = (await session.execute(stmt)).scalars().all()
    return cards, ops


# Column management


async def list_columns(session, project_key: str):
    stmt = (
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .order_by(KanbanColumn.rank.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def get_column(session, column_id: str):
    return await session.get(KanbanColumn, column_id)


async def create_column(session, project_key: str, name: str,
                        rank: str | None = None, default_agent: str | None = None):
    col = KanbanColumn(
        id=uuid.uuid4().hex,
        project_key=project_key,
        name=name,
        rank=rank or uuid.uuid4().hex,
        default_agent=default_agent,
    )
    session.add(col)
    await session.flush()
    return col


async def update_column(session, column_id: str, **kwargs):
    col = await session.get(KanbanColumn, column_id)
    if col is None:
        return None
    for k, v in kwargs.items():
        if v is not None:
            setattr(col, k, v)
    col.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return col


async def delete_column(session, column_id: str) -> bool:
    col = await session.get(KanbanColumn, column_id)
    if col is None:
        return False
    await session.delete(col)
    await session.flush()
    return True


async def get_column_default_agent(session, project_key: str, column_name: str) -> str | None:
    """Look up the default agent for a column name within a project."""
    stmt = (
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .where(KanbanColumn.name == column_name)
    )
    col = (await session.execute(stmt)).scalar_one_or_none()
    return col.default_agent if col else None


async def list_pending_cards(session, project_key: str) -> list[KanbanCard]:
    """Unclaimed cards in Backlog or To Resume — candidates for dispatch.

    To Resume cards carry a `resume_session_id` set by the limit-recovery path,
    so dispatching them goes through the resume transport (`get_transport_for_card`)
    instead of spawning a fresh session.
    """
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.project_key == project_key)
        .where(KanbanCard.column.in_(("Backlog", "To Resume")))
        .where(KanbanCard.claimed_by.is_(None))
        .options(selectinload(KanbanCard.deliverables))
        .order_by(KanbanCard.rank.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def list_orphaned_cards(session, project_key: str) -> list[KanbanCard]:
    """Cards on agent columns (not Backlog/Dispatch/Impediment/Done) that are unclaimed."""
    from app.kanban.schemas import COLUMNS
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.project_key == project_key)
        .where(KanbanCard.claimed_by.is_(None))
        .where(~KanbanCard.column.in_(COLUMNS))
        .options(selectinload(KanbanCard.deliverables))
        .order_by(KanbanCard.rank.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def sync_agent_columns(session, project_key: str, agents: list[str]) -> None:
    """Sync agent columns with the list of agents for a project.
    
    Creates columns for agents that don't have one yet.
    Does not remove columns for agents that are no longer in the list
    (to preserve card references).
    """
    from app.kanban.schemas import COLUMNS
    
    existing = await list_columns(session, project_key)
    existing_names = {c.name for c in existing}
    
    # Find the rank of "Done" column to insert agent columns before it
    done_rank = "9999"
    for col in existing:
        if col.name == "Done":
            done_rank = col.rank
            break
    
    # Create columns for agents that don't have one yet
    for i, agent_name in enumerate(agents):
        if agent_name not in existing_names:
            # Insert before Done: rank between last fixed column and Done
            rank = f"{int(done_rank) - len(agents) + i:04d}" if done_rank != "9999" else f"0{len(COLUMNS) + i:03d}"
            await create_column(session, project_key, name=agent_name, rank=rank, default_agent=agent_name)

    await session.flush()


# Decision gates


async def create_gate(session, card_id: str, project_key: str,
                      question: str, options: list[str]) -> KanbanGate:
    gate = KanbanGate(
        id=uuid.uuid4().hex,
        card_id=card_id,
        project_key=project_key,
        question=question,
        options=options,
        status="open",
    )
    session.add(gate)
    await session.flush()
    return gate


async def get_gate(session, gate_id: str) -> KanbanGate | None:
    return await session.get(KanbanGate, gate_id)


async def list_gates(session, card_id: str) -> list[KanbanGate]:
    stmt = (
        select(KanbanGate)
        .where(KanbanGate.card_id == card_id)
        .order_by(KanbanGate.created_at.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def answer_gate(session, gate_id: str, answer: str) -> KanbanGate | None:
    """Record the human's answer. Idempotent: answering an already-answered
    gate again is a no-op that returns the existing (first) answer, so a
    double-click in the UI can't silently overwrite what was already recorded."""
    gate = await session.get(KanbanGate, gate_id)
    if gate is None:
        return None
    if gate.status == "answered":
        return gate
    if answer not in gate.options:
        raise ValueError("answer must be one of the gate's options")
    gate.answer = answer
    gate.status = "answered"
    gate.answered_at = datetime.now(timezone.utc)
    await session.flush()
    return gate
