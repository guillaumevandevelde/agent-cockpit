# backend/tests/test_kanban_reviewer_gate.py
"""Independent reviewer gate — docs/cockpit/reviewer-agent-decision.md (REVISED
2026-07-18). A card reaching genuine Done in a project that has a `reviewer`
column is first routed through the reviewer; the reviewer's own Done move
passes straight through; analysis cards and parent cards are excluded; a
reviewer rejection restores the engineer so the resume fixes the work."""
import pytest
import pytest_asyncio

from app.kanban import mcp_server as m
from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import apply_operation
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _reviewer_column(project_key: str) -> None:
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key, name=service.REVIEWER_COLUMN,
                                     default_agent=service.REVIEWER_COLUMN)
        await s.commit()


async def _card_in_agent_column(project_key: str, *, agent: str = "engineer",
                                column: str = "engineer", work_type: str = "feature",
                                claimed_by: str = "agent:sess-x") -> str:
    cid = (await m.create_card(project_key, "Do the thing", "the wish",
                               work_type=work_type, confirm_new_project=True))["id"]
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid, payload={"agent": agent})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": column})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="", entity_id=cid, payload={"claimed_by": claimed_by})
        await s.commit()
    return cid


@pytest.mark.asyncio
async def test_done_redirects_to_reviewer_when_reviewer_column_exists():
    await _reviewer_column("P")
    cid = await _card_in_agent_column("P")
    result = await m.move_card(cid, "Done", summary="built and shipped it")
    assert result["column"] == service.REVIEWER_COLUMN
    assert result["agent"] == service.REVIEWER_COLUMN

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        ops = await service.card_activity(s, cid)
    # The persona that did the work is stashed for return-routing on rejection.
    assert (card.meta or {}).get(service.REVIEW_RETURN_AGENT_KEY) == "engineer"
    # The engineer's summary survives as a comment for the reviewer to read.
    assert any("**Summary:** built and shipped it" in (o.payload.get("text") or "")
               for o in ops if o.op_type == "comment")


@pytest.mark.asyncio
async def test_done_not_gated_without_reviewer_column():
    cid = await _card_in_agent_column("P")
    result = await m.move_card(cid, "Done", summary="built it")
    assert result["column"] == "Done"


@pytest.mark.asyncio
async def test_reviewer_own_done_move_reaches_real_done():
    """The reviewer approving a card must land on real Done — not loop back
    into the reviewer column."""
    await _reviewer_column("P")
    cid = await _card_in_agent_column("P", agent=service.REVIEWER_COLUMN,
                                      column=service.REVIEWER_COLUMN)
    result = await m.move_card(cid, "Done", summary="reviewed: compliant")
    assert result["column"] == "Done"


@pytest.mark.asyncio
async def test_analysis_card_not_gated_to_reviewer():
    """Analysis cards use their own outcome contract + child cards as the
    review surface; they are excluded from the reviewer gate."""
    await _reviewer_column("P")
    cid = (await m.create_card("P", "analyse", "", work_type="analysis",
                               confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done", summary="no follow-up needed",
                               outcome="no_action_needed")
    assert result["column"] == "Done"


@pytest.mark.asyncio
async def test_parent_card_parks_not_reviewer():
    """A parent with open children parks in Awaiting Subtasks — the reviewer
    gate only fires on a genuine Done."""
    await _reviewer_column("P")
    parent = (await m.create_card("P", "parent", "", confirm_new_project=True))["id"]
    await m.create_card("P", "child", "", parent_card_id=parent,
                        confirm_new_project=True)
    result = await m.move_card(parent, "Done", summary="split into subtasks")
    assert result["column"] == "Awaiting Subtasks"


@pytest.mark.asyncio
async def test_reviewer_reject_restores_return_agent():
    """A reviewer rejection restores card.agent to the persona that did the
    work so the human's impediment answer resumes the engineer, not the
    reviewer."""
    await _reviewer_column("P")
    cid = await _card_in_agent_column("P")
    # Route it through the gate to set up the reviewer + return-agent state.
    await m.move_card(cid, "Done", summary="built it")
    # Now the reviewer rejects it.
    result = await m.report_impediment(
        cid, "Requirement 2 is not implemented.",
        options=["Implement it now", "Split to a follow-up card",
                 "Drop the requirement", "Ship as-is, document the gap"],
    )
    assert result["column"] == "Impediment"
    assert result["agent"] == "engineer"

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
    # The return-agent key is cleared once consumed.
    assert service.REVIEW_RETURN_AGENT_KEY not in (card.meta or {})


@pytest.mark.asyncio
async def test_reviewer_column_exists_helper():
    async with KanbanSessionLocal() as s:
        assert await service.reviewer_column_exists(s, "P") is False
    await _reviewer_column("P")
    async with KanbanSessionLocal() as s:
        assert await service.reviewer_column_exists(s, "P") is True
        # A different project is unaffected.
        assert await service.reviewer_column_exists(s, "OTHER") is False


def test_reviewer_prompt_has_review_contract_not_ship_steps():
    from app.kanban import dispatch

    class _C:
        title = "T"
        description = "the wish"
        agent = "reviewer"

    prompt = dispatch.build_card_prompt(_C(), persona="You are the reviewer.",
                                        ship_mode="direct")
    assert "independent reviewer" in prompt
    assert "report_impediment" in prompt
    # A reviewer never merges/ships.
    assert "merge your branch into master" not in prompt
    assert "npm run lint && npm run build" not in prompt
