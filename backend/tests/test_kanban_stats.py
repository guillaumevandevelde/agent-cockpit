# backend/tests/test_kanban_stats.py
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from tests.kanban_test_db import TestSessionLocal, reset_test_tables
from app.kanban.operations import apply_operation
from app.kanban import service, stats

KanbanSessionLocal = TestSessionLocal()

T0 = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def _op(card_id, seq, op_type, payload, *, minutes=0):
    """Synthetic op; hlc just needs to sort, created_at drives durations."""
    return SimpleNamespace(
        entity_id=card_id,
        op_type=op_type,
        payload=payload,
        hlc=f"{seq:04d}",
        created_at=T0 + timedelta(minutes=minutes),
    )


def _card(card_id, agent=None):
    return SimpleNamespace(id=card_id, agent=agent)


def test_session_of_folder():
    assert stats.session_of_folder(
        "-home-guillaume-dev-proj--claude-worktrees-k-foo-1a2b"
    ) == "k-foo-1a2b"
    assert stats.session_of_folder("-home-guillaume-dev-proj") is None


def test_is_agent_column():
    assert stats.is_agent_column("developer")
    assert not stats.is_agent_column("Backlog")
    assert not stats.is_agent_column("Done")
    assert not stats.is_agent_column(None)


def test_completed_task_with_duration():
    card = _card("c1")
    ops = [
        _op("c1", 1, "create", {"column": "Backlog"}, minutes=0),
        _op("c1", 2, "move", {"column": "developer"}, minutes=2),
        _op("c1", 3, "claim", {"claimed_by": "agent:k-dev-0001"}, minutes=2),
        _op("c1", 4, "move", {"column": "Done"}, minutes=12),
    ]
    out = stats.compute_core_stats([card], ops)
    assert out["totals"]["total_tasks"] == 1
    assert out["totals"]["completed"] == 1
    assert out["totals"]["failed"] == 0
    dev = next(a for a in out["agents"] if a["agent"] == "developer")
    assert dev["completed"] == 1
    assert dev["success_rate"] == 1.0
    assert dev["avg_duration_seconds"] == 600.0  # 10 minutes in developer column
    assert out["session_to_agent"] == {"k-dev-0001": "developer"}


def test_failed_task_and_common_failures():
    card = _card("c2")
    ops = [
        _op("c2", 1, "create", {"column": "Backlog"}),
        _op("c2", 2, "move", {"column": "developer"}, minutes=1),
        _op("c2", 3, "comment", {"text": "**Impediment:** need DB schema"}, minutes=3),
        _op("c2", 4, "move", {"column": "Impediment"}, minutes=3),
    ]
    out = stats.compute_core_stats([card], ops)
    assert out["totals"]["failed"] == 1
    assert out["totals"]["completed"] == 0
    dev = next(a for a in out["agents"] if a["agent"] == "developer")
    assert dev["failed"] == 1
    assert dev["success_rate"] == 0.0
    assert out["common_failures"] == [
        {"agent": "developer", "reason": "need DB schema", "count": 1}
    ]


def test_in_progress_segment_not_counted_as_task():
    card = _card("c3")
    ops = [
        _op("c3", 1, "create", {"column": "Backlog"}),
        _op("c3", 2, "move", {"column": "testing"}, minutes=1),
    ]
    out = stats.compute_core_stats([card], ops)
    assert out["totals"]["total_tasks"] == 0
    assert out["totals"]["in_progress"] == 1
    testing = next(a for a in out["agents"] if a["agent"] == "testing")
    assert testing["in_progress"] == 1
    assert testing["avg_duration_seconds"] is None


def test_handoff_between_agents_counts_two_tasks():
    card = _card("c4")
    ops = [
        _op("c4", 1, "create", {"column": "Backlog"}),
        _op("c4", 2, "move", {"column": "analyst"}, minutes=0),
        _op("c4", 3, "move", {"column": "developer"}, minutes=5),
        _op("c4", 4, "move", {"column": "Done"}, minutes=20),
    ]
    out = stats.compute_core_stats([card], ops)
    assert out["totals"]["total_tasks"] == 2
    assert out["totals"]["completed"] == 2
    analyst = next(a for a in out["agents"] if a["agent"] == "analyst")
    developer = next(a for a in out["agents"] if a["agent"] == "developer")
    assert analyst["avg_duration_seconds"] == 300.0   # 5 min
    assert developer["avg_duration_seconds"] == 900.0  # 15 min


def test_agent_columns_excludes_legacy_columns():
    """With an explicit agent set, time in renamed/legacy columns is ignored."""
    card = _card("c5")
    ops = [
        _op("c5", 1, "create", {"column": "Backlog"}),
        _op("c5", 2, "move", {"column": "Doing"}, minutes=1),       # legacy column
        _op("c5", 3, "move", {"column": "developer"}, minutes=5),    # real agent
        _op("c5", 4, "move", {"column": "Done"}, minutes=10),
    ]
    out = stats.compute_core_stats([card], ops, agent_columns={"developer", "analyst"})
    assert [a["agent"] for a in out["agents"]] == ["developer"]
    assert out["totals"]["total_tasks"] == 1
    # Without the filter, the legacy column would be counted as an agent too.
    loose = stats.compute_core_stats([card], ops)
    assert {a["agent"] for a in loose["agents"]} == {"Doing", "developer"}


def test_failure_counts_group_by_reason():
    cards = [_card("a"), _card("b")]
    ops = []
    for cid in ("a", "b"):
        ops += [
            _op(cid, 1, "create", {"column": "Backlog"}),
            _op(cid, 2, "move", {"column": "developer"}, minutes=1),
            _op(cid, 3, "comment", {"text": "**Impediment:** flaky test"}, minutes=2),
            _op(cid, 4, "move", {"column": "Impediment"}, minutes=2),
        ]
    out = stats.compute_core_stats(cards, ops)
    assert out["common_failures"] == [
        {"agent": "developer", "reason": "flaky test", "count": 2}
    ]


def test_apply_token_usage():
    agents = [
        {"agent": "developer", "input_tokens": 0, "output_tokens": 0,
         "cache_creation_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0},
    ]
    matched = stats.apply_token_usage(agents, {
        "developer": {"input_tokens": 100, "output_tokens": 50,
                      "cache_creation_tokens": 10, "cache_read_tokens": 5},
        "ghost": {"input_tokens": 999},  # no matching agent row
    })
    assert matched is True
    assert agents[0]["total_tokens"] == 165
    assert agents[0]["input_tokens"] == 100


@pytest.mark.asyncio
async def test_apply_token_usage_empty():
    assert stats.apply_token_usage([], {}) is False
    assert await stats.gather_token_usage({}) == {}


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_list_project_ops_joins_by_card_id():
    """Move/claim ops carry project_key='' — the service must still find them."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="P", entity_id=None, payload={"title": "t", "column": "Backlog"})
        # router passes project_key="" for these ops in production
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "developer"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="", entity_id=cid, payload={"claimed_by": "agent:k-t-0001"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Done"})
        await s.commit()

        cards, ops = await service.list_project_ops(s, "P")
        assert len(cards) == 1
        op_types = sorted(o.op_type for o in ops)
        assert op_types == ["claim", "create", "move", "move"]

        out = stats.compute_core_stats(cards, ops)
        assert out["totals"]["completed"] == 1
        assert out["session_to_agent"] == {"k-t-0001": "developer"}
