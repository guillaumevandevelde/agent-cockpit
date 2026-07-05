"""Edge cases for the kanban op pipeline: races, idempotency, HLC uniqueness,
project isolation. Complements the happy-path coverage in test_kanban_operations.py.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.kanban.models import KanbanCard, KanbanOp
from app.kanban.operations import apply_operation
from app.kanban.service import list_cards
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_duplicate_create_is_idempotent():
    # Replaying a create op for an id that already exists must be a no-op,
    # not a second card or an overwrite of the original title.
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id="fixed-id", payload={"title": "original"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id="fixed-id", payload={"title": "replayed"})
        await s.commit()
        cards = (await s.execute(select(KanbanCard))).scalars().all()
        assert len(cards) == 1
        assert cards[0].title == "original"


@pytest.mark.asyncio
async def test_move_on_missing_card_is_silently_ignored():
    # Race: a move op arrives for a card that was already deleted. It must not
    # raise and must not resurrect the card as a phantom row.
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id="ghost", payload={"column": "Doing"})
        await s.commit()
        assert await s.get(KanbanCard, "ghost") is None


@pytest.mark.asyncio
async def test_claim_on_missing_card_is_silently_ignored():
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id="ghost", payload={"claimed_by": "a@d"})
        await s.commit()
        assert await s.get(KanbanCard, "ghost") is None


@pytest.mark.asyncio
async def test_delete_on_missing_card_does_not_raise():
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="delete", entity_type="card",
            project_key="p", entity_id="ghost", payload={})
        await s.commit()  # no exception == pass


@pytest.mark.asyncio
async def test_release_without_prior_claim_is_a_safe_noop():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="p", entity_id=cid, payload={})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by is None


@pytest.mark.asyncio
async def test_stale_release_does_not_clear_a_newer_claim():
    # A release whose HLC predates the current claim must lose the LWW race and
    # leave the owner intact (out-of-order delivery of a release op).
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        card = await s.get(KanbanCard, cid)
        card.claimed_by = "owner@dev"
        card.claim_hlc = "9999999999999:00000:dev-z"  # far-future claim
        await s.flush()
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="p", entity_id=cid, payload={})
        await s.commit()
        refreshed = await s.get(KanbanCard, cid)
        assert refreshed.claimed_by == "owner@dev"  # stale release rejected


@pytest.mark.asyncio
async def test_rapid_ops_get_unique_monotonic_hlcs():
    # The in-process clock's logical counter must keep HLCs unique and strictly
    # increasing even when many ops land within the same physical millisecond.
    async with KanbanSessionLocal() as s:
        for i in range(30):
            await apply_operation(s, op_type="create", entity_type="card",
                project_key="p", entity_id=None, payload={"title": f"c{i}"})
        await s.commit()
        hlcs = [o.hlc for o in (await s.execute(
            select(KanbanOp).order_by(KanbanOp.seq.asc()))).scalars().all()]
        assert len(hlcs) == 30
        assert len(set(hlcs)) == 30  # no duplicate HLCs
        assert hlcs == sorted(hlcs)  # strictly monotonic in apply order


@pytest.mark.asyncio
async def test_cards_are_isolated_per_project_key():
    # Two projects must not see each other's cards even though they share the
    # same materialized table (no project_key collisions on read).
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="git:proj-a", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="git:proj-b", entity_id=None, payload={"title": "b"})
        await s.commit()
        a_cards = await list_cards(s, "git:proj-a")
        b_cards = await list_cards(s, "git:proj-b")
        assert [c.title for c in a_cards] == ["a"]
        assert [c.title for c in b_cards] == ["b"]
