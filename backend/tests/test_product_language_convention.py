"""Drift-test for the product-language convention in Done summaries and
impediment options.

The convention comes from
``docs/cockpit/product-owner-volgbaarheid-analyse.md`` §4.2 (kaart
``75c0952f…``, follow-up card ``4358fe0a00e342878bc7a77fd21ffebe``). The
canonical phrasing lives in three locations that must stay in sync:

  1. ``docs/cockpit/kanban-conventions.md`` — the source-of-truth doc
     that future engineers/analysts/reviewers read directly.
  2. ``backend/app/kanban/mcp_server.py`` — the ``move_card`` and
     ``report_impediment`` MCP tool descriptions. These are the strings
     the dispatched agent actually sees in its tool listing, so they
     are the *enforcement* surface — without the convention here, an
     agent can ignore the persona-prompt reminder and the gate falls
     back to "summary required" only.
  3. ``.claude/agents/engineer.md`` + ``.claude/agents/analyst.md`` +
     ``.claude/agents/reviewer.md`` — the persona prompts. The reminder
     language appears in §Kaart bijwerken / outcome-contract / move_card
     step. Lockstep update per the drift pattern established by
     ``test_fcr_prompt_drift.py`` (kaart ``d9447e49``).
  4. ``backend/app/kanban/dispatch.py::_build_ship_instructions`` + the
     same function's analyst counterpart — the mirror the dispatcher
     inlines into spawned sessions, so dispatched sessions get the same
     reminder as the persona.
  5. ``.claude/skills/git-ship/SKILL.md`` — the ship-recipe skill that
     hand-run sessions (i.e. sessions that read the skill rather than
     receiving the inline dispatch mirror) follow. CLAUDE.md:145-150
     declares the skill the *source of truth* and the dispatch mirror
     the copy that must stay in sync. Per kaart ``4358fe0a…``
     impediment: a future edit to the skill that drops the convention
     would silently undo the rule for hand-run sessions, and a
     subsequent re-sync to dispatch.py would propagate the loss
     outward. The guard has to cover both directions of that drift.

The drift guard ensures all mirrors stay in sync; without it, an edit
that updates the persona but forgets the dispatch mirror (or vice
versa) gives a silent inconsistency between hand-run sessions and
auto-dispatched sessions. The same drift class is what motivated
``test_fcr_prompt_drift`` and ``test_ship_recipe_drift`` — adding the
convention without this guard would re-open the same hole.

The invariants list lives at module scope — edit it (and all mirrors)
in the same commit whenever the convention legitimately changes (e.g.
adding a new before/after example). Delete an invariant only when the
underlying wording truly disappears from every mirror, otherwise the
guard fails loudly on the next CI run instead of allowing silent drift.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.kanban import dispatch, mcp_server

REPO_ROOT = Path(__file__).resolve().parents[2]


# Core product-language-convention invariants, keyed by source.
#
# Each entry maps a source name (a key in ``SOURCES``) to a list of
# ``(label, anchor)`` pairs. The label is used in the parametrised
# test id and the failure message — keep it short so a CI failure
# points the next editor at the right knob without opening the file.
#
# Some anchors apply to *every* source (e.g. ``product-taal`` — the
# convention name itself); others are source-specific (e.g.
# ``productbetekenis`` belongs to the Done-summary surface and the
# ``move_card`` MCP docstring, but not to ``report_impediment``).
# Splitting per-source keeps the test honest: an anchor that does not
# semantically belong on a mirror would mask a real omission elsewhere.
#
# When the convention legitimately changes (new wording, refined
# rationale): edit this dict AND every mirror in lockstep. The drift
# detector's whole point is that an inconsistency here is loud, not
# silent.
COMMON_PRODUCT_LANGUAGE_ANCHORS: list[tuple[str, str]] = [
    # The single token that names the convention itself. Every mirror
    # references this so a search across the repo lands on the same
    # §-anchor in kanban-conventions.md.
    (
        "convention name",
        "product-taal",
    ),
    # The "no process-meta in human-facing summaries" rule (kaart
    # ``8b3ce64c…``). The new §5a anchors the three-delen-vorm with a
    # explicit prohibition on FCR-uitslagen / session-retro / dedup-boekhouding
    # / audit-log-archeologie in the human-facing summary — those belong in
    # the activity-feed / retro-kaarten, not on the banner a human reads.
    # Every mirror that reminds the agent about the convention also has to
    # carry this rule's name so a future edit that drops the rule fails
    # CI loudly on every mirror at once, not on the convention doc alone.
    (
        "no process-meta in human-facing summaries",
        "proces-meta",
    ),
]

# Done-summary surfaces: anchors about the "lead with product
# meaning" rule and its ordering versus engineering detail.
DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS: list[tuple[str, str]] = [
    (
        "Done summary: lead with product meaning",
        "productbetekenis",
    ),
    (
        "Done summary: engineering detail follows product sentence",
        "engineering",
    ),
]

# Impediment-options surfaces: anchors about product trade-offs vs.
# implementation forks.
IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS: list[tuple[str, str]] = [
    (
        "Impediment options: product tradeoffs not implementation forks",
        "producttrade",
    ),
]

# Per-source anchor bundles.
#
# The convention doc + the three persona prompts + the analyst-prompt
# Python mirror + the engineer-ship-instruction dispatch mirrors all
# cover BOTH the Done-summary rule AND the impediment-options rule,
# because those mirrors describe the whole convention. The two MCP
# tool docstrings split: ``move_card`` only covers Done-summary
# wording (its parameter is ``summary``); ``report_impediment`` only
# covers options wording (its parameter is ``options``).
SOURCE_PRODUCT_LANGUAGE_INVARIANTS: dict[str, list[tuple[str, str]]] = {
    "docs/cockpit/kanban-conventions.md": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    ".claude/agents/engineer.md": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    ".claude/agents/analyst.md": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    ".claude/agents/reviewer.md": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    "backend/app/kanban/analyst_prompt.py": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    "dispatch._build_ship_instructions('direct')": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    "dispatch._build_ship_instructions('pull-request')": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    "dispatch._build_analyst_session_end_instructions()": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    "mcp_server.move_card docstring": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
    ),
    "mcp_server.report_impediment docstring": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
    ".claude/skills/git-ship/SKILL.md": (
        COMMON_PRODUCT_LANGUAGE_ANCHORS
        + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
        + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
    ),
}

# Flat list kept for the demo negative-case test — picks one anchor
# that must be in every mirror.
CORE_PRODUCT_LANGUAGE_INVARIANTS: list[tuple[str, str]] = (
    COMMON_PRODUCT_LANGUAGE_ANCHORS
    + DONE_SUMMARY_PRODUCT_LANGUAGE_ANCHORS
    + IMPEDIMENT_OPTIONS_PRODUCT_LANGUAGE_ANCHORS
)


def _conventions_md_body() -> str:
    return (REPO_ROOT / "docs" / "cockpit" / "kanban-conventions.md").read_text(encoding="utf-8")


def _engineer_md_body() -> str:
    return (REPO_ROOT / ".claude" / "agents" / "engineer.md").read_text(encoding="utf-8")


def _analyst_md_body() -> str:
    return (REPO_ROOT / ".claude" / "agents" / "analyst.md").read_text(encoding="utf-8")


def _reviewer_md_body() -> str:
    return (REPO_ROOT / ".claude" / "agents" / "reviewer.md").read_text(encoding="utf-8")


def _analyst_prompt_py_body() -> str:
    return (REPO_ROOT / "backend" / "app" / "kanban" / "analyst_prompt.py").read_text(encoding="utf-8")


def _git_ship_skill_md_body() -> str:
    """Return the git-ship skill source-of-truth body.

    Per CLAUDE.md:145-150, ``.claude/skills/git-ship/SKILL.md`` is the
    *source* the dispatch mirror in ``_build_ship_instructions`` stays in
    sync with — the inverse of the convention mirror direction used for
    persona prompts. A hand-run engineer session reading this skill
    rather than receiving the inlined dispatch mirror gets its
    Done-summary instruction directly from this file, so the convention
    has to live here too (kaart ``4358fe0a…`` impediment).
    """
    return (REPO_ROOT / ".claude" / "skills" / "git-ship" / "SKILL.md").read_text(encoding="utf-8")


def _mcp_move_card_docstring() -> str:
    """Return the ``move_card`` tool description the dispatched agent sees.

    Calling the live function (rather than grepping the file) tests the
    *rendered* docstring a freshly spawned agent receives — if a future
    refactor wraps the docstring in a helper, the guard still works.
    """
    return mcp_server.move_card.__doc__ or ""


def _mcp_report_impediment_docstring() -> str:
    return mcp_server.report_impediment.__doc__ or ""


def _dispatch_direct_prompt() -> str:
    return dispatch._build_ship_instructions("direct")


def _dispatch_pull_request_prompt() -> str:
    return dispatch._build_ship_instructions("pull-request")


def _dispatch_analyst_prompt() -> str:
    """Render the analyst-session-end instructions as dispatched.

    Mirrors ``test_fcr_prompt_drift._dispatch_direct_prompt`` — calling
    the function (rather than grepping) tests the rendered string.
    """
    return dispatch._build_analyst_session_end_instructions()


# Source registry: name -> callable yielding the source text. Using a
# dict so the parametrised test iterates sources symmetrically and the
# failure message reads "SOURCE_NAME missing LABEL: 'substring'", which
# is exactly what the next editor needs to see.
SOURCES: dict[str, callable[[], str]] = {
    "docs/cockpit/kanban-conventions.md": _conventions_md_body,
    ".claude/agents/engineer.md": _engineer_md_body,
    ".claude/agents/analyst.md": _analyst_md_body,
    ".claude/agents/reviewer.md": _reviewer_md_body,
    "backend/app/kanban/analyst_prompt.py": _analyst_prompt_py_body,
    "mcp_server.move_card docstring": _mcp_move_card_docstring,
    "mcp_server.report_impediment docstring": _mcp_report_impediment_docstring,
    "dispatch._build_ship_instructions('direct')": _dispatch_direct_prompt,
    "dispatch._build_ship_instructions('pull-request')": _dispatch_pull_request_prompt,
    "dispatch._build_analyst_session_end_instructions()": _dispatch_analyst_prompt,
    ".claude/skills/git-ship/SKILL.md": _git_ship_skill_md_body,
}


@pytest.mark.parametrize(
    "source_name,invariant_label,anchor",
    [
        (source_name, label, anchor)
        for source_name in sorted(SOURCES)
        for label, anchor in SOURCE_PRODUCT_LANGUAGE_INVARIANTS[source_name]
    ],
    ids=[
        f"{source_name}|{label}"
        for source_name in sorted(SOURCES)
        for label, _ in SOURCE_PRODUCT_LANGUAGE_INVARIANTS[source_name]
    ],
)
def test_product_language_invariant_present_in_every_mirror(
    source_name: str, invariant_label: str, anchor: str
) -> None:
    """A core product-language-convention substring must appear in its mirror.

    Parametrised across (source × invariant) so a single regression points
    at exactly which mirror lost which substring — the failure message
    reads e.g. ``mcp_server.move_card docstring missing convention name:
    'product-taal'``.

    Some anchors are intentionally per-mirror (e.g. ``productbetekenis``
    belongs to the Done-summary surface and the ``move_card`` MCP
    docstring, but not to ``report_impediment``). The
    ``SOURCE_PRODUCT_LANGUAGE_INVARIANTS`` map captures which anchors
    must live on which mirrors; a missing entry there is a real
    omission, not a test bug.

    If this test fails: either the convention legitimately changed
    (update every mirror AND ``SOURCE_PRODUCT_LANGUAGE_INVARIANTS``),
    or a mirror silently drifted (revert the offending mirror to match
    the others). Do NOT delete an invariant to make the test pass —
    that's the regression this guard is here to catch.
    """
    source_text = SOURCES[source_name]()
    assert anchor in source_text, (
        f"{source_name} missing {invariant_label}: {anchor!r}. "
        f"Either the product-language convention changed (update every "
        f"mirror and SOURCE_PRODUCT_LANGUAGE_INVARIANTS) or the test is "
        f"stale."
    )


def test_product_language_convention_doc_has_before_after_examples() -> None:
    """The kanban-conventions §-anchor for the convention must include at
    least one explicit before/after example.

    The acceptance criteria (kaart ``4358fe0a…``) require "one or two
    concrete before/after examples" in the convention doc. Without an
    example the rule is too abstract to enforce consistently across
    agents — this guard ensures the doc carries at least one paired
    "before → after" so future readers see both shapes.
    """
    body = _conventions_md_body()
    # Cheap heuristic: a paired example needs both the words "before"
    # and "after" (or their Dutch convention-doc counterparts "Vóór" /
    # "Na") in the convention section. The convention is referenced via
    # the "product-taal" anchor (see CORE_PRODUCT_LANGUAGE_INVARIANTS)
    # so the section text can be located via its surrounding context.
    product_section_idx = body.find("product-taal")
    assert product_section_idx != -1, (
        "kanban-conventions.md: 'product-taal' section anchor not found — "
        "did the convention section get added?"
    )
    # Look at the next ~6 KB of doc text (the convention § + example
    # block are contiguous in normal usage; 6 KB is comfortable slack).
    section_window = body[product_section_idx:product_section_idx + 6144]
    has_before_marker = any(token in section_window.lower() for token in (
        "before", "vóór", "voor ", "oude ",
    ))
    has_after_marker = any(token in section_window.lower() for token in (
        "after", "na ", "nieuwe ",
    ))
    assert has_before_marker, (
        "kanban-conventions.md: product-taal section is missing a "
        "before/example marker (one of: 'before', 'vóór', 'voor ', "
        "'oude '). Add a concrete 'before' example to the convention."
    )
    assert has_after_marker, (
        "kanban-conventions.md: product-taal section is missing an "
        "after/example marker (one of: 'after', 'na ', 'nieuwe '). "
        "Add a concrete 'after' example to the convention."
    )


def test_product_language_convention_drift_detector_fails_when_mirror_loses_a_substring() -> None:
    """Demonstrate the drift detector catches a missing substring in one
    mirror. Builds a fake mirror that is missing the convention name
    and runs the same presence check the parametrised test runs. If
    this test ever stops failing-on-purpose, the detector's premise has
    rotted (e.g. the invariants list shrank to nothing) — pin it down
    with a live negative case so the contract is enforced, not assumed.
    """
    fake_mirror = (
        "The Done summary should describe what was built and link to the "
        "deliverable. Impediment options should explain the trade-offs in "
        "the implementation choice."
        # NOTE: does NOT contain "product-taal".
    )
    assert "product-taal" not in fake_mirror, (
        "test fixture bug: fake mirror unexpectedly contains 'product-taal' anchor"
    )
    missing = [
        (label, anchor)
        for label, anchor in CORE_PRODUCT_LANGUAGE_INVARIANTS
        if anchor not in fake_mirror
    ]
    assert ("convention name", "product-taal") in missing, (
        f"drift detector would NOT flag a fake mirror missing "
        f"'convention name'. Detected missing: {missing}"
    )
