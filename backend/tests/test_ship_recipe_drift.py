"""Drift-test for the direct-mode ship recipe.

The ship recipe (``git worktree add --detach ... && merge --no-ff && push``)
is intentionally duplicated across two content mirrors:

  1. ``backend/app/kanban/dispatch.py::_build_ship_instructions``
     — the prompt the dispatcher injects into a fresh agent session.
  2. ``.claude/skills/git-ship/SKILL.md`` §4a
     — the provider-agnostic skill (read when the agent has filesystem access),
     bron van waarheid.

The duplication is by design (a freshly spawned agent may not be able to read
``.claude/skills/``), but each recipe-edit otherwise has to be applied in both
places by hand. Without a drift guard, a future edit that forgets one mirror
gives silent inconsistency between what the prompt says and what the skill
says.

``CLAUDE.md`` §Git Workflow used to be a third content mirror, but was
deliberately trimmed to a pointer (commit 4c697d0, "CLAUDE.md <200 regels —
ship-recipes als pointer") that redirects to the skill and to
``_build_ship_instructions`` instead of carrying its own copy of the recipe.
It is still guarded here, just differently: ``test_claude_md_points_at_the_two_mirrors``
asserts the pointer paragraph still names both mirrors, so a rename of the
skill path or the dispatch function would still be caught.

This test asserts the *core* recipe invariants appear in both content
mirrors. Adding or removing a command in one mirror without the other will
fail the parametrised test below on the next CI run, with a failure message
that names exactly which mirror lost which command.

The test reads ``SKILL.md`` and ``CLAUDE.md`` as plain text (they're checked
into the repo) and renders the dispatch prompt by calling
``dispatch._build_ship_instructions("direct")`` directly. The
``CORE_RECIPE_INVARIANTS`` list lives at module scope — edit it when the
recipe itself changes, so both content mirrors can be updated in lockstep
with the test as the safety net.
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
    # checked out in the main worktree" error from git-worktree-add. The
    # worktree lives under the shared `.git/worktrees/<name>` (NOT under
    # `mktemp -d`) so the Bash tool's harness can't reap it between calls —
    # otherwise the merge commit lands in a vanished checkout and the
    # subsequent `git push` fails with a spurious non-fast-forward
    # (kanban card 01aa1ef5…). Slot name is `ship-merge-$$` (PID-unique),
    # not a fixed name — a fixed slot collides across concurrent dispatched
    # sessions (kanban card c23dfe46).
    (
        "detached-worktree merge target",
        'git worktree add --detach "$WT" origin/master',
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
    # Remote-branch cleanup after a successful merge-to-master. GitHub's
    # `delete_branch_on_merge` (enabled 2026-07-07) only fires when a *PR*
    # merges; the direct route closes no PR, so without this command every
    # shipped card leaves a dead branch on `origin` forever — 7 fully-merged
    # branches accumulated over 6 weeks before this was caught (kanban card
    # 3027671c…). Position matters as much as presence: see
    # `test_branch_delete_guarded_by_push_success` for the invariant that it
    # must run *only* when the push to master succeeded.
    (
        "remote branch cleanup",
        'git push origin --delete "$BRANCH"',
    ),
    # Pre-flight: the detached worktree only sees COMMITTED state, so an
    # uncommitted/untracked branch would merge as a silent no-op. This guard
    # catches that before `git worktree add` runs.
    (
        "pre-flight uncommitted-changes guard",
        "git diff --quiet HEAD",
    ),
    # Tracked-files-only counterpart of the above. Deliberately NOT
    # `[ -n "$(git ls-files --others --exclude-standard)" ]`: on this shared
    # box concurrent dispatched sessions share a worktree mount, so a foreign
    # session's untracked scratch output (e.g. a `.tmp-measure-*/` harness dir
    # with hundreds of files) blocked the ship for changes the merge never
    # reads — and `rm` is deny-listed, so recovery meant a `mv` dance
    # (kanban card c28e576d…). `git status --porcelain | grep -v '^??'` keeps
    # every tracked state (` M`, `M `, `MM`, `A `, `D `) and drops only `??`.
    (
        "pre-flight tracked-changes guard",
        "git status --porcelain | grep -v '^??'",
    ),
    # Throwaway detached worktree cleanup. Slot name MUST be the same `ship-merge-$$`
    # used by `git worktree add` so the remove targets the entry git actually created.
    (
        "worktree cleanup",
        'git worktree remove --force "$WT"',
    ),
    # Generated documentation conflicts have a deterministic recovery path in
    # both mirrors; keep the filenames and strict verification command pinned.
    (
        "generated README conflict carve-out",
        "docs/cockpit/README.md",
    ),
    (
        "generated llms conflict carve-out",
        "docs/cockpit/llms.txt",
    ),
    (
        "generated index strict verification",
        "generate-doc-index.py --check --strict",
    ),
    # The carve-out must enumerate the conflict set with a machine-checkable
    # command so the "exclusively in generated artifacts" condition is a
    # hard predicate, not a judgement call (kanban card efb8187b…).
    (
        "conflict-set enumeration",
        'git -C "$WT" diff --name-only --diff-filter=U',
    ),
    # Carve-out must accept a non-empty subset of {README.md, llms.txt},
    # not the exact pair — a conflict in only one of the two is the same
    # class as both, and both files are regenerated from frontmatter
    # regardless (kanban card 72db7429…). The previous
    # `[ "$CONFLICTED" != "$EXPECTED" ]` exact-equality check rejected the
    # subset and forced a fallback to report_impediment. A subset idiom
    # (e.g. `comm -23` to surface non-generated paths) is the
    # machine-checkable predicate that supersedes it.
    (
        "conflict-set subset check (non-generated exclusion)",
        "comm -23",
    ),
    # README.md is partially generated: only the block between
    # `BEGIN GENERATED DOC INDEX` and `END GENERATED DOC INDEX` is owned
    # by `scripts/generate-doc-index.py`. The surrounding hand-curated
    # prose (feature → canonical-doc mapping, "Regels", etc.) must NOT
    # be silently clobbered by the regenerate. The carve-out therefore
    # verifies that any conflict hunks in README.md all lie between
    # those markers — anything outside still falls through to
    # report_impediment (kanban card 72db7429…).
    (
        "README marker-boundary check (BEGIN side)",
        "BEGIN GENERATED DOC INDEX",
    ),
    (
        "README marker-boundary check (END side)",
        "END GENERATED DOC INDEX",
    ),
    # Concrete resolution command for the carved-out conflict files. The
    # abstract "keep the generated files from the merge result" wording
    # that this replaced (kanban card efb8187b…) had no operational meaning
    # — the agent had to improvise a command and got it wrong. `--theirs`
    # takes the branch-being-merged's regenerated content as a placeholder
    # that the next `generate-doc-index.py` step overwrites anyway.
    (
        "concrete generated-file resolution",
        'git -C "$WT" checkout --theirs -- docs/cockpit/README.md docs/cockpit/llms.txt',
    ),
    # `scripts/generate-doc-index.py:78` derives its repo-root from
    # `Path(__file__).resolve().parent.parent`, so the script MUST be invoked
    # via the worktree path. A bare `./scripts/generate-doc-index.py` (or
    # even `cd $WT && ./scripts/generate-doc-index.py`) regenerates the
    # calling shell's tree, not the conflicted `$WT` — see kanban card
    # efb8187b… for the failure mode.
    (
        "worktree-path script invocation",
        '"$WT"/scripts/generate-doc-index.py',
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


def _claude_md_git_workflow_section() -> str:
    """Extract the ``## Git Workflow`` section of CLAUDE.md (up to the next
    ``## `` heading).

    CLAUDE.md no longer carries its own copy of the recipe — it points at the
    two content mirrors instead (see module docstring). Restrict to this
    section rather than the whole file so unrelated prose elsewhere in
    CLAUDE.md can't accidentally satisfy the pointer assertions below.
    """
    full = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    header_marker = "## Git Workflow"
    header_idx = full.find(header_marker)
    if header_idx == -1:
        raise AssertionError(
            "CLAUDE.md: expected a '## Git Workflow' section to anchor the "
            "ship-recipe pointer check"
        )
    next_heading_idx = full.find("\n## ", header_idx + len(header_marker))
    if next_heading_idx == -1:
        return full[header_idx:]
    return full[header_idx:next_heading_idx]


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
#
# CLAUDE.md is deliberately NOT in this registry: it no longer carries its
# own copy of the recipe (see module docstring), so it structurally cannot
# contain these bash-command invariants. It is still guarded, just by
# test_claude_md_points_at_the_two_mirrors below.
SOURCES: dict[str, callable[[], str]] = {
    "dispatch._build_ship_instructions('direct')": _dispatch_direct_prompt,
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
    """A core direct-mode ship-recipe command must appear in every content mirror.

    Parametrised across (source × invariant) so a single regression points at
    exactly which mirror lost which command — the failure message reads e.g.
    ``dispatch._build_ship_instructions('direct') missing pre-flight
    uncommitted-changes guard: 'git diff --quiet HEAD'``.

    If this test fails: either the recipe legitimately changed (update both
    content mirrors AND ``CORE_RECIPE_INVARIANTS``), or a mirror silently
    drifted (revert the offending mirror to match the other one). Do NOT
    delete an invariant to make the test pass — that's the regression we're
    guarding against.
    """
    source_text = SOURCES[source_name]()
    assert command in source_text, (
        f"{source_name} missing {invariant_label}: {command!r}. "
        f"Either the recipe changed (update both content mirrors) or the "
        f"test is stale (update CORE_RECIPE_INVARIANTS)."
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
    # The carve-out has its own dedicated invariants — pin them too so a
    # future editor can't soften the carve-out back to prose without
    # breaking this guard. See kanban card efb8187b… for the failure mode
    # that motivated each one.
    assert any("--diff-filter=U" in c for c in commands), (
        "invariants list lost the conflict-set enumeration command"
    )
    assert any("checkout --theirs" in c for c in commands), (
        "invariants list lost the concrete generated-file resolution command"
    )
    assert any(c == '"$WT"/scripts/generate-doc-index.py' for c in commands), (
        "invariants list lost the worktree-path script invocation command"
    )
    # Subset + marker-boundary invariants (kanban card 72db7429…). The
    # exact-equality `[ "$CONFLICTED" != "$EXPECTED" ]` check rejected
    # the legitimate subset of {README.md, llms.txt}; the new predicate
    # is a subset exclusion (`comm -23` over the expected set) plus a
    # marker-boundary check for README.md conflicts. Pin both so the
    # carve-out can't silently regress to the strict exact-equality
    # form.
    assert any("comm -23" in c for c in commands), (
        "invariants list lost the conflict-set subset check (comm -23)"
    )
    assert any("BEGIN GENERATED DOC INDEX" in c for c in commands), (
        "invariants list lost the README marker-boundary check"
    )
    # Remote-branch cleanup (kanban card 3027671c…). Without it the direct
    # route leaks a merged branch onto `origin` on every single ship.
    assert any("push origin --delete" in c for c in commands), (
        "invariants list lost the remote branch cleanup command"
    )


def test_claude_md_points_at_the_two_mirrors() -> None:
    """CLAUDE.md carries no recipe of its own; it must still name both mirrors.

    CLAUDE.md §Git Workflow was deliberately trimmed to a pointer (commit
    4c697d0) instead of holding its own copy of the recipe, so it can't be
    checked against ``CORE_RECIPE_INVARIANTS`` like the two content mirrors
    (see module docstring). What it *can* still drift on is the pointer
    itself: if the skill moves or the dispatch function is renamed without
    updating this paragraph, an agent following CLAUDE.md would be sent to a
    dead reference. Assert both names are still present.
    """
    section = _claude_md_git_workflow_section()
    assert ".claude/skills/git-ship/SKILL.md" in section, (
        "CLAUDE.md 'Git Workflow' section no longer points at "
        "'.claude/skills/git-ship/SKILL.md'"
    )
    assert "_build_ship_instructions" in section, (
        "CLAUDE.md 'Git Workflow' section no longer points at "
        "'_build_ship_instructions' (the dispatch.py mirror)"
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
        'git worktree add --detach "$WT" origin/master\n'
        'git -C "$WT" merge --no-ff "$BRANCH" -m "Merge $BRANCH"\n'
        'git -C "$WT" push origin HEAD:master\n'
        'git worktree remove "$WT" --force\n'
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


# The blocking pre-flight condition, as a single shell test. Both mirrors must
# use exactly this — a bare `git ls-files --others --exclude-standard` inside
# the *blocking* condition is the regression this pins (kanban card
# c28e576d…): on this shared box, concurrent dispatched sessions write
# untracked scratch output into the same worktree mount, and the throwaway
# merge worktree reads COMMITTED state only, so untracked files cannot cause
# the silent no-op the guard exists to prevent.
BLOCKING_PREFLIGHT = (
    'if ! git diff --quiet HEAD || [ -n "$(git status --porcelain | grep -v \'^??\')" ]; then'
)


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_blocking_preflight_ignores_foreign_untracked_files(source_name: str) -> None:
    """The blocking pre-flight must test tracked changes only.

    Regression pin for kanban card ``c28e576d…``: the previous condition
    ``[ -n "$(git ls-files --others --exclude-standard)" ]`` aborted a ship
    because a *different* dispatched session had left 544 untracked files in
    ``.tmp-measure-token-saver/`` in the shared worktree mount. Nothing the
    merge reads was affected (the detached worktree only ever sees committed
    state), and because ``rm`` is deny-listed in this repo the only recovery
    was moving another session's work aside with ``mv``.

    ``git status --porcelain | grep -v '^??'`` keeps every tracked state
    (`` M``, ``M ``, ``MM``, ``A ``, ``D ``, ``R ``) — so a session that ran
    ``git add`` but not ``git commit`` is still refused — and drops only the
    ``??`` untracked lines.
    """
    source_text = SOURCES[source_name]()
    assert BLOCKING_PREFLIGHT in source_text, (
        f"{source_name}: blocking pre-flight is not the tracked-changes-only "
        f"form. Expected the line {BLOCKING_PREFLIGHT!r}."
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_untracked_files_are_advisory_not_blocking(source_name: str) -> None:
    """Untracked files must still be *reported*, just never fatal.

    Dropping ``??`` from the blocking condition removes a real (if noisy)
    safety net: a session that created a brand-new file and forgot to
    ``git add`` it would now ship a merge without it, silently. The fix keeps
    the signal as a non-fatal advisory — an untracked listing printed before
    the merge, with no ``exit 1`` attached — so the agent can spot its own
    forgotten file while a foreign session's scratch dir stays harmless.

    Asserts (a) the advisory names the untracked-listing command, and (b) the
    only ``exit 1`` in the pre-flight belongs to the blocking condition, i.e.
    the advisory is not silently upgraded back to a hard abort.
    """
    source_text = SOURCES[source_name]()
    assert "git ls-files --others --exclude-standard" in source_text, (
        f"{source_name}: lost the untracked-files advisory entirely — "
        f"untracked files must still be surfaced, just not block the ship."
    )

    # The advisory sits after the blocking `fi`, before `git worktree add`.
    blocking_idx = source_text.index(BLOCKING_PREFLIGHT)
    worktree_add_idx = source_text.index("git worktree add --detach")
    advisory_region = source_text[blocking_idx:worktree_add_idx]
    assert "git ls-files --others --exclude-standard" in advisory_region, (
        f"{source_name}: untracked advisory is not between the blocking "
        f"pre-flight and `git worktree add` — it would not run before the merge."
    )

    # Exactly one `exit 1` in the pre-flight region: the blocking condition's.
    # A second one would mean the advisory became fatal again.
    assert advisory_region.count("exit 1") == 1, (
        f"{source_name}: expected exactly 1 `exit 1` in the pre-flight region "
        f"(the tracked-changes guard), found "
        f"{advisory_region.count('exit 1')} — the untracked advisory must not "
        f"abort the ship (kanban card c28e576d…)."
    )


def test_blocking_preflight_would_flag_the_old_untracked_shape() -> None:
    """Live negative case: the old shape must fail the new invariant.

    Without this, a future editor could revert the blocking condition to the
    ``git ls-files``-based form and only notice via a substring test that no
    longer applies. Replays the exact check against a fake mirror carrying the
    pre-fix condition.
    """
    old_shape_mirror = (
        'if ! git diff --quiet HEAD || [ -n "$(git ls-files --others --exclude-standard)" ]; then\n'
        "  echo 'ERROR: uncommitted/untracked changes' >&2; exit 1\n"
        "fi\n"
        'git worktree add --detach "$WT" origin/master\n'
    )
    assert BLOCKING_PREFLIGHT not in old_shape_mirror, (
        "the tracked-changes invariant no longer distinguishes the old "
        "untracked-blocking shape — the regression pin has rotted."
    )


# Carve-out recovery markers for the positional invariant. The carve-out is
# the auto-recovery path that fires when the merge hits a conflict in exactly
# the two generated-doc files; it must live *inside* the merge-handler
# ``if``-block, not as prose below an unconditional ``exit 1`` (the exact
# failure mode of kanban-kaart ``efb8187b…`` / ``c06a3a2a…``). The opening
# marker is the first concrete recovery command — the script MUST be invoked
# through the worktree path, so the carve-out starts with the `--theirs`
# checkout that clears merge markers. The closing marker is the commit that
# finalises the merge result.
CARVE_OUT_OPEN = 'git -C "$WT" checkout --theirs -- docs/cockpit/README.md docs/cockpit/llms.txt'
CARVE_OUT_CLOSE = 'git -C "$WT" commit --no-edit'
MERGE_HANDLER = "merge --no-ff"
PUSH_HANDLER = "push origin HEAD:master"


def _line_indent(line: str) -> int:
    """Return the leading-whitespace count of ``line``. Used to detect an
    unconditional ``exit 1`` (same indent as the merge handler) vs. a
    legitimate nested exit (deeper indent, inside a sub-``if``)."""
    return len(line) - len(line.lstrip())


def _carve_out_is_in_recovery_path(source_text: str) -> tuple[bool, str]:
    """Return ``(ok, reason)``: whether the carve-out lives in the executable
    recovery path between ``merge --no-ff`` and ``push origin HEAD:master``,
    and not as unreachable prose below the merge-handler's closing ``fi``.

    Walks the source line-by-line between the merge-handler line and the
    push-handler line, and checks:

    1. The carve-out opens (``CARVE_OUT_OPEN``) somewhere inside that span.
    2. No ``fi`` at the merge-handler's indent level appears *before* the
       carve-out opening — such a ``fi`` closes the merge-handler ``if``-
       block, leaving the carve-out as unreachable prose below the handler
       (the exact failure mode of kanban-kaart ``efb8187b…``). Nested
       ``fi`` lines (deeper indent) are legitimate: they close the
       carve-out-rejected branch (``if [ "$CONFLICTED" != "$EXPECTED" ]``)
       and the strict-check guard (``if ! --check --strict``).

    The check is intentionally *positional*, complementing the
    substring-presence invariants in ``CORE_RECIPE_INVARIANTS``: those pin
    *what* must appear in each mirror, this one pins *where* the carve-out
    must sit for it to actually fire in the conflict scenario.
    """
    merge_idx = source_text.find(MERGE_HANDLER)
    carve_open_idx = source_text.find(CARVE_OUT_OPEN)
    carve_close_idx = source_text.find(CARVE_OUT_CLOSE)
    push_idx = source_text.find(PUSH_HANDLER)
    if merge_idx == -1 or carve_open_idx == -1 or push_idx == -1:
        return False, (
            f"missing one of: merge_idx={merge_idx}, "
            f"carve_open_idx={carve_open_idx}, push_idx={push_idx}"
        )
    if not (merge_idx < carve_open_idx < push_idx):
        return False, (
            f"carve-out is NOT between merge --no-ff ({merge_idx}) and "
            f"push origin HEAD:master ({push_idx}); found at {carve_open_idx}"
        )

    # Determine the merge-handler line's indent so we can spot a ``fi``
    # that closes the merge-handler ``if``-block prematurely.
    merge_line_start = source_text.rfind("\n", 0, merge_idx) + 1
    merge_line_end = source_text.find("\n", merge_idx)
    merge_line = source_text[merge_line_start:merge_line_end]
    merge_indent = _line_indent(merge_line)

    # Walk lines between the merge-handler and the carve-out opening,
    # flagging any ``fi`` at the merge-handler's indent (which would close
    # the ``if``-block before the carve-out runs).
    before_carve = source_text[merge_line_start:carve_open_idx]
    for raw_line in before_carve.split("\n"):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if MERGE_HANDLER in stripped:
            continue
        line_indent = _line_indent(raw_line)
        if stripped == "fi" and line_indent == merge_indent:
            return False, (
                f"`fi` at merge-handler indent {merge_indent} found BEFORE "
                f"the carve-out opening — the if-block closes prematurely "
                f"and the recovery is unreachable as prose below "
                f"(kanban-kaart `c06a3a2a…`/`efb8187b…`). "
                f"Offending line at indent {line_indent}: {stripped!r}"
            )

    # Sanity: the carve-out close (``commit --no-edit``) must also be inside
    # the merge→push span, otherwise the recovery block is broken.
    if carve_close_idx == -1 or not (merge_idx < carve_close_idx < push_idx):
        return False, (
            f"carve-out close ({CARVE_OUT_CLOSE!r}) is NOT between merge "
            f"and push (found at {carve_close_idx})"
        )
    return True, ""


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_carve_out_substring_in_recovery_path(source_name: str) -> None:
    """The carve-out must live in the executable recovery path, not in prose.

    Pins the position-based invariant from kanban-kaart ``c06a3a2a…`` /
    ``efb8187b…``: the ``docs/cockpit/README.md`` + ``docs/cockpit/llms.txt``
    recovery (``checkout --theirs`` → regenerate → ``--check --strict`` →
    ``add -A`` → ``commit --no-edit``) must physically sit inside the
    ``if ! git -C "$WT" merge --no-ff …; then … fi`` block, between
    ``merge --no-ff`` and ``push origin HEAD:master``. An unconditional
    ``exit 1`` (same indent as the merge handler) anywhere before the
    carve-out opening makes the recovery unreachable in exactly the
    scenario it was meant to handle — the original bug shipped because the
    22 substring-presence drift tests in ``CORE_RECIPE_INVARIANTS`` only
    verified presence, not structural reachability.

    This test complements the parametrised substring-presence test:
    presence says "the recovery is *somewhere* in the mirror"; this test
    says "the recovery is in the executable path". A future edit that
    demotes the carve-out to a comment below the merge handler (the
    ``efb8187b…`` regression) trips this test on the next CI run, with a
    failure message naming the offending line.
    """
    source_text = SOURCES[source_name]()
    ok, reason = _carve_out_is_in_recovery_path(source_text)
    assert ok, (
        f"{source_name}: carve-out not in recovery path — {reason}"
    )


# Remote-branch cleanup markers for the second positional invariant. The
# delete must be *guarded by push success*: `git push origin --delete` is
# irreversible-ish (the branch is gone from `origin`), and the recipe
# explicitly falls back to the pull-request route when the push to master is
# rejected (master moved / protected). Deleting the branch on that failure
# path would strand the work — the PR route needs `origin/$BRANCH` to exist.
# So the delete must sit inside the `if <push>; then` success branch, not
# unconditionally after a bare push. (kanban card 3027671c…)
BRANCH_DELETE = 'git push origin --delete "$BRANCH"'
PUSH_CONDITIONAL = 'if git -C "$WT" push origin HEAD:master; then'


def _branch_delete_is_guarded_by_push_success(source_text: str) -> tuple[bool, str]:
    """Return ``(ok, reason)``: whether the remote-branch delete runs only on
    a successful push to master.

    Checks, in order:

    1. Both the push and the delete are present.
    2. The push-to-master is used as an ``if`` *condition* (``PUSH_CONDITIONAL``)
       rather than a bare statement — a bare ``git push … HEAD:master`` followed
       by an unconditional delete would nuke ``origin/$BRANCH`` even when the
       push was rejected.
    3. The delete sits between that condition and the ``else``/``fi`` that
       closes it at the same indent — i.e. in the *success* branch, not the
       rejection branch and not after the block.

    Positional, like ``_carve_out_is_in_recovery_path``: the substring
    invariant in ``CORE_RECIPE_INVARIANTS`` pins *that* the delete exists,
    this pins *when it fires*.
    """
    push_idx = source_text.find(PUSH_HANDLER)
    delete_idx = source_text.find(BRANCH_DELETE)
    if push_idx == -1 or delete_idx == -1:
        return False, (
            f"missing one of: push_idx={push_idx}, delete_idx={delete_idx} "
            f"(expected {PUSH_HANDLER!r} and {BRANCH_DELETE!r})"
        )
    if delete_idx < push_idx:
        return False, (
            f"branch delete ({delete_idx}) appears BEFORE the push to master "
            f"({push_idx}) — the branch would be deleted while the merge is "
            f"still unpushed"
        )
    cond_idx = source_text.find(PUSH_CONDITIONAL)
    if cond_idx == -1:
        return False, (
            f"push-to-master is not used as an if-condition "
            f"({PUSH_CONDITIONAL!r} not found) — an unconditional delete after "
            f"a bare push would also fire when the push is REJECTED, deleting "
            f"the branch the pull-request fallback needs (kanban card 3027671c…)"
        )

    # Find the `else`/`fi` that closes the push-conditional at its own indent.
    cond_line_start = source_text.rfind("\n", 0, cond_idx) + 1
    cond_indent = _line_indent(source_text[cond_line_start:cond_idx + len(PUSH_CONDITIONAL)])
    cursor = source_text.find("\n", cond_idx) + 1
    close_idx = -1
    for raw_line in source_text[cursor:].split("\n"):
        stripped = raw_line.strip()
        if stripped in ("else", "fi") and _line_indent(raw_line) == cond_indent:
            close_idx = source_text.find(raw_line, cursor)
            break
        cursor += len(raw_line) + 1
    if close_idx == -1:
        return False, (
            f"could not find the `else`/`fi` closing the push-conditional at "
            f"indent {cond_indent} — the if-block looks malformed"
        )
    if not (cond_idx < delete_idx < close_idx):
        return False, (
            f"branch delete ({delete_idx}) is NOT inside the push-success "
            f"branch (condition at {cond_idx}, closed at {close_idx}) — it "
            f"would fire even when the push to master is rejected, deleting "
            f"the branch the pull-request fallback needs"
        )
    return True, ""


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_branch_delete_guarded_by_push_success(source_name: str) -> None:
    """The remote-branch delete must fire only after a successful push.

    Pins the positional invariant from kanban card ``3027671c…``. Presence
    of ``git push origin --delete "$BRANCH"`` alone is not enough: the recipe
    documents a pull-request fallback for when the push to master is rejected
    (master moved / branch protection), and that fallback needs
    ``origin/$BRANCH`` to still exist. A delete placed unconditionally after
    a bare push would destroy the branch on exactly the path where it is
    still needed — turning a recoverable rejection into lost work.
    """
    source_text = SOURCES[source_name]()
    ok, reason = _branch_delete_is_guarded_by_push_success(source_text)
    assert ok, f"{source_name}: branch delete not guarded by push success — {reason}"


def test_branch_delete_guard_detects_unconditional_delete() -> None:
    """Demonstrate the guard catches the naive "just append the delete" shape.

    The card's own suggested fix appended ``git push origin --delete`` directly
    after a bare ``git push origin HEAD:master``. That shape deletes the branch
    even when the push was rejected. Pin the detector with a live negative case
    so the contract is enforced, not assumed.
    """
    naive_mirror = (
        'git -C "$WT" push origin HEAD:master\n'
        'git push origin --delete "$BRANCH" || echo "WARN: al weg?"\n'
        'git worktree remove --force "$WT"\n'
    )
    ok, reason = _branch_delete_is_guarded_by_push_success(naive_mirror)
    assert not ok, (
        f"guard did NOT flag an unconditional delete after a bare push; "
        f"reason={reason!r}. The positional invariant has rotted."
    )
    assert "if-condition" in reason, (
        f"unexpected failure reason: {reason!r}; expected a missing "
        f"if-condition diagnosis."
    )


def test_carve_out_in_recovery_path_detects_unconditional_exit() -> None:
    """Demonstrate the positional invariant catches the original bug shape.

    Builds a fake mirror that reproduces the ``efb8187b…`` regression:
    the merge handler exits unconditionally, and the carve-out lives as
    prose *below* the ``exit 1``. The structural check must flag this.
    If this test ever stops failing-on-purpose, the positional invariant
    has rotted (e.g. the indent comparison shrank to no-op) — pin it down
    with a live negative case so the contract is enforced, not assumed.
    """
    fake_mirror_with_bug = (
        f'git worktree add --detach "$WT" origin/master\n'
        f'if ! git -C "$WT" {MERGE_HANDLER} "$BRANCH" -m "Merge $BRANCH"; then\n'
        f'  echo "ERROR: merge conflict" >&2\n'
        f'  exit 1\n'
        f'fi\n'
        f'# NOTE: if conflict is exactly the two generated files, the\n'
        f'# recovery is: `git checkout --theirs -- README.md llms.txt`,\n'
        f'# regenerate, strict-check, add -A, commit --no-edit.\n'
        f'{CARVE_OUT_OPEN}\n'
        f'"$WT"/scripts/generate-doc-index.py\n'
        f'{CARVE_OUT_CLOSE}\n'
        f'git -C "$WT" {PUSH_HANDLER}\n'
        f'git worktree remove --force "$WT"\n'
    )
    ok, reason = _carve_out_is_in_recovery_path(fake_mirror_with_bug)
    assert not ok, (
        f"structural check did NOT flag the efb8187b…-shaped fake mirror; "
        f"reason={reason!r}. The positional invariant has rotted — a "
        f"future regression would no longer trip CI."
    )
    assert "fi" in reason and "prematurely" in reason, (
        f"unexpected failure reason for the fake mirror: {reason!r}; "
        f"expected a premature-`fi` diagnosis."
    )


# Carve-out exact-equality anti-pattern (kanban card 72db7429…). The
# previous predicate `[ "$CONFLICTED" != "$EXPECTED" ]` rejected any
# subset of {README.md, llms.txt} — a conflict in only one file was
# treated as a handwritten conflict and forced a report_impediment
# fallback. The new predicate must allow non-empty subsets, so the
# exact-equality form must be GONE from every mirror. Pinned as an
# explicit anti-presence so a future editor who reverts the carve-out
# to the exact-equality shape trips this test on the next CI run.
EXACT_EQUALITY_ANTI_PATTERN = '[ "$CONFLICTED" != "$EXPECTED" ]'


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_carve_out_does_not_use_strict_exact_equality(source_name: str) -> None:
    """The carve-out must NOT use `[ "$CONFLICTED" != "$EXPECTED" ]`.

    Pins kanban card ``72db7429…``: the previous predicate was a strict
    set-equality check, which rejected legitimate subsets of
    ``{docs/cockpit/README.md, docs/cockpit/llms.txt}`` (a conflict in
    only one of the two) and fell through to ``report_impediment`` even
    though the regenerate step would have reconciled from the merged
    frontmatter. The new predicate accepts any non-empty subset and adds
    a marker-boundary check for ``README.md`` conflicts.
    """
    source_text = SOURCES[source_name]()
    assert EXACT_EQUALITY_ANTI_PATTERN not in source_text, (
        f"{source_name}: carve-out still uses strict exact-equality "
        f"({EXACT_EQUALITY_ANTI_PATTERN!r}). A conflict in only "
        f"README.md or only llms.txt would fall through to "
        f"report_impediment (kanban card 72db7429…)."
    )


# README marker-boundary positional check (kanban card 72db7429…). The
# README conflict check must run BEFORE the `checkout --theirs` that
# clears the merge markers — once cleared, the hunks are gone and the
# boundary check has nothing to look at. So in the source text:
# BEGIN_MARKER_REF must appear AFTER the conflict enumeration
# (`diff --name-only --diff-filter=U`) and BEFORE the carve-out OPEN
# (`checkout --theirs`). Complement the structural (substring) invariants
# above with a positional one, mirroring the carve-out recovery-path
# check from kanban-kaart ``efb8187b…``.
BEGIN_MARKER_REF = "BEGIN GENERATED DOC INDEX"
CONFLICT_ENUM = 'git -C "$WT" diff --name-only --diff-filter=U'


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_readme_marker_check_sits_between_enumeration_and_open(
    source_name: str,
) -> None:
    """The README marker check must lie between conflict enumeration and carve-out open.

    Pins the *position* of the marker-boundary check, complementing the
    *presence* invariant above. The carve-out order is:

        CONFLICTED=$(git -C "$WT" diff --name-only --diff-filter=U ...)
        <non-empty subset check>
        <README marker-boundary check (BEGIN/END line numbers)>
        git -C "$WT" checkout --theirs -- ...

    If the marker check moves after ``checkout --theirs``, the merge
    markers are already cleared and the check silently no-ops —
    `report_impediment` never fires for a README conflict outside the
    generated block. This test catches that order regression.
    """
    source_text = SOURCES[source_name]()
    enum_idx = source_text.find(CONFLICT_ENUM)
    marker_idx = source_text.find(BEGIN_MARKER_REF)
    open_idx = source_text.find(CARVE_OUT_OPEN)
    assert enum_idx != -1, (
        f"{source_name}: missing conflict enumeration command "
        f"({CONFLICT_ENUM!r})"
    )
    assert marker_idx != -1, (
        f"{source_name}: missing README marker reference "
        f"({BEGIN_MARKER_REF!r}) — subset check is wired but the "
        f"marker-boundary check is not"
    )
    assert open_idx != -1, (
        f"{source_name}: missing carve-out open command "
        f"({CARVE_OUT_OPEN!r})"
    )
    assert enum_idx < marker_idx < open_idx, (
        f"{source_name}: README marker check is NOT positioned between "
        f"conflict enumeration and carve-out open. "
        f"enum_idx={enum_idx}, marker_idx={marker_idx}, open_idx={open_idx}. "
        f"If the marker check sits after `checkout --theirs`, the merge "
        f"markers are already cleared and the check silently no-ops "
        f"(kanban card 72db7429…)."
    )
