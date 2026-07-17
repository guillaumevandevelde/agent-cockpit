"""Drift-test for the Feature-Compliance Review (FCR) prompt.

The FCR step is intentionally duplicated across two mirrors:

  1. ``.claude/agents/engineer.md`` §6 — the persona the agent reads when
     running a kanban card by hand.
  2. ``backend/app/kanban/dispatch.py::_build_ship_instructions`` — the
     prompt the dispatcher injects into a freshly-spawned agent session
     (both ``direct`` and ``pull-request`` ship modes).

This duplication mirrors the git-ship recipe pattern (see
``test_ship_recipe_drift.py`` and kanban card ``d9447e49`` for the
original drift-val). The drift guard ensures both mirrors stay in sync;
without it, an edit that forgets one mirror gives a silent inconsistency
between what the persona says and what the dispatched session actually
gets. The persona + dispatch duplication is by design — a freshly spawned
agent may not have filesystem access to read ``.claude/agents/`` itself,
so the canonical FCR prompt is also inlined into the dispatch prompt.

The invariants list lives at module scope — edit it (and both mirrors) in
the same commit whenever the FCR prompt legitimately changes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.kanban import dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]


# Core FCR-prompt invariants.
#
# Each entry is (human-readable label, anchored substring that must appear
# in every mirror). The label is used in the parametrised test id and the
# failure message — keep it short so a CI failure points the next editor
# at the right knob without opening the file.
#
# When the FCR prompt itself changes (a new requirement bullet, refined
# wording, a removed invariant): edit this list AND both mirrors in
# lockstep. The drift detector's whole point is that an inconsistency here
# is loud, not silent.
CORE_FCR_INVARIANTS: list[tuple[str, str]] = [
    # Marker that names the review itself — guarantees the search finds it.
    (
        "FCR review marker",
        "Feature-Compliance-Review",
    ),
    # Marker that names the cleared-context subagent mechanism (the FCR is
    # a Task/Agent call, not an inline review) — distinguishes from the
    # existing code-quality checks.
    (
        "subagent marker",
        "subagent-call",
    ),
    # Three mandatory inputs the FCR subagent receives.
    (
        "input: card title",
        "kaart-titel",
    ),
    (
        "input: card description",
        "kaart-beschrijving",
    ),
    (
        "input: diff against origin/master",
        "diff tegen",
    ),
    # Four specific bullets from reviewer-agent-decision.md
    # §"Wat lost de feature-compliance-review op?".
    (
        "requirement: every bullet implemented",
        "Elke requirement/bullet",
    ),
    (
        "requirement: API/UI matches spec",
        "API/UI matcht",
    ),
    (
        "requirement: no sibling breakage",
        "integreert zonder siblings te breken",
    ),
    (
        "requirement: deliverable claimed is present",
        "deliverable dat in de samenvatting geclaimd wordt",
    ),
    # Output contract — OK to ship, OR a list of blocking issues.
    (
        "output contract: OK-to-ship or blocking issues",
        "OK om te shippen",
    ),
    # Distinguishes FCR from code-quality (the latter is already covered
    # by /code-review and iteration-loop verify).
    (
        "non-overlap marker with code-review",
        "code-quality-check",
    ),
]


def _engineer_md_body() -> str:
    return (REPO_ROOT / ".claude" / "agents" / "engineer.md").read_text(encoding="utf-8")


def _dispatch_direct_prompt() -> str:
    """Render the direct-mode ship instructions as the agent would see them.

    Mirrors ``test_ship_recipe_drift._dispatch_direct_prompt`` — calling
    the function (rather than grepping the file) tests the *rendered*
    string the agent actually receives, so a future Python-side
    transformation is still caught.
    """
    return dispatch._build_ship_instructions("direct")


def _dispatch_pull_request_prompt() -> str:
    return dispatch._build_ship_instructions("pull-request")


# Source registry: name -> callable that yields the source text. Using a
# dict so the parametrised test iterates sources symmetrically and the
# failure message reads "SOURCE_NAME missing LABEL: 'substring'", which
# is exactly what the next editor needs to know.
SOURCES: dict[str, callable[[], str]] = {
    ".claude/agents/engineer.md": _engineer_md_body,
    "dispatch._build_ship_instructions('direct')": _dispatch_direct_prompt,
    "dispatch._build_ship_instructions('pull-request')": _dispatch_pull_request_prompt,
}


@pytest.mark.parametrize("source_name", sorted(SOURCES))
@pytest.mark.parametrize(
    "invariant_label,anchor",
    CORE_FCR_INVARIANTS,
    ids=[label for label, _ in CORE_FCR_INVARIANTS],
)
def test_fcr_invariant_present_in_every_mirror(
    source_name: str, invariant_label: str, anchor: str
) -> None:
    """A core FCR-prompt substring must appear in every mirror.

    Parametrised across (source × invariant) so a single regression points
    at exactly which mirror lost which substring — the failure message
    reads e.g. ``.claude/agents/engineer.md missing input: diff against
    origin/master: 'diff tegen'``.

    If this test fails: either the FCR legitimately changed (update both
    mirrors AND ``CORE_FCR_INVARIANTS``), or a mirror silently drifted
    (revert the offending mirror to match the other). Do NOT delete an
    invariant to make the test pass — that's the regression this guard
    is here to catch.
    """
    source_text = SOURCES[source_name]()
    assert anchor in source_text, (
        f"{source_name} missing {invariant_label}: {anchor!r}. "
        f"Either the FCR prompt changed (update both mirrors) or the "
        f"test is stale (update CORE_FCR_INVARIANTS)."
    )


def test_fcr_step_runs_before_ship_workflow() -> None:
    """The FCR step must appear BEFORE step 1 (Sync) in the dispatch prompt.

    Guards against ordering regressing — the FCR is a pre-Done gate, so
    it must come before the ship workflow's first numbered step. Without
    this guard, a future edit could reorder the sections and silently
    push the FCR after the merge-to-master step, defeating the gate.
    """
    for mode in ("direct", "pull-request"):
        instructions = dispatch._build_ship_instructions(mode)
        fcr_idx = instructions.lower().find("feature-compliance")
        sync_idx = instructions.find("1. **Sync**")
        assert fcr_idx != -1, (
            f"FCR step not found in dispatch._build_ship_instructions({mode!r})"
        )
        assert sync_idx != -1, (
            f"Sync step not found in dispatch._build_ship_instructions({mode!r}) — "
            f"expected '1. **Sync**'"
        )
        assert fcr_idx < sync_idx, (
            f"FCR step must appear BEFORE the Sync step in {mode!r} mode. "
            f"Found FCR at offset {fcr_idx}, Sync at offset {sync_idx}."
        )


def test_fcr_invariants_list_covers_the_required_inputs() -> None:
    """Sanity guard: the invariants list itself must cover the three
    canonical FCR inputs and the four canonical requirement bullets from
    ``reviewer-agent-decision.md`` §"Wat lost de feature-compliance-review
    op?". A future editor who strips the list down to e.g. one substring
    would still pass the parametrised test but defeat the drift
    detector's coverage — this guard keeps that from happening silently.
    """
    labels = [label for label, _ in CORE_FCR_INVARIANTS]
    required_inputs = {"input: card title", "input: card description",
                       "input: diff against origin/master"}
    assert required_inputs.issubset(set(labels)), (
        f"invariants list lost one or more required FCR inputs; "
        f"missing: {required_inputs - set(labels)}"
    )
    required_bullets = {
        "requirement: every bullet implemented",
        "requirement: API/UI matches spec",
        "requirement: no sibling breakage",
        "requirement: deliverable claimed is present",
    }
    assert required_bullets.issubset(set(labels)), (
        f"invariants list lost one or more FCR requirement bullets; "
        f"missing: {required_bullets - set(labels)}"
    )


def test_drift_detector_fails_when_mirror_loses_a_substring() -> None:
    """Demonstrate the drift detector catches a missing substring in one
    mirror. Builds a fake mirror that is missing one of the inputs and
    runs the same presence check the parametrised test runs. If this
    test ever stops failing-on-purpose, the detector's premise has
    rotted (e.g. the invariants list shrank to nothing) — pin it down
    with a live negative case so the contract is enforced, not assumed.
    """
    fake_mirror = (
        "We run a feature-compliance review against the spec.\n"
        "Inputs include the diff. We check that the API/UI matches and "
        "that no siblings break. We check the deliverable.\n"
    )
    missing_card_title_label, missing_card_title_anchor = (
        "input: card title", "kaart-titel",
    )
    assert missing_card_title_anchor not in fake_mirror, (
        f"test fixture bug: fake mirror unexpectedly contains "
        f"{missing_card_title_label}: {missing_card_title_anchor!r}"
    )
    missing = [
        (label, anchor)
        for label, anchor in CORE_FCR_INVARIANTS
        if anchor not in fake_mirror
    ]
    assert (missing_card_title_label, missing_card_title_anchor) in missing, (
        f"drift detector would NOT flag a fake mirror missing "
        f"{missing_card_title_label}: {missing_card_title_anchor!r}. "
        f"Detected missing: {missing}"
    )


def test_engineer_md_fcr_step_lives_in_review_section() -> None:
    """The engineer-persona FCR step must live in §6 (Zelf-review),
    i.e. AFTER the iteration-loop preset verify step and BEFORE the
    Werkomgeving section. Anchors: §6 heading text and Werkomgeving
    heading text. This guards against the FCR getting accidentally
    relocated into the operational guidance section or below the
    Kaart-bijwerken section where it would be invisible to the agent.
    """
    body = _engineer_md_body()
    section6_idx = body.find("Zelf-review via `iteration-loop`")
    fcr_idx = body.lower().find("feature-compliance")
    werkomgeving_idx = body.find("Werkomgeving in worktree")
    assert section6_idx != -1, "engineer.md: §6 'Zelf-review' anchor not found"
    assert fcr_idx != -1, "engineer.md: FCR step not found"
    assert werkomgeving_idx != -1, "engineer.md: 'Werkomgeving in worktree' anchor not found"
    assert section6_idx < fcr_idx < werkomgeving_idx, (
        f"engineer.md: FCR step must live inside §6 (between "
        f"'Zelf-review' at offset {section6_idx} and 'Werkomgeving in "
        f"worktree' at offset {werkomgeving_idx}). Found FCR at offset "
        f"{fcr_idx}."
    )