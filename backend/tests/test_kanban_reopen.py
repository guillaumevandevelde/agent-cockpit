# backend/tests/test_kanban_reopen.py
"""Weerleg & heropen: challenge a Done card and re-dispatch it with context.

A human who disagrees with a completed decision posts a `**Revisit:** <note>`
comment on the original Done card, then the card moves back to Backlog. The
dispatch prompt for the re-picked-up card carries a `## REVISIT` section
(mirror of `## IMPEDIMENT`) with the rebuttal + a pointer to the previous
decision (the last `**Summary:**` + the deliverable refs).

Best-effort: if the original session transcript is still on disk (the worktree
+ the ~/.claude/projects/<folder>/<uuid>.jsonl files), reopen also sets
`resume_session_id`/`resume_project_folder` so the spawned session can resume
that transcript. Resume is fragile — analyst cards routinely merge + GC the
worktree before reopen — so the failure mode is *graceful*: no resume fields
set, dispatch still runs with the prompt-injected context.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import dispatch, service
from app.kanban.operations import apply_operation
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest_asyncio.fixture
async def _client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


REVISIT_PREFIX = "**Revisit:** "


async def _make_done_card(s, project_key="P", title="Ship the decision",
                          summary="Decided X.", deliverables=None):
    """Create a Done card with a `**Summary:**` comment + optional deliverables."""
    cid = await apply_operation(s, op_type="create", entity_type="card",
        project_key=project_key, entity_id=None, payload={"title": title})
    await apply_operation(s, op_type="move", entity_type="card",
        project_key="", entity_id=cid, payload={"column": "Done"})
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="", entity_id=cid, payload={"text": f"**Summary:** {summary}"})
    for kind, ref in (deliverables or []):
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=cid, payload={"kind": kind, "ref": ref})
    await s.commit()
    return cid


# --- Service layer -----------------------------------------------------------


@pytest.mark.asyncio
async def test_reopen_posts_revisit_comment_on_original():
    """Acceptance (a): the original card's activity feed gains a
    `**Revisit:**`-prefixed comment carrying the rebuttal text."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)

    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, original_id, "X is wrong because Y.")
        await s.commit()

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, original_id)
    revisit_comments = [
        op.payload.get("text", "")
        for op in activity
        if op.op_type == "comment"
        and op.payload.get("text", "").startswith(REVISIT_PREFIX)
    ]
    assert len(revisit_comments) == 1
    assert "X is wrong because Y." in revisit_comments[0]


@pytest.mark.asyncio
async def test_reopen_moves_card_back_to_backlog():
    """Acceptance (b): the card ends up in the Backlog column so the
    dispatcher picks it up again."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)

    async with KanbanSessionLocal() as s:
        card = await service.reopen_card(s, original_id, "Wrong.")
        await s.commit()

    assert card.column == "Backlog"


@pytest.mark.asyncio
async def test_reopen_rejects_non_done_non_backlog_card():
    """Acceptance (c) at the service layer: reopening a card in flight
    (Doing/Impediment/etc.) raises CardNotInDone so the board is left
    untouched. Done and Backlog are both accepted (Done for the first
    reopen, Backlog for sharpened follow-up rebuttals)."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="P", entity_id=None, payload={"title": "wip"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Doing"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        with pytest.raises(service.CardNotInDone):
            await service.reopen_card(s, cid, "too early")


@pytest.mark.asyncio
async def test_reopen_accepts_already_backlog_card():
    """A second reopen round on a card already in Backlog posts the new
    Revisit comment but does NOT re-move (the dispatch tick already has
    the card on its list). The fresh comment becomes the latest `**Revisit:**`
    — see test_revisit_extraction_picks_last_revisit_comment."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)
    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, original_id, "First rebuttal.")
        await s.commit()
    async with KanbanSessionLocal() as s:
        card = await service.reopen_card(s, original_id, "Second rebuttal.")
        await s.commit()
    assert card.column == "Backlog"
    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, original_id)
    revisit_texts = [
        op.payload.get("text", "")
        for op in activity
        if op.op_type == "comment"
        and op.payload.get("text", "").startswith(REVISIT_PREFIX)
    ]
    assert len(revisit_texts) == 2


@pytest.mark.asyncio
async def test_reopen_missing_card_returns_none():
    async with KanbanSessionLocal() as s:
        assert await service.reopen_card(s, "does-not-exist", "x") is None


@pytest.mark.asyncio
async def test_reopen_does_not_disturb_original_done_summary():
    """The revisit prefix is distinct from `**Summary:** ` so the Done
    summary survives; the card now lives in Backlog but enrich_done_info
    still returns the original Done summary when requested (it reads the
    op-log, not column state)."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s, summary="Original summary text.")

    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, original_id, "Disagree.")
        await s.commit()

    async with KanbanSessionLocal() as s:
        summary, _ = await service.enrich_done_info(s, original_id)
    assert summary == "Original summary text."


@pytest.mark.asyncio
async def test_reopen_does_not_set_resume_fields():
    """Resume handling lives at the dispatch layer (not the service
    layer), because the resume resolver needs `project_path` which the
    service-level card API doesn't carry. The service only mutates board
    state — comment + move-to-Backlog — and the next dispatch tick is
    what tries to attach the resume fields. Here we assert the service
    leaves them alone so dispatch owns that decision cleanly."""

    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="", entity_id=original_id,
            payload={"claimed_by": "agent:k-some-0001"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.reopen_card(s, original_id, "Wrong.")
        await s.commit()

    # Service-level reopen must NOT stamp resume fields — dispatch owns that.
    assert card.resume_session_id is None
    assert card.resume_project_folder is None
    # The board mutation still happens.
    assert card.column == "Backlog"


@pytest.mark.asyncio
async def test_reopen_restores_return_agent_for_reviewer_gate_card():
    """Mirror of `report_impediment`'s reviewer-gate return-agent-reset
    (mcp_server.py:820-835). When a reviewer-approved Done card is
    reopened, the engineer persona must be restored and
    `review_return_agent` must be cleaned up — otherwise the dispatcher
    re-runs the reviewer (which has no Write tools) against a card that
    needs *rework*. Originally regressed in kanban-kaart `0b7c3e98…`
    (kaart 4279448c observed it in production dispatch `k-overlap-in-im-446b`).
    """
    async with KanbanSessionLocal() as s:
        cid = await _make_done_card(s, project_key="P")
        # Stamp the state the reviewer-gate redirect leaves behind on Done:
        # agent=reviewer, metadata.review_return_agent=engineer (the
        # persona that produced the work).
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid, payload={
                "agent": service.REVIEWER_COLUMN,
                "metadata": {service.REVIEW_RETURN_AGENT_KEY: "engineer"},
            })
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.reopen_card(s, cid, "Requirement 2 is missing.")
        await s.commit()

    # The engineer persona is restored so the dispatch picks the right
    # agent — *not* the reviewer (which has no Write tools).
    assert card.agent == "engineer"
    # The return-agent key is consumed so the dispatcher doesn't carry
    # stale state forward.
    assert service.REVIEW_RETURN_AGENT_KEY not in (card.meta or {})
    # The board mutation still happens.
    assert card.column == "Backlog"


@pytest.mark.asyncio
async def test_reopen_preserves_non_reviewer_agent():
    """The reviewer-gate return-agent restoration is gated on
    `card.agent == REVIEWER_COLUMN`. A regular engineer's Done card (no
    reviewer column in the project) must not be touched — the rebuttal
    simply re-enters the dispatch queue with the same persona."""
    async with KanbanSessionLocal() as s:
        cid = await _make_done_card(s, project_key="P")
        # No reviewer column → no return-agent redirect. The card sits
        # in Done with agent=engineer and no special metadata.
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid, payload={"agent": "engineer"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.reopen_card(s, cid, "Disagree with the call.")
        await s.commit()

    assert card.agent == "engineer"
    assert card.meta == {} or card.meta is None
    assert card.column == "Backlog"


@pytest.mark.asyncio
async def test_revisit_extraction_picks_last_revisit_comment():
    """The dispatch prompt must reflect the *latest* Revisit comment, even
    when multiple rounds of reopen have happened — extract_revisit_question
    walks the activity feed in reverse and picks the most recent
    `**Revisit:**` comment."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)

    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, original_id, "First rebuttal.")
        await s.commit()

    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, original_id, "Second, sharper rebuttal.")
        await s.commit()

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, original_id)
        revisit = dispatch.extract_revisit_question(activity)
    assert revisit == "Second, sharper rebuttal."


@pytest.mark.asyncio
async def test_revisit_extraction_none_without_comment():
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, original_id)
        revisit = dispatch.extract_revisit_question(activity)
    assert revisit is None


# --- build_card_prompt -------------------------------------------------------


@pytest.mark.asyncio
async def test_build_card_prompt_includes_revisit_section_when_revisit_set():
    """Acceptance (d): when the dispatch picks up a reopened card, the
    prompt carries a `## REVISIT` section with the rebuttal text + a
    pointer to the previous decision (deliverables + done summary)."""

    class _C:
        title = "Old Decision"
        description = ""

    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct",
        revisit_question="The X assumption is wrong.",
        revisit_prior_decision={
            "summary": "Decided X because Y.",
            "deliverables": [{"kind": "branch", "ref": "k-old-branch"}],
        },
    )
    assert "## REVISIT" in prompt
    assert "The X assumption is wrong." in prompt
    # Pointer to the prior decision.
    assert "Decided X because Y." in prompt
    assert "branch: k-old-branch" in prompt


def test_build_card_prompt_no_revisit_section_when_unset():
    """The `## REVISIT` section is opt-in: cards that aren't reopened
    never see it."""
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct")
    assert "## REVISIT" not in prompt


# --- dispatch injection ------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_revisit_returns_question_and_prior_decision():
    """When the card has a Revisit comment + a Done summary + a deliverable,
    _resolve_revisit returns the latest rebuttal + the prior-decision dict
    (summary + deliverable refs) so build_card_prompt can render a
    pointer-rich `## REVISIT` section."""
    async with KanbanSessionLocal() as s:
        cid = await _make_done_card(s, summary="Original decision.",
            deliverables=[("branch", "k-original-branch")])
    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, cid, "Wrong because Z.")
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        revisit, prior = await dispatch._resolve_revisit(s, card)
    assert revisit == "Wrong because Z."
    assert prior["summary"] == "Original decision."
    assert prior["deliverables"] == [
        {"kind": "branch", "ref": "k-original-branch"},
    ]


@pytest.mark.asyncio
async def test_resolve_revisit_returns_none_pair_when_no_revisit():
    async with KanbanSessionLocal() as s:
        cid = await _make_done_card(s)
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        revisit, prior = await dispatch._resolve_revisit(s, card)
    assert revisit is None
    assert prior is None


@pytest.mark.asyncio
async def test_stamp_resume_target_persists_session_id(monkeypatch, tmp_path):
    """Best-effort resume: when the prior session's worktree + transcript
    exist on disk, _stamp_resume_target writes `resume_session_id` +
    `resume_project_folder` onto the card so the spawn below picks the
    resume transport. Failure (transcript gone) is silent — None fallback
    is the expected path for analyst cards post-GC."""
    from app.kanban import session_recovery

    repo = tmp_path / "repo"
    (repo / ".claude" / "worktrees" / "k-some-0001").mkdir(parents=True)
    calls = []

    def fake_resolve(project_path, session_name, *, cli_id):
        calls.append((project_path, session_name, cli_id))
        return "abc-123", "encoded-project-folder"

    monkeypatch.setattr(session_recovery, "_resolve_resume_target", fake_resolve)

    async with KanbanSessionLocal() as s:
        cid = await _make_done_card(s)
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="", entity_id=cid,
            payload={"claimed_by": "agent:k-some-0001"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        await dispatch._stamp_resume_target(
            s, card=card, project_key=card.project_key,
            project_path=str(repo),
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
    assert card.resume_session_id == "abc-123"
    assert card.resume_project_folder == "encoded-project-folder"
    assert calls == [(str(repo), "k-some-0001", "claude-code")]


@pytest.mark.asyncio
async def test_stamp_resume_target_silent_when_no_transcript(
        monkeypatch, tmp_path):
    """When the transcript is gone, _stamp_resume_target must NOT raise —
    dispatch just falls through to a fresh-session spawn. The graceful
    fallback is the contract (analysis cards GC their worktrees after
    Done)."""
    from app.kanban import session_recovery
    monkeypatch.setattr(
        session_recovery,
        "_resolve_resume_target",
        lambda project_path, session_name, **kwargs: None,
    )

    async with KanbanSessionLocal() as s:
        cid = await _make_done_card(s)
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="", entity_id=cid,
            payload={"claimed_by": "agent:k-gc'd-9999"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        await dispatch._stamp_resume_target(
            s, card=card, project_key=card.project_key,
            project_path=str(tmp_path / "no-such-repo"),
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
    assert card.resume_session_id is None
    assert card.resume_project_folder is None


@pytest.mark.asyncio
async def test_stamp_resume_target_noop_without_agent_claim(monkeypatch, tmp_path):
    """Cards with no `agent:` claim (never dispatched, only commented by
    hand) have no prior session to resume — the helper is a no-op so it
    doesn't synthesize a phantom resume pointer."""
    from app.kanban import session_recovery

    def unexpected_resolve(*args, **kwargs):
        raise AssertionError("resolver must not run without an agent claim")

    monkeypatch.setattr(
        session_recovery, "_resolve_resume_target", unexpected_resolve,
    )

    async with KanbanSessionLocal() as s:
        cid = await _make_done_card(s)
        # No claim applied — left as None.
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        await dispatch._stamp_resume_target(
            s, card=card, project_key=card.project_key,
            project_path=str(tmp_path / "no-such-repo"),
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
    assert card.resume_session_id is None


# --- REST layer --------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_reopen_creates_revisit_and_moves_to_backlog(_client):
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "REST", "title": "Done decision", 'confirm_new_project': True})
    cid = r.json()["id"]
    from app.kanban import mcp_server as m
    await m.move_card(cid, "Done", summary="Decided X.")

    r = await _client.post(f"/api/v1/kanban/cards/{cid}/reopen",
        json={"note": "X is wrong because Y."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["column"] == "Backlog"

    act = await _client.get(f"/api/v1/kanban/cards/{cid}/activity")
    texts = [e["payload"].get("text", "") for e in act.json()
             if e["op_type"] == "comment"]
    assert any(t.startswith("**Revisit:** ") and "X is wrong because Y." in t
               for t in texts)


@pytest.mark.asyncio
async def test_rest_reopen_409_on_in_flight_card(_client):
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "REST", "title": "wip", 'confirm_new_project': True})
    cid = r.json()["id"]
    # Move it to Doing so it's in flight.
    await _client.post(f"/api/v1/kanban/cards/{cid}/move",
        json={"column": "Doing"})
    r = await _client.post(f"/api/v1/kanban/cards/{cid}/reopen",
        json={"note": "too early"})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_rest_reopen_404_on_missing_card(_client):
    r = await _client.post("/api/v1/kanban/cards/nope/reopen",
        json={"note": "x"})
    assert r.status_code == 404, r.text


# --- MCP layer ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_reopen_creates_revisit_and_moves_to_backlog():
    from app.kanban import mcp_server as m

    original_id = (await m.create_card("MCP", "Done decision", "", confirm_new_project=True))["id"]
    await m.move_card(original_id, "Done", summary="Decided X.")

    card = await m.reopen_card(original_id, "X is wrong.")
    assert card["column"] == "Backlog"


@pytest.mark.asyncio
async def test_mcp_reopen_error_on_in_flight_card():
    from app.kanban import mcp_server as m

    cid = (await m.create_card("MCP", "wip", "", confirm_new_project=True))["id"]
    # Move it into a column other than Done / Backlog so reopen refuses.
    await m.move_card(cid, "Doing")
    res = await m.reopen_card(cid, "too early")
    assert res["error"] == "not_in_done"
    assert res["column"] == "Doing"