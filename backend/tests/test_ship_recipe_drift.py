"""Drift-test for the direct-mode ship recipe.

The ship recipe (``git worktree add --detach ... && merge --no-ff && push``)
is intentionally duplicated across three mirrors:

  1. ``backend/app/kanban/dispatch.py::_build_ship_instructions``
     — the prompt the dispatcher injects into a fresh agent session.
  2. ``CLAUDE.md`` §Git Workflow "Finishing a branch" recipe
     — the operator-facing doc the running agent reads.
  3. ``.claude/skills/git-ship/SKILL.md`` §4a
     — the provider-agnostic skill (read when the agent has filesystem access).

The duplication is by design (a freshly spawned agent may not be able to read
``.claude/skills/``), but each recipe-edit otherwise has to be applied in all
three places by hand. Without a drift guard, a future edit that forgets one
mirror gives silent inconsistency between what the prompt says and what the
docs/skill say.

This test asserts the *core* recipe invariants appear in all three mirrors.
Adding or removing a command in one mirror without the others will fail the
parametrised test below on the next CI run, with a failure message that names
exactly which mirror lost which command.

The test reads ``CLAUDE.md`` and ``SKILL.md`` as plain text (they're checked
into the repo) and renders the dispatch prompt by calling
``dispatch._build_ship_instructions("direct")`` directly. The
``CORE_RECIPE_INVARIANTS`` list lives at module scope — edit it when the
recipe itself changes, so all three mirrors can be updated in lockstep with
the test as the safety net.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.kanban import dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]


# Core direct-mode ship-recipe invariants.
#
# Each entry is (human-readable label, anchored substring that must appear in
# every mirror). The label is used in the parametrised test id and in the
# failure message — keep it short and descriptive so a CI failure points the
# next editor at the right knob without opening the file.
#
# When the recipe itself changes (a new step, a renamed flag, a removed
# command): edit this list AND all three mirrors. The drift detector's whole
# point is that an inconsistency here is loud, not silent.
CORE_RECIPE_INVARIANTS: list[tuple[str, str]] = [
    # The throwaway detached worktree that sidesteps the "master already
    # checked out in the main worktree" error from git-worktree-add.
    (
        "detached-worktree merge target",
        'git worktree add --detach "$TMP/m" origin/master',
    ),
    # Required so master history shows a real merge commit, not a fast-forward.
    (
        "--no-ff merge flag",
        "merge --no-ff",
    ),
    # Direct-mode final push target — the merged commit lands on master.
    (
        "push-to-master",
        "push origin HEAD:master",
    ),
    # Pre-flight: the detached worktree only sees COMMITTED state, so an
    # uncommitted/untracked branch would merge as a silent no-op. This guard
    # catches that before `git worktree add` runs.
    (
        "pre-flight uncommitted-changes guard",
        "git diff --quiet HEAD",
    ),
    # Untracked-file counterpart of the above (git diff --quiet only checks
    # tracked changes).
    (
        "pre-flight untracked-files guard",
        "git ls-files --others --exclude-standard",
    ),
    # Throwaway detached worktree cleanup (otherwise /tmp leaks).
    (
        "worktree cleanup",
        'git worktree remove "$TMP/m" --force',
    ),
]


def _dispatch_direct_prompt() -> str:
    """Render the direct-mode ship instructions as the agent would see them.

    Pulling the prompt via the function (rather than grepping the file)
    guarantees we test the *rendered* string the agent actually receives —
    any future change that introduces a Python-side transformation would
    still be caught.
    """
    return dispatch._build_ship_instructions("direct")


def _claude_md_ship_recipe() -> str:
    """Extract the ship-recipe ```bash``` block from CLAUDE.md.

    We deliberately restrict to the fenced recipe block rather than matching
    against the whole file — CLAUDE.md is much larger and unrelated prose
    (notably the ``## Commands`` ``Install`` block) would otherwise dilute the
    assertion. The recipe block lives immediately after the unique
    "Finishing a branch" header bullet under ``## Git Workflow``; anchor on
    that header so a future Commands-section edit cannot accidentally land
    inside this extractor.
    """
    full = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    header_marker = "**Finishing a branch**"
    header_idx = full.find(header_marker)
    if header_idx == -1:
        raise AssertionError(
            "CLAUDE.md: expected the 'Finishing a branch' header bullet "
            "to anchor the ship-recipe extractor"
        )
    fence_start = full.find("```bash", header_idx)
    if fence_start == -1:
        raise AssertionError(
            "CLAUDE.md: 'Finishing a branch' header found but no ```bash "
            "fence follows it"
        )
    fence_end = full.find("```", fence_start + len("```bash"))
    if fence_end == -1:
        raise AssertionError(
            "CLAUDE.md: ship-recipe ```bash block is not closed"
        )
    return full[fence_start:fence_end]


def _skill_md_direct_recipe() -> str:
    """Read the full git-ship skill; §4a is the direct-mode recipe.

    Matching against the whole skill (rather than just §4a) keeps the test
    resilient to heading renames and whitespace tweaks inside the block —
    as long as the recipe commands remain present, the test passes.
    """
    return (REPO_ROOT / ".claude/skills/git-ship/SKILL.md").read_text(encoding="utf-8")


# Source registry: name -> callable that yields the source text. Using a dict
# so the parametrised test can iterate sources symmetrically and the failure
# message reads "SOURCE_NAME missing LABEL: 'command'", which is exactly what
# the next editor needs to know.
SOURCES: dict[str, callable[[], str]] = {
    "dispatch._build_ship_instructions('direct')": _dispatch_direct_prompt,
    "CLAUDE.md 'Finishing a branch' recipe block": _claude_md_ship_recipe,
    ".claude/skills/git-ship/SKILL.md": _skill_md_direct_recipe,
}


@pytest.mark.parametrize("source_name", sorted(SOURCES))
@pytest.mark.parametrize(
    "invariant_label,command",
    CORE_RECIPE_INVARIANTS,
    ids=[label for label, _ in CORE_RECIPE_INVARIANTS],
)
def test_core_recipe_command_present_in_every_mirror(
    source_name: str, invariant_label: str, command: str
) -> None:
    """A core direct-mode ship-recipe command must appear in every mirror.

    Parametrised across (source × invariant) so a single regression points at
    exactly which mirror lost which command — the failure message reads e.g.
    ``dispatch._build_ship_instructions('direct') missing pre-flight
    uncommitted-changes guard: 'git diff --quiet HEAD'``.

    If this test fails: either the recipe legitimately changed (update all
    three mirrors AND ``CORE_RECIPE_INVARIANTS``), or a mirror silently
    drifted (revert the offending mirror to match the other two). Do NOT
    delete an invariant to make the test pass — that's the regression we're
    guarding against.
    """
    source_text = SOURCES[source_name]()
    assert command in source_text, (
        f"{source_name} missing {invariant_label}: {command!r}. "
        f"Either the recipe changed (update all three mirrors) or the test "
        f"is stale (update CORE_RECIPE_INVARIANTS)."
    )


def test_invariants_list_covers_the_four_commands_from_the_card() -> None:
    """Sanity guard: the invariants list itself must list at least the four
    commands named in the [self-improve] card (pre-flight guard, worktree-add,
    merge --no-ff, push). A future editor who strips the list down to e.g.
    one command would still pass the parametrised test but defeat the drift
    detector's coverage — this guard keeps that from happening silently.
    """
    commands = [cmd for _, cmd in CORE_RECIPE_INVARIANTS]
    assert any("git worktree add --detach" in c for c in commands), (
        "invariants list lost the 'git worktree add --detach' core command"
    )
    assert "merge --no-ff" in commands, (
        "invariants list lost the 'merge --no-ff' core command"
    )
    assert "push origin HEAD:master" in commands, (
        "invariants list lost the 'push origin HEAD:master' core command"
    )
    assert "git diff --quiet HEAD" in commands, (
        "invariants list lost the pre-flight 'git diff --quiet HEAD' guard"
    )


def test_drift_detector_fails_when_mirror_loses_a_command() -> None:
    """Demonstrate the drift detector catches a missing command in one mirror.

    Builds a fake mirror that is missing the pre-flight guard and runs the
    same presence check the parametrised test runs. If this test ever stops
    failing-on-purpose, the detector's premise has rotted (e.g. the
    invariants list shrank to nothing) — pin it down with a live negative
    case so the contract is enforced, not assumed.
    """
    fake_mirror = (
        'git worktree add --detach "$TMP/m" origin/master\n'
        'git -C "$TMP/m" merge --no-ff "$BRANCH" -m "Merge $BRANCH"\n'
        'git -C "$TMP/m" push origin HEAD:master\n'
        'git worktree remove "$TMP/m" --force\n'
    )
    pre_flight_label, pre_flight_command = next(
        (label, cmd) for label, cmd in CORE_RECIPE_INVARIANTS
        if "pre-flight" in label and "uncommitted" in label
    )
    # Baseline: the fake mirror really is missing the pre-flight guard.
    assert pre_flight_command not in fake_mirror, (
        f"test fixture bug: fake mirror unexpectedly contains "
        f"{pre_flight_label}: {pre_flight_command!r}"
    )
    # Detector contract: the parametrised test would flag this. Replay its
    # exact check here so a future refactor of the parametrised test is
    # forced to keep the same failure mode.
    missing = [
        (label, cmd) for label, cmd in CORE_RECIPE_INVARIANTS
        if cmd not in fake_mirror
    ]
    assert (pre_flight_label, pre_flight_command) in missing, (
        f"drift detector would NOT flag a fake mirror missing "
        f"{pre_flight_label}: {pre_flight_command!r}. "
        f"Detected missing: {missing}"
    )


def test_pull_request_mode_is_explicitly_out_of_scope() -> None:
    """This drift detector guards the *direct*-mode ship recipe only.

    The pull-request mode in §4b uses a different recipe (``gh pr create``,
    ``gh pr merge --auto --squash``, poll loop) that lives in its own three
    mirrors and would warrant its own drift test if it ever grows to need
    one. Asserting that the detector's invariants are NOT pull-request
    primitives prevents a future editor from accidentally widening the test's
    scope and creating false positives whenever a PR-mode command drifts.
    """
    pr_mode_primitives = [
        "gh pr create --draft",
        "gh pr merge --auto --squash",
        "gh pr ready",
    ]
    for primitive in pr_mode_primitives:
        assert all(
            primitive not in cmd for _, cmd in CORE_RECIPE_INVARIANTS
        ), (
            f"invariants list contains PR-mode primitive {primitive!r} — "
            f"this drift detector is scoped to direct mode only"
        )
