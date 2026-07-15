"""Tests for the card-gate mechanism (gate = machine-readable business trigger
that holds a card out of auto-dispatch, independent of ``depends_on``).

Regression target: kanban card
"[problem] Gepoorte kaarten ('bewust niet nu, pas bij trigger X') worden
auto-gedispatcht zodra hun depends_on klaar is" — when a card's only remaining
dependency was a business trigger documented as prose in its description, the
dispatcher had no way to know about the gate and would happily dispatch the
card the moment its ``depends_on`` parents landed on Done. The fix is a
``metadata.gated_on`` key that ``_next_card`` and the bulk auto-dispatch paths
honour, parallel to the existing ``_is_due`` and ``_awaiting_plan_ref`` checks.
"""
import pytest

from app.kanban import dispatch
from app.kanban.dispatch import _is_gated, _next_card


class _FakeCard:
    """Minimal duck-typed card matching the slots `_next_card` reads.

    `_next_card` reads `column`, `claimed_by`, `scheduled_at`, `parent_card_id`,
    and `deliverables`; we just set the ones each test needs. `meta` is the
    ORM attribute name (the DB column is `metadata`, see models.py:94).
    """
    def __init__(self, *, id="c", column="Backlog", claimed_by=None,
                 scheduled_at=None, parent_card_id=None, deliverables=None,
                 meta=None):
        self.id = id
        self.column = column
        self.claimed_by = claimed_by
        self.scheduled_at = scheduled_at
        self.parent_card_id = parent_card_id
        self.deliverables = deliverables or []
        self.meta = meta


# ---- _is_gated: pure helper -----------------------------------------------

def test_is_gated_false_when_no_metadata():
    """A card without any metadata is not gated."""
    card = _FakeCard()
    assert _is_gated(card) is False


def test_is_gated_false_when_metadata_empty():
    card = _FakeCard(meta={})
    assert _is_gated(card) is False


def test_is_gated_false_when_metadata_has_other_keys():
    """The gate is keyed on `gated_on` specifically — other metadata keys
    (e.g. an external id, a workflow tag) must not be misinterpreted as a gate."""
    card = _FakeCard(meta={"external_ref": "JIRA-123", "owner": "team-x"})
    assert _is_gated(card) is False


def test_is_gated_true_when_gated_on_set():
    """The trigger string is opaque to the dispatcher; any non-empty string is
    treated as 'card is gated, do not auto-dispatch'. The human operator picks
    when the trigger has fired and clears the key."""
    card = _FakeCard(meta={"gated_on": "second-executor-provider-onboarded"})
    assert _is_gated(card) is True


def test_is_gated_false_when_gated_on_empty_string():
    """An empty string is treated as no gate (fail open — same contract as
    ``_is_due``'s unparseable timestamp). A card author who wrote
    ``gated_on: ''`` likely meant to clear the gate and forgot to delete the
    key; respecting that intent avoids a stuck card."""
    card = _FakeCard(meta={"gated_on": ""})
    assert _is_gated(card) is False


def test_is_gated_tolerates_meta_attribute_missing():
    """Some test doubles (and theoretically older row shapes) may not even
    expose the ``meta`` attribute. Fail open — same pattern as the rest of the
    dispatcher helpers — rather than AttributeError-ing the tick."""
    class _Bare:
        pass
    card = _Bare()
    assert _is_gated(card) is False


# ---- _next_card: gated card is held out even when deps are satisfied -----

def test_next_card_skips_gated_card_even_with_no_deps():
    """The core regression: a card with ``metadata.gated_on`` set must NOT be
    returned by ``_next_card``, even if its ``depends_on`` is empty (the
    canonical 'card is ready except for the business trigger' state). The
    operator who set the gate is the only one who can lift it."""
    gated = _FakeCard(meta={"gated_on": "awaiting-legal-review"})
    cards = [gated]
    assert _next_card(cards) is None


def test_next_card_skips_gated_card_but_picks_ungated_sibling():
    """A gated card and an ungated sibling in the same column: only the
    ungated one is dispatched. Mirrors the existing dependency-gate pattern
    where one blocked card doesn't poison the rest of the backlog."""
    gated = _FakeCard(id="g1", meta={"gated_on": "trigger-x"})
    ready = _FakeCard(id="r1")
    cards = [gated, ready]
    picked = _next_card(cards)
    assert picked is not None
    assert picked.id == "r1"


def test_next_card_picks_card_after_gate_is_lifted():
    """Lifting the gate is just removing the ``gated_on`` key from metadata.
    Same card with empty meta returns to the dispatch pool."""
    card = _FakeCard(meta={"gated_on": "trigger-x"})
    assert _next_card([card]) is None
    card.meta = None  # operator cleared the gate
    assert _next_card([card]) is card


def test_next_card_independent_of_depends_on():
    """The gate and ``depends_on`` are two independent holds. A card with
    neither is dispatchable; either one set holds the card out. Mirrors the
    semantics described in the card description: 'machine-leesbare poort die
    auto-dispatch respecteert, los van depends_on'."""
    plain = _FakeCard()
    gated = _FakeCard(meta={"gated_on": "t"})
    assert _next_card([plain]) is plain
    assert _next_card([gated]) is None


# ---- dispatch_project: end-to-end with stub _run_card -------------------

@pytest.mark.asyncio
async def test_dispatch_project_skips_gated_card_with_all_deps_done(monkeypatch):
    """End-to-end regression for the card's concrete instantie: a card whose
    only remaining barrier was a business trigger (encoded as
    ``metadata.gated_on``) would previously be dispatched by ``dispatch_project``
    the moment its ``depends_on`` parents hit Done. The spawn would then fail
    in the spawned session because the trigger hadn't fired (e.g. 'no second
    executor provider exists yet') and the session would bounce back to
    Impediment — wasting a worktree + session for nothing.

    This test sets up exactly that scenario:
      - a parent card already in Done (deps satisfied)
      - a child card in Backlog with ``metadata.gated_on`` set
      - stubs ``_run_card`` to record any spawn attempt

    Assertion: ``_run_card`` is never called for the gated child.
    """
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    spawns = []

    async def fake_run_card(session, **kwargs):
        card = kwargs["card"]
        spawns.append((kwargs["phase"], card.id))
        # Mirror the real ``_run_card`` shape so the dispatch tick's
        # analyst_run_id write-back doesn't blow up if the gated card
        # were somehow picked (defence in depth — should be unreachable
        # after the gate check lands).
        return {
            "card_id": card.id,
            "session_name": f"tmux-{card.id}",
            "claimant": f"agent:tmux-{card.id}",
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "capability", "column": "Done"},
        )
        child_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={
                "title": "[spike][GEPOORT] ACP transport sibling",
                "column": "Backlog",
                "depends_on": [parent_id],
                "metadata": {
                    "gated_on": "second-executor-provider-onboarded",
                },
            },
        )
        await s.commit()

        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/none")
        await s.commit()

        assert spawns == [], (
            f"dispatcher spawned {spawns!r}; the gated card must be held "
            f"out of auto-dispatch until the operator lifts the gate. "
            f"This is the regression that motivated this card."
        )

        # The card is still on the board, untouched, with its gate intact.
        child = await s.get(KanbanCard, child_id)
        assert child.column == "Backlog", (
            "gated card must remain visible on Backlog (not be claimed/moved) "
            "so the operator can see the trigger that holds it"
        )
        assert (child.meta or {}).get("gated_on") == (
            "second-executor-provider-onboarded"
        )


@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_dispatch_project_picks_card_after_metadata_clears(monkeypatch):
    """End-to-end counterpart to the previous test: once the operator clears
    ``metadata.gated_on`` (sets ``metadata`` to a dict without the key, or
    replaces it entirely), the dispatcher picks the card up on the very next
    tick.

    Uses an in-memory ``_next_card`` round-trip rather than a second full
    ``dispatch_project`` call: ``_next_card`` is the part whose state actually
    changes when the operator lifts the gate (the gate check is here), and
    that's enough to prove the round-trip. Driving a full second tick would
    need the test to manage another transport + per-column-cap + analyst
    phase branch — that territory is covered by the existing one-tick
    regressions, so we don't double up.
    """
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={
                "title": "card after gate lifted",
                "column": "Backlog",
                # Initially gated. The operator clears it.
                "metadata": {"gated_on": "trigger-x"},
            },
        )
        await s.commit()

        # While gated, _next_card returns None — no candidate is dispatchable.
        before = await s.get(KanbanCard, cid)
        assert _next_card([before]) is None

        # Operator lifts the gate by replacing metadata with a dict that
        # doesn't contain gated_on (the realistic "delete the key" UX).
        await apply_operation(
            s, op_type="update", entity_type="card",
            project_key=PK, entity_id=cid,
            payload={"metadata": {"owner": "team-y"}},
        )
        await s.commit()

        after = await s.get(KanbanCard, cid)
        assert (after.meta or {}).get("gated_on") is None
        assert _next_card([after]) is not None
        assert _next_card([after]).id == cid
