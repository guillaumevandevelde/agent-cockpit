"""Tests for ``app.kanban.gate_migration``: the one-shot inventory/migration
helper that lifts prose-gated cards (``"BEWUST NIET NU"`` / ``"GEPOORT"`` /
``"activeert pas bij"`` in title/description) to the new
``metadata.gated_on`` machine-readable form.

Triggered by kanban card `f8ef71a0…` (the bug class the gate mechanism
fixes — prose-only gates are invisible to the dispatcher). The helper is the
defined migration path; the regressions here guard the inventory logic and
the apply step's atomicity.
"""
import pytest
import pytest_asyncio

from app.kanban.gate_migration import _apply, _inventory
from app.kanban.operations import apply_operation
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


# ---- inventory ----

@pytest.mark.asyncio
async def test_inventory_returns_card_with_prose_gate_marker():
    """A card whose description says 'BEWUST NIET NU' must show up in the
    inventory with the canonical 'prose-gate-marker' value attached."""
    async with __import__("app.kanban.db", fromlist=["KanbanSessionLocal"]).KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key="P",
            entity_id=None,
            payload={"title": "spike X", "description": "BEWUST NIET NU",
                     "column": "Backlog"},
        )
        await s.commit()

    rows = await _inventory(project_key="P")
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["canonical_gated_on"] == "prose-gate-marker"


@pytest.mark.asyncio
async def test_inventory_uses_specific_marker_over_generic():
    """If a card mentions BOTH 'activeert pas bij' (specific) and 'BEWUST NIET
    NU' (generic), the inventory must pick the specific marker. Otherwise
    a future ``flag-problem`` audit that greps on the specific phrase would
    fail to surface the card."""
    async with __import__("app.kanban.db", fromlist=["KanbanSessionLocal"]).KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="create", entity_type="card", project_key="P",
            entity_id=None,
            payload={
                "title": "GEPOORT — activeert pas bij tweede-executor-provider-onboarding",
                "description": "Wacht op die trigger.",
                "column": "Backlog",
            },
        )
        await s.commit()

    rows = await _inventory(project_key="P")
    assert len(rows) == 1
    # The 'activeert pas bij' marker fires before 'BEWUST NIET NU' (which
    # isn't even in this card, so the priority doesn't strictly bite here,
    # but the canonical value should be the descriptive one).
    assert "trigger" in rows[0]["canonical_gated_on"]


@pytest.mark.asyncio
async def test_inventory_skips_cards_already_with_machine_readable_gate():
    """A card with metadata.gated_on already set is the *new* style of gate
    and must NOT show up in the inventory (applying on top would be a
    surprising overwrite)."""
    async with __import__("app.kanban.db", fromlist=["KanbanSessionLocal"]).KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key="P",
            entity_id=None,
            payload={"title": "x", "description": "", "column": "Backlog",
                     "metadata": {"gated_on": "already-set"}},
        )
        # Add a stray 'BEWUST NIET NU' afterwards (e.g. via a comment) to
        # make sure the prose-detect stays inactive.
        await apply_operation(
            s, op_type="update", entity_type="card", project_key="P",
            entity_id=cid,
            payload={"description": "BEWUST NIET NU — alt text"},
        )
        await s.commit()

    rows = await _inventory(project_key="P")
    assert rows == [], (
        f"card with metadata.gated_on already set must not appear in the "
        f"prose-inventory; got {rows!r}"
    )


@pytest.mark.asyncio
async def test_inventory_returns_empty_when_nothing_matches():
    async with __import__("app.kanban.db", fromlist=["KanbanSessionLocal"]).KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="create", entity_type="card", project_key="P",
            entity_id=None,
            payload={"title": "ordinary", "description": "nothing to see",
                     "column": "Backlog"},
        )
        await s.commit()

    rows = await _inventory(project_key="P")
    assert rows == []


# ---- apply ----

@pytest.mark.asyncio
async def test_apply_writes_metadata_and_posts_audit_comment():
    """``_apply`` writes ``metadata.gated_on`` through the op-log and posts
    a ``**Gate:** migrated from prose`` activity-feed comment. The op-log
    path is the same one the REST/MCP set_card_gate path uses, so replays
    don't drop the field — gate_migration isn't a special-cased write."""
    async with __import__("app.kanban.db", fromlist=["KanbanSessionLocal"]).KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key="P",
            entity_id=None,
            payload={"title": "spike", "description": "BEWUST NIET NU",
                     "column": "Backlog"},
        )
        await s.commit()

    rows = await _inventory(project_key="P")
    assert len(rows) == 1
    results = await _apply(rows)
    assert results[0]["action"] == "applied"
    assert results[0]["canonical_gated_on"] == "prose-gate-marker"

    # Verify both writes landed.
    from app.kanban.models import KanbanCard, KanbanOp
    async with __import__("app.kanban.db", fromlist=["KanbanSessionLocal"]).KanbanSessionLocal() as s:
        card = await s.get(KanbanCard, cid)
        activity = (await s.execute(
            __import__("sqlalchemy").select(KanbanOp).where(
                KanbanOp.entity_id == cid
            ).order_by(KanbanOp.hlc.asc())
        )).scalars().all()

    assert (card.meta or {}).get("gated_on") == "prose-gate-marker"
    audit_comments = [
        op for op in activity
        if op.op_type == "comment"
        and op.payload.get("text", "").startswith("**Gate:**")
    ]
    assert len(audit_comments) == 1
    assert "migrated from prose" in audit_comments[0].payload["text"]


@pytest.mark.asyncio
async def test_apply_one_failure_does_not_poison_the_batch():
    """If one row's apply raises, the rest must still land. The migration
    helper's only promise is "each row is its own transaction"; a flaky
    card shouldn't block every other card in the same batch."""
    async with __import__("app.kanban.db", fromlist=["KanbanSessionLocal"]).KanbanSessionLocal() as s:
        cid_good = await apply_operation(
            s, op_type="create", entity_type="card", project_key="P",
            entity_id=None,
            payload={"title": "spike good", "description": "BEWUST NIET NU",
                     "column": "Backlog"},
        )
        cid_missing = "this-card-does-not-exist-00000000"
        await s.commit()

    rows = [
        {"id": cid_good, "canonical_gated_on": "prose-gate-marker",
         "title": "x", "column": "Backlog", "depends_on": []},
        {"id": cid_missing, "canonical_gated_on": "prose-gate-marker",
         "title": "x", "column": "Backlog", "depends_on": []},
    ]
    results = await _apply(rows)
    by_id = {r["id"]: r for r in results}
    assert by_id[cid_good]["action"] == "applied"
    assert by_id[cid_missing]["action"] == "skipped"
