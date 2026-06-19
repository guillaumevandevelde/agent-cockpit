"""Read-side queries over the materialized state + op-log activity feed."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban.models import KanbanCard, KanbanColumn, KanbanOp


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
