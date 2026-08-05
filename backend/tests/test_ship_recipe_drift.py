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
    # checked out in the main worktree" error from git-worktree-add. Its
    # PATH is pinned by the two `SHIP_TMP`/`WT` invariants further down:
    # persistent (not `mktemp -d` — the Bash harness reaps /tmp mid-ship,
    # kanban card 01aa1ef5…) and outside `.git/worktrees/` (that is git's
    # own admin dir for the worktree being created; overlapping them
    # corrupted the index and broke every ship, kanban card 7dd8a3dd…).
    # Slot name stays `ship-merge-$$` (PID-unique), not a fixed name — a
    # fixed slot collides across concurrent dispatched sessions (kanban
    # card c23dfe46).
    #
    # `"$BASE"`, a variable — NOT a hardcoded `master` and NOT a hardcoded
    # `origin/master`. Both hardcodings are wrong, in opposite directions,
    # and the recipe now picks between them at runtime:
    #
    #   * hardcoded `origin/master` (kanban card 5e83b6e0… fourth
    #     iteration): on a multi-session box local master routinely has N
    #     commits ahead of origin/master (other agents' pushes in flight).
    #     Basing the merge there strands those commits on every ship.
    #   * hardcoded `master` (this card): paired with a guard that blocked
    #     on *any* `behind > 0`, it made the ship self-blocking. The
    #     post-push `pull --ff-only` skips with a WARN whenever the main
    #     checkout is dirty, so local master falls behind and stays behind,
    #     and every subsequent ship tripped the guard — with `ahead=0`,
    #     i.e. with nothing to strand.
    #
    # The base-selection block below resolves `$BASE` to local `master`
    # when local is at/ahead of origin, and to `origin/master` in the
    # behind-only (`ahead=0`) case. Keep the variable; a future edit that
    # hardcodes either side reintroduces one of the two bugs.
    (
        "detached-worktree merge target",
        'git worktree add --detach "$WT" "$BASE"',
    ),
    # Base selection, first arm (kanban card 5e83b6e0…).
    # `--is-ancestor origin/master master` returns 0 when origin/master is
    # reachable from local master — i.e. local is at, or ahead of, origin,
    # and a push from local master would be a fast-forward. That arm keeps
    # the local-master base so concurrent unpushed commits ride along.
    (
        "base selection: local at/ahead of origin",
        "git merge-base --is-ancestor origin/master master",
    ),
    # Base selection, second arm (this card). The SYMMETRIC ancestry test:
    # local master reachable from origin/master = behind-only, `ahead=0`.
    # Nothing to strand, so the ship proceeds on `origin/master` instead of
    # blocking. Losing this arm collapses the recipe back to "block on any
    # behind", which is the self-reinforcing shape this card removed — so
    # it is pinned as its own invariant, not folded into the arm above.
    (
        "base selection: behind-only, nothing to strand",
        "git merge-base --is-ancestor master origin/master",
    ),
    # Main-checkout path discovery (kanban card 5e83b6e0…). The post-push
    # sync runs against the canonical checkout where `master` is actually
    # checked out — the throwaway detached worktree cannot update `master`
    # itself. The dispatch mirror inlines `project_path` via `shlex.quote`
    # (kaart a962b209… blocker C: single-quote-wrapped paths survive spaces,
    # `$`, `, embedded quotes, and backslashes); the skill mirror is
    # self-discovering via `git rev-parse --git-common-dir` because the
    # skill must work without the dispatch prompt. Both forms end up
    # identical on the meta project; the safety property is that the
    # referenced path is the main checkout, not the worktree.
    (
        "main-checkout path discovery",
        "MAIN_CHECKOUT",
    ),
    # Post-push main-checkout sync (kanban card 5e83b6e0…). After a
    # successful push the local master ref in the main checkout must move
    # too, or the divergence guard above trips on every subsequent ship on
    # this multi-session box — even though the divergence is fully explained
    # by our own push. `git pull --ff-only origin master` in one step
    # fast-forwards the local master ref AND updates the index + working
    # tree in the main checkout (so the dev-stack keeps running against the
    # latest tree). The `--ff-only` refusal on a dirty working tree is the
    # right default — we don't want to clobber in-flight edits.
    (
        "post-push main-checkout fast-forward",
        "git -C \"$MAIN_CHECKOUT\" pull --ff-only origin master",
    ),
    # Skip-with-WARN on a failed pull (kanban card 5e83b6e0… round 3
    # decision). The absent `update-ref` fallback is pinned by a dedicated
    # absence test below — `test_post_push_sync_must_not_use_update_ref_fallback`
    # — rather than as a presence-assertion in the drift invariant list
    # (this list is presence-only by design). Keeping the explanation here
    # so a future editor of this file knows where the absence-pin lives.
    # Post-`worktree add` 0-byte-index guard. A crashed/aborted predecessor
    # in the shared gitdir can leave the freshly-created slot's `index` at
    # 0 bytes; `git worktree add` reports success anyway, and the very next
    # `merge` dies with `fatal: …/index: index file smaller than expected`
    # while `git worktree remove --force` refuses with `is not a working
    # tree` — orphaning the slot (kanban card 608e2a27…). The recovery is
    # `git read-tree HEAD` against the slot: the checkout already holds the
    # right tree, only the index needs rebuilding.
    (
        "slot gitdir resolution",
        'WT_GITDIR=$(git -C "$WT" rev-parse --absolute-git-dir)',
    ),
    (
        "0-byte index detection",
        'if [ ! -s "$WT_GITDIR/index" ]',
    ),
    (
        "0-byte index recovery",
        'git -C "$WT" read-tree HEAD',
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
    # must run *only* when the push to master succeeded, and
    # `test_branch_delete_guarded_by_remote_ref_exists` for the further
    # invariant that the delete is gated on the remote ref actually
    # existing.
    (
        "remote branch cleanup",
        'git push origin --delete "$BRANCH"',
    ),
    # Remote-ref pre-check for the delete (kanban card 552036fa…). The
    # delete itself prints two `error:` lines when the remote ref doesn't
    # exist (`error: unable to delete '<branch>': remote ref does not
    # exist` + `error: failed to push some refs to '<url>'`), and that
    # spurious error output reliably trips "the ship failed" reading on
    # the tail of the log — even though the push-to-master already
    # succeeded by then and the recipe was fail-open. Pinning the
    # substring alone is insufficient: see
    # `test_branch_delete_guarded_by_remote_ref_exists` for the
    # structural invariant that the pre-check is *positionally* the
    # gate of the delete (delete inside the `then`-branch of an `if` whose
    # condition is this exact `git ls-remote` line).
    (
        "remote-ref pre-check for delete",
        'git ls-remote --exit-code --heads origin "$BRANCH"',
    ),
    # Pre-flight: the detached worktree only sees COMMITTED state, so an
    # uncommitted/untracked branch would merge as a silent no-op. This guard
    # catches that before `git worktree add` runs.
    #
    # The trailing `--` is part of the invariant (kanban card 7dd8a3dd…):
    # without it a repo-root file named `HEAD` makes the argument ambiguous
    # and git exits 128 (`fatal: ambiguous argument 'HEAD': both revision and
    # filename`). Under `if ! ...` a 128 reads as "tree is dirty", so EVERY
    # ship aborted with a bogus uncommitted-changes error before reaching the
    # merge. Ten such files really were tracked on master; see
    # `scripts/check-worktree-admin-files.sh` for the standing gate.
    (
        "pre-flight uncommitted-changes guard",
        "git diff --quiet HEAD --",
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
    # Throwaway worktree location (kanban card 7dd8a3dd…). It must be
    # persistent (not `/tmp`, which the Bash harness can reap mid-ship —
    # card 01aa1ef5…) AND outside `.git/worktrees/`, because that directory
    # is where git keeps its own admin files (HEAD, index, MERGE_*) for the
    # very worktree being created. Overlapping the checkout with the admin
    # dir is what let `add -A` commit ten of git's files to the repo root
    # and broke every subsequent ship. `$HOME/.cache/` satisfies both.
    (
        "ship worktree outside .git and outside /tmp",
        'SHIP_TMP="${HOME}/.cache/cockpit-ship"',
    ),
    (
        # Branch-unique ship worktree path (NOT PID-derived). The Bash tool
        # spawns a fresh shell per call, so `$$` (PID) drifts between calls —
        # a recipe split across calls lost the worktree path with
        # `fatal: cannot change to …: No such file or directory` on every
        # `git -C "$WT" …` line (the bug motivating this card). A slot
        # derived from `$BRANCH` is stable within a single ship session
        # because `$BRANCH` doesn't change between calls, and still gives
        # unique slots across ships of different branches. Same-branch
        # concurrent ships serialize via the `git worktree remove --force`
        # at end of each ship, so the cross-session collision window is
        # essentially zero. `${BRANCH//\//-}` replaces `/` with `-` so the
        # branch name is a single path component (e.g. `feature/foo` →
        # `feature-foo`).
        "branch-unique ship worktree path (stable across Bash calls)",
        'WT="$SHIP_TMP/ship-merge-${BRANCH//\\//-}"',
    ),
    # The carve-out stages by explicit path, never `add -A` (kanban card
    # 7dd8a3dd…). A blind `add -A` stages everything under the worktree
    # root; the carve-out is only entitled to commit the two files it just
    # regenerated, so it should only be able to stage those.
    (
        "carve-out stages by explicit path",
        'git -C "$WT" add -- docs/cockpit/README.md docs/cockpit/llms.txt',
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


def _engineer_md_worktree_pattern() -> str:
    """Read the engineer persona prompt — third mirror of the recipe shape.

    ``.claude/agents/engineer.md`` carries a paragraph teaching the engineer
    persona where to put scratch worktrees. Historically that paragraph named
    ``WT="$(git rev-parse --git-common-dir)/worktrees/ship-merge-$$"`` — the
    exact pattern that caused card 7dd8a3dd…'s incident: a checkout on top
    of git's live admin dir. After the fix it should reference the
    ``$HOME/.cache/cockpit-ship/ship-merge-$$`` pattern instead. Like the
    skill, we match the full file (not just the paragraph) so the test is
    resilient to prose renames; only the load-bearing substrings matter.
    """
    return (REPO_ROOT / ".claude/agents/engineer.md").read_text(encoding="utf-8")


# Source registry: name -> callable that yields the source text. Using a dict
# so the parametrised test can iterate sources symmetrically and the failure
# message reads "SOURCE_NAME missing LABEL: 'command'", which is exactly what
# the next editor needs to know.
#
# CLAUDE.md is deliberately NOT in this registry: it no longer carries its
# own copy of the recipe (see module docstring), so it structurally cannot
# contain these bash-command invariants. It is still guarded, just by
# test_claude_md_points_at_the_two_mirrors below.
#
# .claude/agents/engineer.md is a PARTIAL mirror: it carries only the
# scratch-worktree placement as a teaching example, not the full ship recipe.
# Adding it to SOURCES would fail almost every CORE_RECIPE_INVARIANT. Instead,
# test_engineer_md_does_not_teach_the_broken_worktree_pattern below pins just
# the one shape engineer.md is responsible for.
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
    assert "git diff --quiet HEAD --" in commands, (
        "invariants list lost the pre-flight 'git diff --quiet HEAD --' "
        "guard (note the trailing `--`: without it a repo-root file named "
        "`HEAD` makes the argument ambiguous and git exits 128, which reads "
        "as a dirty tree and aborts every ship — kanban card 7dd8a3dd…)"
    )
    # Ship-worktree placement + explicit staging (kanban card 7dd8a3dd…).
    # Both are load-bearing against the same incident: an overlapping
    # checkout/admin dir plus a blind `add -A` committed ten of git's own
    # per-worktree admin files to the repo root and broke every ship.
    assert any("cockpit-ship" in c for c in commands), (
        "invariants list lost the ship-worktree location (must be outside "
        "both /tmp and .git/worktrees/)"
    )
    assert any(
        c == 'git -C "$WT" add -- docs/cockpit/README.md docs/cockpit/llms.txt'
        for c in commands
    ), (
        "invariants list lost the explicit-path carve-out staging command"
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
    # Remote-ref pre-check for the delete (kanban card 552036fa…). Without
    # it the delete against a never-pushed branch prints `error:` lines that
    # read like a failed ship — the WARN `||` fallback kept it fail-open
    # but the spurious error tripped "the ship failed" reading on the tail.
    # Substring alone is insufficient: see
    # `test_branch_delete_guarded_by_remote_ref_exists` for the structural
    # invariant that the pre-check is *positionally* the gate of the delete.
    assert any(
        'git ls-remote --exit-code --heads origin "$BRANCH"' in c
        for c in commands
    ), (
        "invariants list lost the remote-ref pre-check for delete (kanban "
        "card 552036fa…)"
    )
    # 0-byte-index guard (kanban card 608e2a27…). Without it, a slot whose
    # index was truncated by an aborted predecessor kills the merge with
    # `index file smaller than expected` and needs a manual
    # `git read-tree HEAD` rescue.
    assert any("read-tree HEAD" in c for c in commands), (
        "invariants list lost the 0-byte-index recovery command"
    )
    assert any('[ ! -s "$WT_GITDIR/index" ]' in c for c in commands), (
        "invariants list lost the 0-byte-index detection guard"
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
    'if ! git diff --quiet HEAD -- || [ -n "$(git status --porcelain | grep -v \'^??\')" ]; then'
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

    # The untracked advisory block itself must NOT be fatal. Slice from the
    # `if [ -n "$UNTRACKED" ]` open to the matching `fi` close and assert
    # no `exit 1` lives inside. (Previously this test counted `exit 1` in
    # the whole pre-flight region as a proxy, but kanban card 5e83b6e0…
    # added the divergence guard between the blocking pre-flight and
    # `git worktree add`, which legitimately carries its own `exit 1` —
    # the count proxy can't tell the difference between the divergence
    # guard's `exit 1` and a regressed advisory-fatal shape.)
    #
    # Walk the line stream and match the FIRST `fi` at the same indentation
    # level as the `if [ -n "$UNTRACKED" ]` opener. dispatch.py indents
    # the closing `fi` with 3 spaces (matching the opener); SKILL.md
    # indents it with 2 — the historical whitespace differs across
    # mirrors, so the bookkeeping has to be indentation-agnostic.
    untracked_open = source_text.index('if [ -n "$UNTRACKED" ]')
    line_start = source_text.rfind("\n", 0, untracked_open) + 1
    opener_indent = len(source_text[line_start:untracked_open]) - len(
        source_text[line_start:untracked_open].lstrip()
    )
    untracked_close = untracked_open + len('if [ -n "$UNTRACKED" ]')
    for line in source_text[untracked_close:].splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped == "fi\n" or stripped == "fi":
            indent = len(line) - len(stripped)
            if indent == opener_indent:
                untracked_close = source_text.index(line, untracked_close) + len(line)
                break
    untracked_block = source_text[untracked_open:untracked_close]
    assert "exit 1" not in untracked_block, (
        f"{source_name}: untracked-files advisory contains an `exit 1` — "
        f"the advisory must be non-fatal (kanban card c28e576d…). Found: "
        f"{untracked_block!r}"
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


# Remote-branch cleanup guards the delete itself on the remote ref actually
# existing (kanban card 552036fa…). Without the pre-check, a never-pushed
# branch yields two `error:` lines from git that read like a failed ship —
# the push-to-master already succeeded, the WARN `||` fallback kept the
# ship fail-open, but the spurious error output reliably trips "the ship
# failed" reading on the tail of the log. The fix wraps the delete in an
# `if git ls-remote --exit-code --heads origin "$BRANCH" …; then` guard:
# when the remote ref doesn't exist the recipe prints a quiet `INFO:` line
# and skips the delete instead of fighting git. Position matters as much as
# presence — see the helper below for the structural pin.
LS_REMOTE_GUARD = 'git ls-remote --exit-code --heads origin "$BRANCH"'


def _branch_delete_is_guarded_by_remote_ref_exists(
    source_text: str,
) -> tuple[bool, str]:
    """Return ``(ok, reason)``: whether the remote-branch delete is wrapped
    inside the ``then``-branch of an ``if git ls-remote --exit-code --heads
    origin "$BRANCH"`` pre-check.

    Pins the structural invariant from kanban card ``552036fa…``: an
    unconditional ``git push origin --delete "$BRANCH"`` against a
    never-pushed branch prints two ``error:`` lines that read like a failed
    ship, even though the push-to-master itself succeeded. The recipe was
    fail-open (``|| echo "WARN: …"``) but the spurious error output reliably
    trips "the ship failed" reading on the tail. The fix wraps the delete
    in a pre-check that exits 0 only when the remote ref actually exists;
    if it does not, the recipe prints a quiet ``INFO:`` line and skips the
    delete — no spurious error output, no WARN noise.

    Checks, in order:

    1. The ``git ls-remote --exit-code --heads origin "$BRANCH"`` pre-check
       is present.
    2. The literal ``git push origin --delete "$BRANCH"`` appears *after*
       the pre-check (the guard must run first).
    3. The delete sits between the pre-check line and the closing ``fi`` at
       the pre-check's indent — i.e. inside the ``then``-branch, not after
       the ``fi`` (unreachable) or in the ``else``-branch (which would only
       fire when the ref doesn't exist — the exact case we want to skip).

    Same positional style as ``_branch_delete_is_guarded_by_push_success``:
    the substring invariant in ``CORE_RECIPE_INVARIANTS`` pins *that* the
    pre-check exists, this one pins *when* it gates the delete.
    """
    guard_idx = source_text.find(LS_REMOTE_GUARD)
    delete_idx = source_text.find(BRANCH_DELETE)
    if guard_idx == -1:
        return False, (
            f"missing the remote-ref pre-check ({LS_REMOTE_GUARD!r}) — the "
            f"delete would fire unconditionally and print `error:` lines on "
            f"a never-pushed branch (kanban card 552036fa…)"
        )
    if delete_idx == -1:
        return False, f"missing the delete command ({BRANCH_DELETE!r})"
    if delete_idx < guard_idx:
        return False, (
            f"branch delete ({delete_idx}) appears BEFORE the ls-remote "
            f"pre-check ({guard_idx}) — the guard runs after the action, "
            f"so it can't gate it"
        )

    # Find the line containing the pre-check, then walk forward to find the
    # closing `fi` at the same indent. The delete must sit between them —
    # i.e. inside the `then`-branch, not in the `else`-branch (which would
    # only fire when the ref doesn't exist) and not after the block.
    guard_line_start = source_text.rfind("\n", 0, guard_idx) + 1
    guard_indent = _line_indent(source_text[guard_line_start:guard_idx])
    cursor = source_text.find("\n", guard_idx) + 1
    close_idx = -1
    for raw_line in source_text[cursor:].split("\n"):
        stripped = raw_line.strip()
        if stripped == "fi" and _line_indent(raw_line) == guard_indent:
            close_idx = source_text.find(raw_line, cursor)
            break
        cursor += len(raw_line) + 1
    if close_idx == -1:
        return False, (
            f"could not find the `fi` closing the ls-remote conditional at "
            f"indent {guard_indent} — the if-block looks malformed"
        )
    if not (guard_idx < delete_idx < close_idx):
        return False, (
            f"branch delete ({delete_idx}) is NOT inside the ls-remote "
            f"`if`/`then`/`fi` block (guard at {guard_idx}, closed at "
            f"{close_idx}) — the delete would fire when the remote ref "
            f"doesn't exist, printing the `error:` lines this guard is "
            f"meant to suppress (kanban card 552036fa…)"
        )
    return True, ""


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_branch_delete_guarded_by_remote_ref_exists(source_name: str) -> None:
    """The remote-branch delete must only fire when the remote ref exists.

    Pins the structural invariant from kanban card ``552036fa…``: a direct-mode
    ship of a never-pushed branch ends with two ``error:`` lines that read
    like a failed ship (``error: unable to delete '<branch>': remote ref
    does not exist`` + ``error: failed to push some refs to '<url>'``), even
    though the push-to-master itself succeeded. The recipe was fail-open
    (``|| echo "WARN: …"``) but the spurious error output reliably tripped
    "the ship failed" reading on the tail. The fix wraps the delete in an
    ``if git ls-remote --exit-code --heads origin "$BRANCH"`` pre-check that
    exits 0 only when the remote ref actually exists; if it does not, the
    recipe prints a quiet ``INFO:`` line and skips the delete — no spurious
    error output, no WARN noise.

    Complements the parametrised substring-presence test: presence says
    "the pre-check is *somewhere* in the mirror"; this test says "the
    pre-check is the gate of the delete".
    """
    source_text = SOURCES[source_name]()
    ok, reason = _branch_delete_is_guarded_by_remote_ref_exists(source_text)
    assert ok, (
        f"{source_name}: branch delete not guarded by remote-ref check — {reason}"
    )


def test_branch_delete_remote_ref_guard_detects_unguarded_delete() -> None:
    """Live negative case: the unguarded recipe shape must fail the new
    positional invariant.

    Reproduces the exact pre-fix recipe (delete after a successful push, no
    remote-ref pre-check). The structural check must flag this so a future
    revert to the unguarded shape trips CI, not just the parametrised
    substring-presence check.
    """
    unguarded_mirror = (
        'if git -C "$WT" push origin HEAD:master; then\n'
        '  git push origin --delete "$BRANCH" || echo "WARN: al weg?"\n'
        'else\n'
        '  echo "WARN: push naar master afgewezen" >&2\n'
        'fi\n'
    )
    ok, reason = _branch_delete_is_guarded_by_remote_ref_exists(unguarded_mirror)
    assert not ok, (
        f"guard did NOT flag an unguarded delete; reason={reason!r}. "
        f"The positional invariant has rotted."
    )
    assert "ls-remote" in reason, (
        f"unexpected failure reason: {reason!r}; expected a missing "
        f"ls-remote-pre-check diagnosis."
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


# ---------------------------------------------------------------------------
# Negative pins for kanban card 7dd8a3dd… — the "no card can ship at all"
# incident. Ten files whose names are exactly git's per-worktree admin
# filenames (AUTO_MERGE, HEAD, MERGE_HEAD, MERGE_MODE, MERGE_MSG, ORIG_HEAD,
# commondir, gitdir, index, index.lock) were committed to the repo root by a
# single ship that went through the conflict carve-out. Two recipe properties
# combined to allow it, and both are pinned below as *absence* assertions —
# the presence-based invariants above cannot express "must NOT contain X".


# Negative pins combine badly with prose: both mirrors *explain* the banned
# shapes in comments (that is the point of the comments — the next editor
# needs to know why `add -A` is forbidden). A naive `X not in source_text`
# would therefore flag the explanation itself. Restrict the absence checks to
# the executable lines: strip each line and drop the ones that start with `#`.
# Fenced-code and prompt indentation are handled by the strip.
def _executable_lines(source_text: str) -> str:
    return "\n".join(
        line
        for raw in source_text.splitlines()
        if not (line := raw.strip()).startswith("#")
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_ship_worktree_is_not_nested_under_git_worktrees(source_name: str) -> None:
    """The throwaway checkout must not live inside `.git/worktrees/`.

    `.git/worktrees/<name>/` is where git stores that worktree's own admin
    files. Pointing `git worktree add` at the same path overlaps the checkout
    with the admin dir, so the carve-out's staging step saw git's HEAD/index/
    MERGE_* as ordinary project files. Once committed, every later
    `git worktree add` checked the tracked copies out over git's live admin
    files and produced `fatal: .../index: index file smaller than expected` —
    after which the conflict carve-out read an EMPTY conflict set, failed its
    predicate, and reported an impediment with `conflicted: ` (blank).

    Pinned as an absence assertion: the old shape derived the path from
    `git rev-parse --git-common-dir`, so its reappearance anywhere in the
    `WT=` assignment is the regression.
    """
    source_text = _executable_lines(SOURCES[source_name]())
    assert '--git-common-dir)/worktrees/' not in source_text, (
        f"{source_name}: ship worktree is nested under `.git/worktrees/` "
        f"again. That path is git's own admin directory for the worktree "
        f"being created — the checkout overlaps it and corrupts the index "
        f"(kanban card 7dd8a3dd…). Use a persistent location outside every "
        f"gitdir, e.g. `${{HOME}}/.cache/cockpit-ship`."
    )
    # And the replacement must not swing back to the /tmp shape that
    # `--git-common-dir` was introduced to fix (kanban card 01aa1ef5…).
    assert 'WT="$(mktemp' not in source_text and "WT=$(mktemp" not in source_text, (
        f"{source_name}: ship worktree moved back under `mktemp -d`. The "
        f"Bash harness can reap /tmp between calls, losing the merge commit "
        f"(kanban card 01aa1ef5…)."
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_ship_worktree_path_is_branch_derived_not_pid(source_name: str) -> None:
    """The ship-worktree slot MUST be derived from $BRANCH, NOT from $$.

    Pins the fix for kanban card c23dfe46-revisit — the cross-call path
    instability: ``$$`` is the calling shell's PID, and the Bash tool spawns
    a fresh shell per call (no state carries between calls). A recipe that
    references ``WT=\"$SHIP_TMP/ship-merge-$$\"`` therefore computed a
    DIFFERENT worktree path on each Bash call, and every ``git -C \"$WT\" …``
    after the first died with ``fatal: cannot change to …: No such file or
    directory``. Recovered by reusing the actually-existing worktree from
    the first call — but that recovery is brittle (the first call has to
    succeed before the second can find anything) and silent from CI's
    perspective.

    A slot derived from ``${BRANCH//\\//-}`` is stable within a single ship
    session because ``$BRANCH`` does not change between calls. Across
    sessions of different branches the slots are naturally unique; same-
    branch concurrent ships serialize via the ``git worktree remove --force``
    at end of each ship, so the cross-session collision window is
    essentially zero (the dispatcher enforces one session per card claim).

    Pinned as an absence assertion on EXECUTABLE lines so a future comment
    that explains the old PID shape (in the spirit of this test's own
    rationale paragraph) doesn't false-positive. The `_executable_lines`
    filter strips `#`-prefixed comment lines — the same filter the existing
    ``test_ship_worktree_is_not_nested_under_git_worktrees`` and
    ``test_carve_out_never_stages_everything`` rely on.
    """
    source_text = _executable_lines(SOURCES[source_name]())
    assert 'ship-merge-$$' not in source_text, (
        f"{source_name}: ship-worktree slot is PID-derived "
        f"(`ship-merge-$$`) in executable code. The Bash tool respawns a "
        f"fresh shell per call, so `$$` drifts between calls and a recipe "
        f"split across calls loses the worktree path "
        f"(`fatal: cannot change to ship-merge-<pid>: No such file or "
        f"directory`). Use the branch-derived form (see the "
        f"`branch-unique ship worktree path` invariant above) so the slot "
        f"survives arbitrary splits of the recipe across Bash calls."
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_carve_out_never_stages_everything(source_name: str) -> None:
    """The carve-out must stage by explicit path, never `add -A`.

    `git -C "$WT" add -A` stages every file under the worktree root. That is
    the exact contamination route from kanban card 7dd8a3dd…: with the
    worktree nested under `.git/worktrees/`, "every file under the worktree
    root" included git's own admin files, and one conflicted ship committed
    all ten.

    Relocating the worktree (test above) removes that specific exposure, but
    an explicit path list closes the whole class — the carve-out is only ever
    entitled to commit the two files it just regenerated, so a future nesting
    mistake can no longer turn into a repo-breaking commit.
    """
    source_text = _executable_lines(SOURCES[source_name]())
    assert 'git -C "$WT" add -A' not in source_text, (
        f"{source_name}: carve-out stages with `add -A` again. It must name "
        f"the files it regenerated: "
        f"`git -C \"$WT\" add -- docs/cockpit/README.md docs/cockpit/llms.txt` "
        f"(kanban card 7dd8a3dd…)."
    )


def test_negative_pins_would_flag_the_pre_fix_recipe() -> None:
    """Live negative case: the pre-fix recipe must trip both pins.

    Without this, a future edit could soften either assertion (e.g. narrow
    the substring until it no longer matches anything) and the pins would
    pass vacuously on every mirror. Replays both checks against a fake mirror
    carrying the exact shapes that shipped the incident.
    """
    pre_fix_mirror = (
        'WT="$(git rev-parse --git-common-dir)/worktrees/ship-merge-$$"\n'
        'git worktree add --detach "$WT" origin/master\n'
        '"$WT"/scripts/generate-doc-index.py\n'
        'git -C "$WT" add -A\n'
        'git -C "$WT" commit --no-edit\n'
    )
    # Run through the SAME filter the live pins use. This is the part that
    # would rot silently: `_executable_lines` drops comment lines so the
    # mirrors can *explain* the banned shapes, and an over-eager filter (one
    # that stripped fenced-code indentation as comments, say) would make both
    # pins pass vacuously on every mirror. Asserting through the helper keeps
    # the filter honest.
    executable = _executable_lines(pre_fix_mirror)
    assert '--git-common-dir)/worktrees/' in executable, (
        "the nesting pin's substring no longer matches the pre-fix shape "
        "after _executable_lines() — the regression pin has rotted and "
        "would pass vacuously."
    )
    assert 'git -C "$WT" add -A' in executable, (
        "the `add -A` pin's substring no longer matches the pre-fix shape "
        "after _executable_lines() — the regression pin has rotted and "
        "would pass vacuously."
    )
    # And the filter must actually remove commentary, otherwise the mirrors'
    # own explanations would trip the live pins (they did, before this).
    commented = _executable_lines(
        '# note: never use `git -C "$WT" add -A` here\n'
        'git -C "$WT" add -- docs/cockpit/README.md docs/cockpit/llms.txt\n'
    )
    assert 'add -A' not in commented, (
        "_executable_lines() no longer strips comment lines — the mirrors' "
        "explanatory comments would false-positive the negative pins."
    )
    assert 'add -- docs/cockpit/README.md' in commented, (
        "_executable_lines() stripped an executable line — the negative "
        "pins would pass vacuously."
    )


def test_repo_has_no_tracked_worktree_admin_files() -> None:
    """The ten admin files must never be tracked again.

    Belt-and-braces alongside `.gitignore` (which `git add -f` bypasses) and
    `scripts/check-worktree-admin-files.sh` (advisory unless `--strict`).
    While any of these is tracked, NO card can ship: `git worktree add`
    corrupts the new worktree's index, and a tracked `HEAD` makes
    `git diff --quiet HEAD` exit 128 with an ambiguity fatal.
    """
    admin_files = [
        "AUTO_MERGE", "HEAD", "MERGE_HEAD", "MERGE_MODE", "MERGE_MSG",
        "ORIG_HEAD", "commondir", "gitdir", "index", "index.lock",
    ]
    tracked = [f for f in admin_files if (REPO_ROOT / f).exists()]
    assert not tracked, (
        f"git per-worktree admin files present in the repo root: {tracked}. "
        f"While tracked these break every ship (kanban card 7dd8a3dd…). "
        f"Remove with `git rm -f` (plain `rm` is deny-listed in this repo)."
    )


def test_engineer_md_does_not_teach_the_broken_worktree_pattern() -> None:
    r"""engineer.md is a PARTIAL mirror of the recipe — pin the one shape it owns.

    The engineer persona prompt carries a paragraph teaching sessions WHERE
    to put scratch worktrees. Historically that paragraph pointed at the
    broken ``WT="$(git rev-parse --git-common-dir)/worktrees/ship-merge-$$"``
    pattern — the exact path whose overlap with git's admin dir caused the
    card-7dd8a3dd… incident. After the c23dfe46-revisit fix (this card) it
    points at ``$HOME/.cache/cockpit-ship/ship-merge-${BRANCH//\//-}``
    instead — branch-derived for cross-call stability (PID drifts between
    Bash tool calls, the very bug motivating this card).

    engineer.md is NOT in SOURCES (it carries only the worktree-placement
    teaching, not the full ship recipe, so almost every CORE_RECIPE_INVARIANT
    would falsely fail). This test pins just the one shape engineer.md is
    responsible for: positive (the new pattern appears) and negative (the
    old full bash-assignment is gone).

    The negative check targets the full ``WT="$(git rev-parse --git-common-dir)/worktrees/ship-merge-$$"``
    bash assignment — the exact teaching shape the original paragraph used.
    A bare mention of the path as prose (e.g. an explanation of why we no
    longer use it) won't trip it, but a re-introduction of the teaching
    paragraph would. Unlike SKILL.md / dispatch.py, engineer.md is prose
    without `#` comment blocks, so we don't filter via _executable_lines.
    """
    text = _engineer_md_worktree_pattern()

    # Positive: the new pattern is the example the persona teaches.
    assert "cockpit-ship/ship-merge-${BRANCH" in text, (
        "engineer.md no longer teaches the new scratch-worktree pattern "
        "`$HOME/.cache/cockpit-ship/ship-merge-${BRANCH//\\//-}`. Restore "
        "the paragraph in the Bash-cwd section so engineers keep placing "
        "scratch worktrees outside every gitdir AND keep the branch-derived "
        "slot name (PID drifts between Bash tool calls — kanban card "
        "c23dfe46-revisit, the very bug this card fixes)."
    )

    # Negative: the original teaching paragraph (full bash assignment) is
    # gone. A bare path mention in prose is fine; this matches only the
    # exact variable-assignment shape.
    assert 'WT="$(git rev-parse --git-common-dir)/worktrees/ship-merge-$$"' not in text, (
        "engineer.md teaches the broken worktree shape as a positive "
        "example again (`WT=\"$(git rev-parse --git-common-dir)/worktrees/ship-merge-$$\"`). "
        "That path overlaps git's live admin dir; checkout there breaks "
        "the index, and a tracked `HEAD` makes `git diff --quiet HEAD` exit "
        "128 (kanban card 7dd8a3dd…). The teaching paragraph must point at "
        "`$HOME/.cache/cockpit-ship/ship-merge-${BRANCH//\\//-}` instead."
    )


def test_recipe_writing_conventions_doc_does_not_show_add_A_as_good() -> None:
    """recipe-writing-conventions.md carries a fenced bash example claiming
    to be "extracted uit `.claude/skills/git-ship/SKILL.md` §4a".

    That doc-as-mirror must keep in sync with the canonical mirrors: an
    editor who fixes the `add -A` bug in SKILL.md but misses this doc has
    just shipped a doc that contradicts its own source citation. The
    counter-example block at line ~138 deliberately shows the OLD broken
    pattern (it's the "wat er fout ging" lesson for kaart efb8187b…) and
    sits inside a `# NOTE:` comment, so `_executable_lines` correctly
    strips it; only the GOED example's `add -A` would survive the filter.

    Same scope as `test_carve_out_never_stages_everything` but targeted at
    this single doc rather than parametrised over SOURCES: this doc is a
    *teaching* mirror, not a *recipe* mirror, so its presence-based
    invariants from SOURCES don't apply.
    """
    doc_text = (REPO_ROOT / "docs/cockpit/recipe-writing-conventions.md").read_text(
        encoding="utf-8"
    )
    executable = _executable_lines(doc_text)
    assert 'git -C "$WT" add -A' not in executable, (
        "docs/cockpit/recipe-writing-conventions.md still teaches "
        "`git -C \"$WT\" add -A` as the GOED carve-out pattern. SKILL.md "
        "§4a replaced it with `git -C \"$WT\" add -- docs/cockpit/README.md "
        "docs/cockpit/llms.txt` (kanban card 7dd8a3dd…) — the fenced bash "
        "example in this doc, which claims to be extracted from §4a, must "
        "match. The counter-example `# NOTE:` comment at ~line 138 is fine "
        "(it shows the OLD broken pattern under an `exit 1` and is filtered "
        "out by `_executable_lines`); only the GOED example matters."
    )


# 0-byte-index guard positional markers (kanban card 608e2a27…). The guard
# only helps if it runs *between* `git worktree add` (which is what leaves —
# or fails to repair — the truncated index) and the `merge --no-ff` that dies
# on it. And, per `docs/cockpit/recipe-writing-conventions.md`, the recovery
# must sit in the *executable* path of the detection `if`, not as prose after
# an `exit 1`: that is the exact `efb8187b…`/`c06a3a2a…` failure shape, one
# level down.
#
# Runtime-selected base (kanban card 5e83b6e0… → made ahead-aware by this
# card): a hardcoded `origin/master` stranded concurrent-session local
# commits on every ship, and a hardcoded `master` made the ship block on
# `behind > 0` even when `ahead=0` (nothing to strand). The base is now
# `$BASE`, resolved by the two-arm ancestry check — paired with the
# divergence guard + post-push main-checkout sync invariants below.
WORKTREE_ADD = 'git worktree add --detach "$WT" "$BASE"'
INDEX_GUARD_GITDIR = 'WT_GITDIR=$(git -C "$WT" rev-parse --absolute-git-dir)'
INDEX_GUARD_DETECT = 'if [ ! -s "$WT_GITDIR/index" ]'
INDEX_GUARD_RECOVER = 'git -C "$WT" read-tree HEAD'


def _index_guard_is_in_recovery_path(source_text: str) -> tuple[bool, str]:
    """Return ``(ok, reason)``: whether the 0-byte-index guard detects *and*
    recovers between ``git worktree add`` and ``merge --no-ff``.

    Checks, in order:

    1. All four markers are present (worktree-add, gitdir resolution,
       detection, recovery).
    2. Their order is add → gitdir → detect → recover → merge. A guard after
       the merge is useless (the merge already died); a recovery before the
       detection is not a guard at all.
    3. No ``exit 1`` sits between the detection and the recovery — that would
       make the recovery unreachable in exactly the scenario it exists for
       (the `efb8187b…` prose-after-exit shape).
    """
    add_idx = source_text.find(WORKTREE_ADD)
    gitdir_idx = source_text.find(INDEX_GUARD_GITDIR)
    detect_idx = source_text.find(INDEX_GUARD_DETECT)
    recover_idx = source_text.find(INDEX_GUARD_RECOVER)
    merge_idx = source_text.find(MERGE_HANDLER)
    missing = [
        name
        for name, idx in (
            ("worktree add", add_idx),
            ("slot gitdir resolution", gitdir_idx),
            ("0-byte index detection", detect_idx),
            ("0-byte index recovery", recover_idx),
            ("merge handler", merge_idx),
        )
        if idx == -1
    ]
    if missing:
        return False, f"missing marker(s): {', '.join(missing)}"
    if not (add_idx < gitdir_idx < detect_idx < recover_idx < merge_idx):
        return False, (
            f"guard is out of order — expected add({add_idx}) < "
            f"gitdir({gitdir_idx}) < detect({detect_idx}) < "
            f"recover({recover_idx}) < merge({merge_idx}). A guard that runs "
            f"after the merge cannot prevent the `index file smaller than "
            f"expected` fatal it exists for."
        )
    between = source_text[detect_idx:recover_idx]
    if "exit 1" in between:
        return False, (
            "an `exit 1` sits between the 0-byte-index detection and the "
            "`git read-tree HEAD` recovery — the recovery is unreachable in "
            "exactly the case it handles (kanban-kaart `efb8187b…` shape)."
        )
    return True, ""


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_index_guard_runs_before_the_merge(source_name: str) -> None:
    """The 0-byte-index guard must fire between `worktree add` and the merge.

    Pins kanban card ``608e2a27…``: a concurrent session that aborts mid-ship
    can leave the shared-gitdir slot's ``index`` at 0 bytes. ``git worktree
    add`` still reports success, so the corruption only surfaces on the next
    ``merge``, as ``fatal: …/index: index file smaller than expected`` —
    and ``git worktree remove --force`` then refuses with ``is not a working
    tree``, orphaning the slot. The checkout already holds the right tree, so
    ``git read-tree HEAD`` rebuilds the index from the slot's own HEAD and the
    merge proceeds. Presence alone is not enough: the guard has to run *before*
    the merge, and its recovery has to be inside the detection branch.
    """
    source_text = SOURCES[source_name]()
    ok, reason = _index_guard_is_in_recovery_path(source_text)
    assert ok, f"{source_name}: 0-byte-index guard not wired correctly — {reason}"


def test_index_guard_detects_a_mirror_without_the_guard() -> None:
    """Live negative case: the pre-fix recipe shape must fail the invariant.

    Replays the recipe exactly as it read before kanban card ``608e2a27…`` —
    ``worktree add`` immediately followed by ``merge --no-ff``, no index
    check. If this stops failing, the positional invariant has rotted.
    """
    pre_fix_mirror = (
        f'{WORKTREE_ADD}\n'
        f'if ! git -C "$WT" {MERGE_HANDLER} "$BRANCH" -m "Merge $BRANCH"; then\n'
        f'  exit 1\n'
        f'fi\n'
        f'git -C "$WT" {PUSH_HANDLER}\n'
    )
    ok, reason = _index_guard_is_in_recovery_path(pre_fix_mirror)
    assert not ok, (
        f"guard detector did NOT flag the pre-fix mirror; reason={reason!r}"
    )
    assert "missing marker" in reason, (
        f"unexpected failure reason: {reason!r}; expected a missing-marker "
        f"diagnosis."
    )


def test_index_guard_detects_recovery_after_an_exit() -> None:
    """Live negative case: recovery demoted below an `exit 1` must be flagged.

    The `efb8187b…` / `c06a3a2a…` failure shape, applied to this guard: the
    detection aborts the ship and the ``read-tree`` rescue is left as
    documentation the agent has to notice and re-run by hand — which is
    exactly the ~4-tool-call manual rescue this card removes.
    """
    prose_mirror = (
        f'{WORKTREE_ADD}\n'
        f'{INDEX_GUARD_GITDIR}\n'
        f'{INDEX_GUARD_DETECT}; then\n'
        f'  echo "ERROR: corrupt slot index" >&2\n'
        f'  exit 1\n'
        f'fi\n'
        f'# To recover by hand, run: {INDEX_GUARD_RECOVER}\n'
        f'if ! git -C "$WT" {MERGE_HANDLER} "$BRANCH" -m "Merge $BRANCH"; then\n'
        f'  exit 1\n'
        f'fi\n'
    )
    ok, reason = _index_guard_is_in_recovery_path(prose_mirror)
    assert not ok, (
        f"guard detector did NOT flag the prose-after-exit mirror; "
        f"reason={reason!r}"
    )
    assert "unreachable" in reason, (
        f"unexpected failure reason: {reason!r}; expected an unreachable-"
        f"recovery diagnosis."
    )
