"""Cascade-delete behaviour for DB relations (prevents orphaned records).

PresenceEvent rows must be removed when their parent PresenceSession is deleted,
regardless of whether the delete goes through a bulk DELETE statement (as
``remove_session`` / ``clear_all_sessions`` do) or an ORM ``session.delete()``
(as the stopped-session cleanup does). The cascade is enforced at the DB level
via a ``ondelete="CASCADE"`` foreign key, with ``PRAGMA foreign_keys=ON``.
"""
import types
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.database import AsyncSessionLocal, Base, engine
from app.models.database import PresenceEvent, PresenceSession
from app.services.presence_service import PresenceService


async def _create_db():
    # The shared test DB may already hold pre-FK presence tables; create_all
    # never ALTERs existing tables, so drop the two presence tables (child
    # first) and recreate them to pick up the cascade FK.
    async with engine.begin() as conn:
        await conn.run_sync(PresenceEvent.__table__.drop, checkfirst=True)
        await conn.run_sync(PresenceSession.__table__.drop, checkfirst=True)
        await conn.run_sync(Base.metadata.create_all)


async def _seed(db, session_id: str, n_events: int = 3):
    db.add(PresenceSession(session_id=session_id))
    for i in range(n_events):
        db.add(PresenceEvent(session_id=session_id, event_type=f"E{i}"))
    await db.flush()


async def _event_count(db, session_id: str) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(PresenceEvent)
        .where(PresenceEvent.session_id == session_id)
    )
    return result.scalar() or 0


@pytest.mark.asyncio
async def test_bulk_delete_session_cascades_to_events():
    await _create_db()
    sid = f"cascade-bulk-{uuid.uuid4()}"
    async with AsyncSessionLocal() as db:
        await _seed(db, sid)
        assert await _event_count(db, sid) == 3

        # Mirrors PresenceService.remove_session (bulk DELETE statement).
        await db.execute(delete(PresenceSession).where(PresenceSession.session_id == sid))
        await db.commit()

    async with AsyncSessionLocal() as db:
        assert await _event_count(db, sid) == 0


@pytest.mark.asyncio
async def test_orm_delete_session_cascades_to_events():
    await _create_db()
    sid = f"cascade-orm-{uuid.uuid4()}"
    async with AsyncSessionLocal() as db:
        await _seed(db, sid)

        # Mirrors _remove_completed_sessions (ORM delete of a loaded row).
        session = (
            await db.execute(
                select(PresenceSession).where(PresenceSession.session_id == sid)
            )
        ).scalar_one()
        await db.delete(session)
        await db.commit()

    async with AsyncSessionLocal() as db:
        assert await _event_count(db, sid) == 0


@pytest.mark.asyncio
async def test_event_insert_ordered_after_session(monkeypatch):
    """A brand-new session + its first event commit together without an FK
    violation (the unit of work must insert the session before the event)."""
    await _create_db()
    sid = f"cascade-new-{uuid.uuid4()}"
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        await service.process_event(
            {"session_id": sid, "hook_event_name": "UserPromptSubmit", "cwd": "/x"},
            db,
        )
        await db.commit()
        assert await _event_count(db, sid) == 1


@pytest.mark.asyncio
async def test_rejected_session_does_not_write_orphan_event(monkeypatch):
    """Under memory pressure a new session is rejected; no parentless event may
    be written (it would violate the cascade FK on commit)."""
    await _create_db()
    sid = f"cascade-reject-{uuid.uuid4()}"
    monkeypatch.setattr(
        "app.services.presence_service.get_dynamic_limits",
        lambda: types.SimpleNamespace(max_active_sessions=0),
    )
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        resp = await service.process_event(
            {"session_id": sid, "hook_event_name": "UserPromptSubmit", "cwd": "/x"},
            db,
        )
        await db.commit()  # must not raise an IntegrityError

    assert resp.status_text == "Rejected: session limit"
    async with AsyncSessionLocal() as db:
        assert await _event_count(db, sid) == 0
        session = (
            await db.execute(
                select(PresenceSession).where(PresenceSession.session_id == sid)
            )
        ).scalar_one_or_none()
        assert session is None
