"""Tests for `classify_hold` — the single answer to "why is this card not moving?".

The regression these lock down is the *phase-blind* plan-ref gate: it belongs to
the pre-dispatch race it was written for, and must not follow a card into an
agent column where it has demonstrably already run. That leak is what hid three
finished-but-unreviewed cards from the reviewer for five days.
"""
from datetime import UTC, datetime, timedelta

from app.kanban.dep_resolver import (
    CLEARED_BY_ANALYST_RUN,
    CLEARED_BY_CARD,
    CLEARED_BY_CLOCK,
    CLEARED_BY_HUMAN,
    HOLD_AWAITING_PLAN_REF,
    HOLD_DANGLING_DEP,
    HOLD_DEPENDENT,
    HOLD_GATED,
    HOLD_MISSING_PARENT,
    HOLD_SCHEDULED,
    classify_hold,
)

BOARD_COLUMNS = ["Backlog", "Impediment", "Awaiting Subtasks", "Done", "To Resume"]


class _FakeDeliverable:
    def __init__(self, kind):
        self.kind = kind


class _FakeCard:
    def __init__(
        self, id="c", column="Backlog", depends_on=None, parent_card_id=None,
        deliverables=None, meta=None, scheduled_at=None,
    ):
        self.id = id
        self.column = column
        self.depends_on = depends_on or []
        self.parent_card_id = parent_card_id
        self.deliverables = deliverables or []
        self.meta = meta
        self.scheduled_at = scheduled_at


def _done(id="parent"):
    return _FakeCard(id=id, column="Done")


def test_unblocked_card_has_no_hold():
    assert classify_hold(_FakeCard(), {}) is None


def test_gated_card_is_held_for_a_human():
    hold = classify_hold(_FakeCard(meta={"gated_on": "second provider onboarded"}), {})
    assert hold.reason == HOLD_GATED
    assert hold.cleared_by == CLEARED_BY_HUMAN


def test_empty_gate_string_fails_open():
    assert classify_hold(_FakeCard(meta={"gated_on": ""}), {}) is None


def test_open_dep_on_live_card_is_dependent_and_names_the_blocker():
    card = _FakeCard(depends_on=["p"])
    hold = classify_hold(card, {"p": _FakeCard(id="p", column="engineer")}, live_ids={"c", "p"})
    assert hold.reason == HOLD_DEPENDENT
    assert hold.blocker_ids == ("p",)
    assert hold.cleared_by == CLEARED_BY_CARD


def test_dep_on_done_card_is_not_a_hold():
    card = _FakeCard(depends_on=["p"])
    assert classify_hold(card, {"p": _done("p")}, live_ids={"c", "p"}) is None


def test_deleted_dep_is_dangling_not_dependent():
    """A deleted dep never becomes Done, so it needs a human — not a wait."""
    card = _FakeCard(depends_on=["gone"])
    hold = classify_hold(card, {}, live_ids={"c"})
    assert hold.reason == HOLD_DANGLING_DEP
    assert hold.blocker_ids == ("gone",)
    assert hold.cleared_by == CLEARED_BY_HUMAN


def test_without_an_existence_oracle_references_are_not_guessed_at():
    """No `live_ids` means no evidence — report the temporary state, not orphaned."""
    card = _FakeCard(depends_on=["unknown"])
    hold = classify_hold(card, {})
    assert hold is None or hold.reason != HOLD_DANGLING_DEP


def test_child_without_plan_ref_is_awaiting_plan_ref():
    card = _FakeCard(parent_card_id="p")
    hold = classify_hold(card, {"p": _FakeCard(id="p")}, live_ids={"c", "p"})
    assert hold.reason == HOLD_AWAITING_PLAN_REF
    assert hold.blocker_ids == ("p",)
    assert hold.cleared_by == CLEARED_BY_ANALYST_RUN


def test_child_with_plan_ref_is_not_held():
    card = _FakeCard(parent_card_id="p", deliverables=[_FakeDeliverable("plan_ref")])
    assert classify_hold(card, {"p": _FakeCard(id="p")}, live_ids={"c", "p"}) is None


def test_plan_ref_gate_does_not_follow_a_card_into_an_agent_column():
    """The regression: a card in `reviewer` has already been executed, so the
    create->attach race cannot apply. Scoping the gate to board columns is what
    lets the reviewer see it again."""
    card = _FakeCard(column="reviewer", parent_card_id="p",
                     deliverables=[_FakeDeliverable("branch")])
    hold = classify_hold(
        card, {"p": _FakeCard(id="p")}, live_ids={"c", "p"},
        plan_ref_columns=BOARD_COLUMNS,
    )
    assert hold is None


def test_plan_ref_gate_still_applies_in_board_columns():
    card = _FakeCard(column="Backlog", parent_card_id="p")
    hold = classify_hold(
        card, {"p": _FakeCard(id="p")}, live_ids={"c", "p"},
        plan_ref_columns=BOARD_COLUMNS,
    )
    assert hold.reason == HOLD_AWAITING_PLAN_REF


def test_deleted_parent_is_missing_parent_not_awaiting_plan_ref():
    """A permanent hold must not wear a temporary hold's label: no analyst run
    survives its deleted parent, so nothing will ever attach the plan."""
    card = _FakeCard(parent_card_id="gone")
    hold = classify_hold(card, {}, live_ids={"c"}, plan_ref_columns=BOARD_COLUMNS)
    assert hold.reason == HOLD_MISSING_PARENT
    assert hold.blocker_ids == ("gone",)
    assert hold.cleared_by == CLEARED_BY_HUMAN


def test_future_schedule_is_a_clock_hold():
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    hold = classify_hold(_FakeCard(scheduled_at=future), {})
    assert hold.reason == HOLD_SCHEDULED
    assert hold.cleared_by == CLEARED_BY_CLOCK


def test_past_schedule_is_due():
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert classify_hold(_FakeCard(scheduled_at=past), {}) is None


def test_unparseable_schedule_fails_open():
    assert classify_hold(_FakeCard(scheduled_at="not-a-date"), {}) is None


def test_gate_outranks_every_other_reason():
    card = _FakeCard(
        meta={"gated_on": "x"}, depends_on=["gone"], parent_card_id="also-gone",
    )
    assert classify_hold(card, {}, live_ids={"c"}).reason == HOLD_GATED


def test_permanent_reason_outranks_temporary_one():
    """A card blocked on both a deleted dep and a live one reports the deleted
    dep — that is the half a human has to fix."""
    card = _FakeCard(depends_on=["gone", "live"])
    hold = classify_hold(
        card, {"live": _FakeCard(id="live", column="Backlog")}, live_ids={"c", "live"},
    )
    assert hold.reason == HOLD_DANGLING_DEP


def test_schedule_is_the_lowest_precedence_reason():
    """An early card that is *also* dependent is not merely early."""
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    card = _FakeCard(depends_on=["p"], scheduled_at=future)
    hold = classify_hold(card, {"p": _FakeCard(id="p", column="Backlog")}, live_ids={"c", "p"})
    assert hold.reason == HOLD_DEPENDENT
