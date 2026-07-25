"""Pure dependency-resolution helpers used by the dispatch tick.

Kept in its own module so the caller (dispatch) can be tested with mocks and so
the cycle-detection has no DB / session imports — it operates on plain dicts.

Also home to :func:`classify_hold`, the single vocabulary for *why* a card is
not dispatchable. See its docstring for why that answer is a first-class,
persisted value rather than a boolean the dispatcher throws away.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime


def meets_dep_prerequisites(card, cards_by_id: dict) -> bool:
    """True iff every entry in `card.depends_on` is in `cards_by_id` AND
    that card is in column 'Done'. A missing parent is treated as
    'not Done' — fail closed."""
    deps = getattr(card, "depends_on", None) or []
    for parent_id in deps:
        parent = cards_by_id.get(parent_id)
        if parent is None:
            return False
        if getattr(parent, "column", None) != "Done":
            return False
    return True


def dangling_dep_ids(card, live_ids) -> list[str]:
    """Return the entries in ``card.depends_on`` that resolve to no live card.

    ``live_ids`` is the board-wide set of existing card ids — the existence
    oracle. A dep-id absent from it is *dangling*: the depended-on card was
    deleted (or never existed), so the fail-closed ``meets_dep_prerequisites``
    gate blocks this card **permanently and invisibly** — a missing parent is
    indistinguishable from "not Done yet". A healthy not-yet-Done dep IS in
    ``live_ids`` (it just isn't in column Done), so it is not returned here.

    Board-wide (not project-scoped) on purpose: a dep pointing at a live card
    in another project is unusual but not dangling, and must not be flagged —
    mirrors ``scripts/sweep_dangling_depends_on.py``, which also checks
    existence board-wide.
    """
    deps = getattr(card, "depends_on", None) or []
    return [d for d in deps if d not in live_ids]


def detect_cycle(graph: dict[str, Sequence[str]]) -> list[str] | None:
    """Return the first cycle found as a list [a, b, ..., a], or None if acyclic.

    Uses the standard 'gray/black' DFS colour scheme. Input keys are nodes,
    values are the parents each node depends on (i.e. edges go from node →
    dependency). Self-loops are cycles and are detected immediately.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in graph}
    path: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = GRAY
        path.append(n)
        for m in graph.get(n, []):
            if m not in color:
                # unknown node: ignore (it's an external dep not part of this graph)
                continue
            if color[m] == GRAY:
                start = path.index(m)
                return path[start:] + [m]
            if color[m] == WHITE:
                c = visit(m)
                if c is not None:
                    return c
        path.pop()
        color[n] = BLACK
        return None

    for n in list(graph):
        if color[n] == WHITE:
            c = visit(n)
            if c is not None:
                return c
    return None


# ---- hold classification ---------------------------------------------------
#
# Why this exists at all
# ---------------------
# Before this module owned it, "why is this card not moving?" was answered by a
# conjunction of booleans inside ``dispatch._next_card``:
#
#     c.column == col and not c.claimed_by and _is_due(c)
#     and not _awaiting_plan_ref(c) and not _is_gated(c)
#
# Every one of those predicates *subtracts* a card from the candidate list and
# emits nothing — no log line, no counter, no field on the card. At the moment
# of skipping, the dispatcher knew exactly which card it was passing over and
# why; that knowledge was discarded on the spot. Afterwards a held card is
# indistinguishable from one that was never a candidate, which is why 13 cards
# could sit untouched for five days without a single signal: every watchdog in
# the system (``reap_stale_claims``, ``check_progress_liveness``) keys on
# ``claimed_by``, so it only ever supervises work that was *started*. Work the
# dispatcher decided not to start fell outside all of it, and the compensating
# response was a growing family of external advisory sweepers
# (``sweep_dangling_depends_on.py``, ``sweep_dangling_plan_refs.py``,
# ``check-analysis-outcomes.sh``) reconstructing from the outside what was
# already known on the inside.
#
# ``classify_hold`` is that discarded answer, made explicit and returned as a
# value. The dispatcher persists it (``held_reason``/``held_since``/
# ``held_blocker``), the API reports it and the board renders it, so all three
# read one truth instead of each re-deriving it.

# Hold reasons. Wire values — persisted on the card and rendered by the UI, so
# treat them as an API contract, not internal names.
HOLD_GATED = "gated"                          # operator set metadata["gated_on"]
HOLD_DANGLING_DEP = "dangling_dep"            # depends_on points at a deleted card
HOLD_MISSING_PARENT = "missing_parent"        # parent_card_id points at a deleted card
HOLD_AWAITING_PLAN_REF = "awaiting_plan_ref"  # child still owed a plan by its analyst
HOLD_DEPENDENT = "dependent"                  # depends_on a live, not-yet-Done card
HOLD_SCHEDULED = "scheduled"                  # scheduled_at names a future time

# Who or what can clear the hold. The point of naming this is that a board can
# then answer "what is waiting on *me*?" — the distinction the old boolean
# filters erased.
CLEARED_BY_HUMAN = "human"              # needs an operator decision or edit
CLEARED_BY_CARD = "card"                # resolves when another card reaches Done
CLEARED_BY_ANALYST_RUN = "analyst_run"  # resolves when an analyst attaches the plan
CLEARED_BY_CLOCK = "clock"              # resolves on its own, at a known time

_CLEARED_BY = {
    HOLD_GATED: CLEARED_BY_HUMAN,
    HOLD_DANGLING_DEP: CLEARED_BY_HUMAN,
    HOLD_MISSING_PARENT: CLEARED_BY_HUMAN,
    HOLD_AWAITING_PLAN_REF: CLEARED_BY_ANALYST_RUN,
    HOLD_DEPENDENT: CLEARED_BY_CARD,
    HOLD_SCHEDULED: CLEARED_BY_CLOCK,
}


@dataclass(frozen=True)
class Hold:
    """Why one card is not dispatchable, and who can clear it."""

    reason: str
    blocker_ids: tuple[str, ...] = field(default=())

    @property
    def cleared_by(self) -> str:
        return _CLEARED_BY.get(self.reason, CLEARED_BY_HUMAN)


def is_due(card) -> bool:
    """True unless ``card.scheduled_at`` names a not-yet-reached future time.

    A missing or unparseable value is treated as due (fail open) rather than
    silently hiding a card from auto-dispatch forever over a bad timestamp.
    ``dispatch._is_due`` delegates here so the clock rule has one home.
    """
    scheduled_at = getattr(card, "scheduled_at", None)
    if not scheduled_at:
        return True
    try:
        fire_at = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return True
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=UTC)
    return fire_at <= datetime.now(UTC)


def has_plan_ref(card) -> bool:
    return any(
        getattr(d, "kind", None) == "plan_ref"
        for d in (getattr(card, "deliverables", None) or ())
    )


def classify_hold(
    card,
    cards_by_id: dict,
    *,
    live_ids: set[str] | None = None,
    plan_ref_columns: Sequence[str] | None = None,
) -> Hold | None:
    """Return the single reason ``card`` is not dispatchable, or None if it is.

    Pure: no DB, no clock beyond ``scheduled_at``. Callers supply the board
    state.

    ``cards_by_id`` is the project working set. ``live_ids`` is the *board-wide*
    existence oracle used to tell a deleted reference (permanent, needs a human)
    apart from a live one that simply is not Done yet (temporary, self-healing).
    Pass None when the caller has no oracle at hand — reference-existence checks
    are then skipped rather than guessed at, so a card is never reported as
    orphaned on missing evidence.

    ``plan_ref_columns`` is the set of columns in which the plan-ref race can
    still apply — normally ``schemas.COLUMNS``, the board columns. Omit to apply
    the gate everywhere (the legacy, phase-blind behaviour).

    Precedence, highest first: gated → dangling_dep → missing_parent →
    awaiting_plan_ref → dependent → scheduled. Human-actionable and permanent
    reasons outrank temporary self-healing ones, so the card reports the
    blocker a human could actually act on; ``scheduled`` sits last because a
    card that is *also* blocked on something else is not merely early.
    """
    # Operator-set business gate. Fail open on an empty string: both a missing
    # key and "" mean "no gate", matching dispatch._is_gated.
    meta = getattr(card, "meta", None) or getattr(card, "metadata", None) or {}
    gate = meta.get("gated_on") if isinstance(meta, dict) else None
    if gate:
        return Hold(HOLD_GATED)

    deps = tuple(getattr(card, "depends_on", None) or ())
    if live_ids is not None:
        dangling = tuple(d for d in deps if d not in live_ids)
        if dangling:
            return Hold(HOLD_DANGLING_DEP, dangling)

    parent_id = getattr(card, "parent_card_id", None)
    if parent_id and live_ids is not None and parent_id not in live_ids:
        # The analyst run that owed this card a plan is gone along with its
        # parent. Without this branch the card falls through to
        # awaiting_plan_ref and waits for a producer that provably no longer
        # exists — a permanent hold wearing a temporary hold's label.
        return Hold(HOLD_MISSING_PARENT, (parent_id,))

    # Child card whose analyst has not yet attached its `plan_ref` deliverable.
    #
    # This closes a seconds-wide race: the analyst creates the child (step 3)
    # directly into a dispatch-eligible column and links the plan (step 4) a
    # moment later; a tick firing in between spawns an executor whose prompt
    # renders the generic "Plan niet beschikbaar" placeholder.
    #
    # `plan_ref_columns` scopes that guard to the phase it belongs to. A card
    # sitting in an *agent* column has demonstrably been dispatched already, so
    # the create→attach race cannot apply to it — yet the phase-blind version of
    # this check ran there too, via the orphan-rescue arm of `_next_card`, and
    # permanently hid finished-but-unreviewed cards from the reviewer.
    if parent_id and not has_plan_ref(card):
        column = getattr(card, "column", None)
        if plan_ref_columns is None or column in plan_ref_columns:
            return Hold(HOLD_AWAITING_PLAN_REF, (parent_id,))

    open_deps = tuple(
        d for d in deps
        if (parent := cards_by_id.get(d)) is not None
        and getattr(parent, "column", None) != "Done"
    )
    if open_deps:
        return Hold(HOLD_DEPENDENT, open_deps)

    if not is_due(card):
        return Hold(HOLD_SCHEDULED)

    return None
