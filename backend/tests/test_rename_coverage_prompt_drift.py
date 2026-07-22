"""Drift-test for the schema/column rename coverage hook in the ship recipe.

The hook is intentionally duplicated across three mirrors:

  1. ``.claude/agents/engineer.md`` §5 (Verifiëren) — the persona the
     agent reads when running a kanban card by hand.
  2. ``backend/app/kanban/dispatch.py::_build_ship_instructions`` step
     3 (Commit) — the prompt the dispatcher injects into a freshly-
     spawned agent session (both ``direct`` and ``pull-request`` modes).
  3. ``.claude/skills/git-ship/SKILL.md`` step 3 (Commit) — the skill
     the agent reads when it has filesystem access.

The duplication mirrors the FCR + git-ship recipe pattern (see
``test_fcr_prompt_drift.py``, ``test_ship_recipe_drift.py``, and kanban
card ``d9447e49`` for the original drift-val). The drift guard ensures
all three mirrors stay in sync; without it, an edit that forgets one
mirror gives a silent inconsistency between what the persona says, what
the dispatched session actually gets, and what the skill recommends.

Kanban card ``ad15e08271c242238db239a90dc559d4`` recorded that
``commit 558ca55 refactor(backend): rename provider/platform terminology``
shipped with two silent-red tests because the rename commit had no
post-rename grep-sweep step. This hook + drift guard is the fix.

The invariants list lives at module scope — edit it (and all three
mirrors) in the same commit whenever the hook text legitimately changes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.kanban import dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]


# Core rename-coverage-hook invariants.
#
# Each entry is (human-readable label, anchored substring that must
# appear in every mirror). The label is used in the parametrised test id
# and the failure message. Keep labels short so a CI failure points the
# next editor at the right knob without opening the file.
#
# When the hook itself changes (the script path, the flags, the rationale
# wording): edit this list AND all three mirrors in lockstep. The drift
# detector's whole point is that an inconsistency here is loud, not
# silent.
CORE_RENAME_COVERAGE_INVARIANTS: list[tuple[str, str]] = [
    # The script path itself — the strongest single anchor.
    (
        "script path",
        "check-schema-rename-coverage",
    ),
    # The trigger condition — any rename (ALTER TABLE ... RENAME COLUMN).
    (
        "trigger: ALTER TABLE RENAME COLUMN",
        "ALTER TABLE",
    ),
    (
        "trigger: RENAME COLUMN form",
        "RENAME COLUMN",
    ),
    # The blocking flag — distinguishes advisory from pre-commit-gate.
    (
        "blocking flag",
        "--strict",
    ),
    # The scope of the sweep — references to both backend/app + backend/tests
    # so a Pydantic field rename in tests/ cannot slip through.
    (
        "scope: backend/app",
        "backend/app",
    ),
    (
        "scope: backend/tests",
        "backend/tests",
    ),
    # Why this exists — the silent-red pattern from commit 558ca55.
    (
        "rationale: 558ca55",
        "558ca55",
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
    CORE_RENAME_COVERAGE_INVARIANTS,
    ids=[label for label, _ in CORE_RENAME_COVERAGE_INVARIANTS],
)
def test_rename_coverage_invariant_present_in_every_mirror(
    source_name: str, invariant_label: str, anchor: str
) -> None:
    """A core rename-coverage-hook substring must appear in every mirror.

    Parametrised across (source × invariant) so a single regression
    points at exactly which mirror lost which substring — the failure
    message reads e.g. ``dispatch._build_ship_instructions('direct')
    missing scope: backend/tests: 'backend/tests'``.

    If this test fails: either the hook legitimately changed (update
    all three mirrors AND ``CORE_RENAME_COVERAGE_INVARIANTS``), or a
    mirror silently drifted (revert the offending mirror to match the
    others). Do NOT delete an invariant to make the test pass — that's
    the regression this guard is here to catch.
    """
    source_text = SOURCES[source_name]()
    assert anchor in source_text, (
        f"{source_name} missing {invariant_label}: {anchor!r}. "
        f"Either the rename-coverage hook changed (update all three "
        f"mirrors) or the test is stale (update "
        f"CORE_RENAME_COVERAGE_INVARIANTS)."
    )


def test_rename_coverage_hook_precedes_merge_step() -> None:
    """The rename-coverage hook must appear BEFORE the merge step in
    the dispatch prompt. Otherwise the engineer only sees it after the
    commit landed — too late to fix a missed reference.

    Guards against ordering regressing in either ship mode. Without this
    guard, a future edit could push the hook after the ``git push`` step
    and silently defeat the pre-commit-gate promise the hook makes.
    """
    for mode in ("direct", "pull-request"):
        instructions = dispatch._build_ship_instructions(mode)
        hook_idx = instructions.find("check-schema-rename-coverage")
        commit_idx = instructions.find(
            'make sure every change is committed'
        )
        # In pull-request mode the merge step uses `gh pr merge`; in
        # direct mode it's `git -C "$WT" push`. Anchor on the first
        # push-like verb as a coarse "merge-or-push" marker.
        if mode == "direct":
            push_idx = instructions.find("push origin HEAD:master")
        else:
            push_idx = instructions.find("gh pr create")
        assert hook_idx != -1, (
            f"rename-coverage hook not found in dispatch.{mode!r}"
        )
        assert commit_idx != -1, (
            f"'make sure every change is committed' anchor missing in {mode!r}"
        )
        assert push_idx != -1, (
            f"push/create-PR anchor missing in {mode!r}"
        )
        assert hook_idx < commit_idx < push_idx, (
            f"rename-coverage hook must appear BEFORE both the commit "
            f"step and the push/PR step in {mode!r} mode. "
            f"hook={hook_idx} commit={commit_idx} push={push_idx}."
        )


def test_rename_coverage_script_exists_and_is_executable() -> None:
    """The referenced script must exist and be executable. A drift that
    only updates the prompt mirrors but forgets to land the actual
    script file would make the hook point at a missing executable —
    this guard catches that regression loudly instead of letting the
    engineer discover it the hard way at runtime.
    """
    script = REPO_ROOT / "scripts" / "check-schema-rename-coverage.sh"
    assert script.exists(), (
        f"referenced script missing: {script}. Did the script file land "
        f"in the same commit as the prompt-mirror edits?"
    )
    assert script.stat().st_mode & 0o111, (
        f"{script} is not executable. chmod +x before committing."
    )


def test_rename_coverage_drift_detector_fails_when_mirror_loses_a_substring() -> (
    None
):
    """Demonstrate the drift detector catches a missing substring in one
    mirror. Builds a fake mirror that is missing the script path and
    runs the same presence check the parametrised test runs. If this
    test ever stops failing-on-purpose, the detector's premise has
    rotted (e.g. the invariants list shrank to nothing) — pin it down
    with a live negative case so the contract is enforced, not assumed.
    """
    fake_mirror = (
        "We re-grep for stale references after a rename. Run the "
        "helper script in --strict mode before committing."
        # NOTE: does NOT contain "check-schema-rename-coverage".
    )
    assert "check-schema-rename-coverage" not in fake_mirror, (
        "test fixture bug: fake mirror unexpectedly contains the script "
        "path anchor"
    )
    missing = [
        (label, anchor)
        for label, anchor in CORE_RENAME_COVERAGE_INVARIANTS
        if anchor not in fake_mirror
    ]
    assert ("script path", "check-schema-rename-coverage") in missing, (
        f"drift detector would NOT flag a fake mirror missing 'script "
        f"path'. Detected missing: {missing}"
    )