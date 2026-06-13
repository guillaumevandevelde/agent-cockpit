"""Read-side queries over the materialized state + op-log activity feed."""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban.models import KanbanCard, KanbanOp


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
