"""Stale-project detection tests.

Covers the two acceptance scenarios (last Done 25h ago + non-empty Backlog →
comment; a still-fresh flag → no second comment) plus the guards: no Backlog,
a project that finished recently, and a never-finished project anchored to its
oldest card.
"""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.config import settings
from app.kanban.hlc import _format as hlc_format
from app.kanban.models import KanbanCard, KanbanMeta, KanbanOp
from app.kanban.stale_detection import (
    STALE_COMMENT_PREFIX,
    _check_project,
    _stale_meta_key,
    run_stale_detection_tick,
)
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def _card(cid: str, column: str, *, age_hours: float, key: str = PK) -> KanbanCard:
    created = datetime.now(UTC) - timedelta(hours=age_hours)
    return KanbanCard(
        id=cid, project_key=key, title=cid, column=column, rank=cid,
        created_at=created, updated_at=created,
    )


def _move_op(oid: str, card_id: str, column: str, *, age_hours: float) -> KanbanOp:
    seq = int(oid.rsplit(":", 1)[1])
    created = datetime.now(UTC) - timedelta(hours=age_hours)
    return KanbanOp(
        op_id=oid, device_id="dev", seq=seq,
        # `hlc` must be a real HLC, not the op_id. apply_operation seeds its
        # clock from the highest hlc in the table (operations._clock_for), and
        # hlc._physical/_logical int()-parse the first two colon-separated
        # fields -- so an op_id-shaped value like "dev:1" made any later
        # apply_operation in the same test raise
        # ValueError: invalid literal for int() with base 10: 'dev'.
        hlc=hlc_format(int(created.timestamp() * 1000), seq, "dev"),
        project_key="", entity_type="card", entity_id=card_id,
        op_type="move", payload={"column": column},
        created_at=created,
    )


async def _comment_texts(s, card_id: str) -> list[str]:
    from sqlalchemy import select
    rows = (
        await s.execute(
            select(KanbanOp.payload).where(
                KanbanOp.entity_id == card_id, KanbanOp.op_type == "comment"
            )
        )
    ).all()
    return [(p or {}).get("text", "") for (p,) in rows]


@pytest.mark.asyncio
async def test_stale_project_gets_comment():
    async with KanbanSessionLocal() as s:
        s.add_all([
            _card("b1", "Backlog", age_hours=30),
            _card("d1", "Done", age_hours=25),
            _move_op("dev:1", "d1", "Done", age_hours=25),
        ])
        await s.commit()

        posted = await _check_project(s, PK)
        await s.commit()

    assert posted is True
    async with KanbanSessionLocal() as s:
        texts = await _comment_texts(s, "b1")
        assert any(t.startswith(STALE_COMMENT_PREFIX) for t in texts)
        assert await s.get(KanbanMeta, _stale_meta_key(PK, "b1")) is not None


@pytest.mark.asyncio
async def test_no_second_comment_within_window():
    async with KanbanSessionLocal() as s:
        s.add_all([
            _card("b1", "Backlog", age_hours=30),
            _card("d1", "Done", age_hours=25),
            _move_op("dev:1", "d1", "Done", age_hours=25),
        ])
        # Already flagged 1h ago — still stale, but inside the dedup window.
        s.add(KanbanMeta(
            key=_stale_meta_key(PK, "b1"),
            value=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        ))
        await s.commit()

        posted = await _check_project(s, PK)
        await s.commit()

    assert posted is False
    async with KanbanSessionLocal() as s:
        assert await _comment_texts(s, "b1") == []


@pytest.mark.asyncio
async def test_recent_done_is_not_stale():
    async with KanbanSessionLocal() as s:
        s.add_all([
            _card("b1", "Backlog", age_hours=30),
            _card("d1", "Done", age_hours=2),
            _move_op("dev:1", "d1", "Done", age_hours=2),
        ])
        await s.commit()

        assert await _check_project(s, PK) is False


@pytest.mark.asyncio
async def test_empty_backlog_is_not_stale():
    async with KanbanSessionLocal() as s:
        s.add_all([
            _card("d1", "Done", age_hours=48),
            _move_op("dev:1", "d1", "Done", age_hours=48),
        ])
        await s.commit()

        assert await _check_project(s, PK) is False


@pytest.mark.asyncio
async def test_never_finished_project_anchors_to_oldest_card():
    async with KanbanSessionLocal() as s:
        # No Done-move ever, but the Backlog card is well past the threshold.
        s.add(_card("b1", "Backlog", age_hours=48))
        await s.commit()

        assert await _check_project(s, PK) is True


@pytest.mark.asyncio
async def test_fresh_never_finished_project_not_flagged():
    async with KanbanSessionLocal() as s:
        s.add(_card("b1", "Backlog", age_hours=1))
        await s.commit()

        assert await _check_project(s, PK) is False


@pytest.mark.asyncio
async def test_tick_only_scans_autodispatch_enabled_projects():
    other = "git:example.com/me/other"
    async with KanbanSessionLocal() as s:
        s.add_all([
            _card("b1", "Backlog", age_hours=48),
            _card("x1", "Backlog", age_hours=48, key=other),
            KanbanMeta(key="autodispatch:" + PK, value="1"),
            KanbanMeta(key="autodispatch:" + other, value="0"),
        ])
        await s.commit()

    await run_stale_detection_tick()

    async with KanbanSessionLocal() as s:
        assert any(
            t.startswith(STALE_COMMENT_PREFIX) for t in await _comment_texts(s, "b1")
        )
        assert await _comment_texts(s, "x1") == []


def test_comment_template_formats():
    text = settings.stale_comment_template.format(hours=25, backlog=3)
    assert text.startswith(STALE_COMMENT_PREFIX)
    assert "25" in text
    assert "3" in text
