"""Drift-test for the source-analysedoc-update convention in the ship recipe.

The convention is intentionally duplicated across three mirrors:

  1. ``.claude/agents/engineer.md`` §5 (Verifiëren) — the persona the
     agent reads when running a kanban card by hand.
  2. ``backend/app/kanban/dispatch.py::_build_ship_instructions`` step
     3 (Commit) — the prompt the dispatcher injects into a freshly-
     spawned agent session (both ``direct`` and ``pull-request`` modes).
  3. ``.claude/skills/git-ship/SKILL.md`` step 3 (Commit) — the skill
     the agent reads when it has filesystem access.

The duplication mirrors the FCR + git-ship + rename-coverage pattern (see
``test_fcr_prompt_drift.py``, ``test_ship_recipe_drift.py``,
``test_rename_coverage_prompt_drift.py``). The drift guard ensures all
three mirrors stay in sync; without it, an edit that forgets one mirror
gives a silent inconsistency between what the persona says, what the
dispatched session actually gets, and what the skill recommends.

Kanban card ``64a8e424b92a4bb5bb59a6db9b577468`` recorded the gap this
convention closes: the four facet-docs of synthesis card ``c980a926`` kept
presenting themselves as pure "niets geïmplementeerd, alleen analyse"
documents while 33 of their 35 filed follow-ups had already merged — only
2 of the 4 docs had been updated. The convention asks the engineer who
lands a follow-up to add a ``✅ Geïmplementeerd (kaart <id>)`` line to the
source doc, before committing, so the doc cannot silently drift stale.

The invariants list lives at module scope — edit it (and all three
mirrors) in the same commit whenever the convention text legitimately
changes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.kanban import dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]


# Core source-doc-update-convention invariants.
#
# Each entry is (human-readable label, anchored substring that must
# appear in every mirror). The label is used in the parametrised test id
# and the failure message. Keep labels short so a CI failure points the
# next editor at the right knob without opening the file.
#
# When the convention itself changes (the marker format, the trigger
# metadata, the rationale card): edit this list AND all three mirrors in
# lockstep. The drift detector's whole point is that an inconsistency
# here is loud, not silent.
CORE_SOURCE_DOC_UPDATE_INVARIANTS: list[tuple[str, str]] = [
    # The marker line the engineer adds to the source doc — the strongest
    # single anchor, and the exact string a future reader will grep for.
    (
        "marker: ✅ Geïmplementeerd (kaart",
        "✅ Geïmplementeerd (kaart",
    ),
    # The trigger metadata — a follow-up card referencing its source doc
    # via metadata.facet (or metadata.parent_card).
    (
        "trigger: metadata.facet",
        "metadata.facet",
    ),
    # The doc family the convention applies to.
    (
        "scope: docs/cockpit/*.md",
        "docs/cockpit/*.md",
    ),
    # Why this exists — the stale-doc pattern from synthesis card c980a926.
    (
        "rationale: c980a926",
        "c980a926",
    ),
    # The explicit no-backfill boundary — matches the card's acceptance
    # criterion ("geen retroactieve verplichting"). Anchored on the single
    # word so a markdown line-wrap between "retroactieve" and "verplichting"
    # in the .md mirrors can't defeat the presence check.
    (
        "boundary: no-backfill (retroactieve)",
        "retroactieve",
    ),
]


def _engineer_md_body() -> str:
    return (REPO_ROOT / ".claude" / "agents" / "engineer.md").read_text(encoding="utf-8")


def _git_ship_skill_body() -> str:
    return (REPO_ROOT / ".claude" / "skills" / "git-ship" / "SKILL.md").read_text(encoding="utf-8")


def _dispatch_direct_prompt() -> str:
    """Render the direct-mode ship instructions as the agent would see them.

    Mirrors the FCR drift-guard pattern (``test_fcr_prompt_drift.py``) —
    calling the function (rather than grepping the file) tests the
    *rendered* string the agent actually receives, so a future Python-
    side transformation is still caught.
    """
    return dispatch._build_ship_instructions("direct")


def _dispatch_pull_request_prompt() -> str:
    return dispatch._build_ship_instructions("pull-request")


# Source registry: name -> callable yielding the source text. A dict so
# the parametrised test iterates sources symmetrically and the failure
# message reads "SOURCE_NAME missing LABEL: 'substring'", which is what
# the next editor needs to see.
SOURCES: dict[str, callable[[], str]] = {
    ".claude/agents/engineer.md": _engineer_md_body,
    ".claude/skills/git-ship/SKILL.md": _git_ship_skill_body,
    "dispatch._build_ship_instructions('direct')": _dispatch_direct_prompt,
    "dispatch._build_ship_instructions('pull-request')": _dispatch_pull_request_prompt,
}


@pytest.mark.parametrize("source_name", sorted(SOURCES))
@pytest.mark.parametrize(
    "invariant_label,anchor",
    CORE_SOURCE_DOC_UPDATE_INVARIANTS,
    ids=[label for label, _ in CORE_SOURCE_DOC_UPDATE_INVARIANTS],
)
def test_source_doc_update_invariant_present_in_every_mirror(
    source_name: str, invariant_label: str, anchor: str
) -> None:
    """A core source-doc-update-convention substring must appear in every mirror.

    Parametrised across (source × invariant) so a single regression
    points at exactly which mirror lost which substring — the failure
    message reads e.g. ``dispatch._build_ship_instructions('direct')
    missing trigger: metadata.facet: 'metadata.facet'``.

    If this test fails: either the convention legitimately changed (update
    all three mirrors AND ``CORE_SOURCE_DOC_UPDATE_INVARIANTS``), or a
    mirror silently drifted (revert the offending mirror to match the
    others). Do NOT delete an invariant to make the test pass — that's
    the regression this guard is here to catch.
    """
    source_text = SOURCES[source_name]()
    assert anchor in source_text, (
        f"{source_name} missing {invariant_label}: {anchor!r}. "
        f"Either the source-doc-update convention changed (update all "
        f"three mirrors) or the test is stale (update "
        f"CORE_SOURCE_DOC_UPDATE_INVARIANTS)."
    )


def test_source_doc_update_precedes_commit_step() -> None:
    """The convention must appear BEFORE the commit anchor in the dispatch
    prompt. Otherwise the engineer only sees it after the commit landed —
    too late to fold the doc update into the same commit.

    Guards against ordering regressing in either ship mode.
    """
    for mode in ("direct", "pull-request"):
        instructions = dispatch._build_ship_instructions(mode)
        convention_idx = instructions.find("✅ Geïmplementeerd (kaart")
        commit_idx = instructions.find("make sure every change is committed")
        assert convention_idx != -1, (
            f"source-doc-update convention not found in dispatch.{mode!r}"
        )
        assert commit_idx != -1, (
            f"'make sure every change is committed' anchor missing in {mode!r}"
        )
        assert convention_idx < commit_idx, (
            f"source-doc-update convention must appear BEFORE the commit "
            f"anchor in {mode!r} mode. "
            f"convention={convention_idx} commit={commit_idx}."
        )


def test_source_doc_update_drift_detector_fails_when_mirror_loses_a_substring() -> (
    None
):
    """Demonstrate the drift detector catches a missing substring in one
    mirror. Builds a fake mirror that is missing the marker anchor and
    runs the same presence check the parametrised test runs. If this
    test ever stops failing-on-purpose, the detector's premise has
    rotted (e.g. the invariants list shrank to nothing) — pin it down
    with a live negative case so the contract is enforced, not assumed.
    """
    fake_mirror = (
        "Werk het bron-analysedoc bij zodra een gefilede follow-up gemerged is. "
        "Voeg een korte regel toe aan de betreffende paragraaf."
        # NOTE: does NOT contain "✅ Geïmplementeerd (kaart".
    )
    assert "✅ Geïmplementeerd (kaart" not in fake_mirror, (
        "test fixture bug: fake mirror unexpectedly contains the marker anchor"
    )
    missing = [
        (label, anchor)
        for label, anchor in CORE_SOURCE_DOC_UPDATE_INVARIANTS
        if anchor not in fake_mirror
    ]
    assert ("marker: ✅ Geïmplementeerd (kaart", "✅ Geïmplementeerd (kaart") in missing, (
        f"drift detector would NOT flag a fake mirror missing 'marker'. "
        f"Detected missing: {missing}"
    )
