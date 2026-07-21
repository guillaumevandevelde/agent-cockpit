"""Pure dependency-resolution helpers used by the dispatch tick.

Kept in its own module so the caller (dispatch) can be tested with mocks and so
the cycle-detection has no DB / session imports — it operates on plain dicts.
"""
from __future__ import annotations

from collections.abc import Sequence


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
