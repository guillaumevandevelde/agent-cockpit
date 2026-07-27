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
    # Immutable commit-hash input. Replaces the old "diff tegen origin/master"
    # anchor (kaart 491c7ba1): a reviewer in a fresh isolated worktree based on
    # origin/master has HEAD == origin/master and an empty worktree diff, so
    # a generic "diff tegen origin/master" turns into a false-negative verdict.
    # The SHA is what links the reviewer to the actually-committed
    # implementation, not their own HEAD. Anchor is the literal token
    # "commit-hash" so a future revert to a HEAD-anchored diff fails loudly.
    (
        "input: explicit commit hash",
        "commit-hash",
    ),
    # The diff command must be SHA-anchored (origin/master..<hash>), not a
    # raw HEAD-anchored diff. Same rationale: reviewer's HEAD == origin/master
    # in the isolated worktree.
    (
        "input: SHA-anchored diff command against origin/master",
        "origin/master..",
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
    # Carve-out: a docs-only / analyst leaf-spike deliverable has no
    # feature-diff to review, so it skips the subagent-FCR and does an
    # inline compliance-check instead (card cf3f456c). Keeping this in the
    # invariants list means the carve-out itself can't silently drift out
    # of one mirror.
    (
        "carve-out: docs-only / analyst leaf-spike skips subagent-FCR",
        "Carve-out — docs-only / analyst leaf-spike",
    ),
    # Subagent-type preference (kaart 27e743eb49d2480d8734f6ee3c484490):
    # the default ``Agent`` fallback (``general-purpose``) trips "Prompt is
    # too long" on long card descriptions. Both mirrors must steer the agent
    # toward ``Explore`` first; if this anchor disappears from one mirror,
    # the engineer persona and the dispatch prompt disagree about *which*
    # subagent-type the FCR should use.
    (
        "FCR subagent-type preference: Explore default",
        "Voorkeur-volgorde van subagent-type",
    ),
    # Reproducibility contract (kaart 491c7ba1). Without an explicit
    # ``git show`` invocation the reviewer has no way to reconstruct a
    # committed diff in a fresh origin/master-based worktree — this is
    # exactly the falsified-verdict trap the old prompt produced.
    (
        "reproducibility command: git show against the commit-hash",
        "git show",
    ),
    # Actionable refusal contract: when the commit-hash is missing or
    # does not resolve, the reviewer must STOP with an actionable error
    # and NOT produce a content verdict. Without this guard, a reviewer
    # in a broken setup would silently return OK-or-not-OK against an
    # empty diff, falsely clearing or falsely blocking the card.
    (
        "missing/unresolvable commit-hash → actionable refusal, no verdict",
        "unresolvable commit-hash",
    ),
    # Reachability check for auto-recovery in shell error-handlers
    # (kanban-kaart `c06a3a2a…` / `efb8187b…`): the FCR reviewer must
    # verify that an auto-recovery path sits in the *executable* branch
    # of the same `if`-block as the error-detection, not as prose or a
    # comment that follows an `exit 1`. Without this anchor, the recipe
    # convention ("auto-recovery hoort in dezelfde if-tak als de
    # fout-detectie" — `docs/cockpit/recipe-writing-conventions.md`)
    # cannot be enforced by review; the original `efb8187b…` carve-out
    # was unreachable precisely because the prose recovery was placed
    # below the `exit 1` handler. Both mirrors must carry this check.
    (
        "auto-recovery reachability check (executable path, not post-exit prose)",
        "recovery in het uitvoeringspad",
    ),
    # Scope-authority ordering (kaart b0a1e1110bb24e2fbdc7f95b1cb43420): the
    # FCR reviewer must reconstruct the implementation from `git show
    # <COMMIT_HASH> --stat` FIRST as the authoritative scope, with the
    # origin/master diff as Step 2 context only. Without this anchor, a
    # future revert to a HEAD-anchored "files I changed" framing relapses
    # into the false-positive scope-creep blocker that the original card
    # observed (a parallel chore-PR that merged between commit and ship).
    # Both mirrors must name the explicit `--stat` invocation so the
    # ordering is unambiguous, not buried in prose.
    (
        "scope-authority ordering: git show <HASH> --stat is the authoritative scope",
        "git show <COMMIT_HASH> --stat",
    ),
    # Out-of-scope refusal clause (kaart b0a1e1110bb24e2fbdc7f95b1cb43420):
    # the reviewer must refuse a content verdict when its blocker-set
    # contains files not in `git show <COMMIT_HASH> --stat`, returning a
    # machine-checkable `out-of-scope review: <files> not in <COMMIT_HASH>`
    # string instead. Without this anchor, a reviewer falling back to a
    # HEAD vs. origin/master diff silently scopes-creep-blocks the commit
    # for files that landed on origin/master during the session.
    (
        "out-of-scope refusal clause: blockers outside --stat → actionable refusal",
        "out-of-scope review",
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
    reads e.g. ``.claude/agents/engineer.md missing input: SHA-anchored
    diff command against origin/master: 'origin/master..'``.

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


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_fcr_show_stat_appears_before_diff_step(source_name: str) -> None:
    """Step 1 (``git show <COMMIT_HASH> --stat``) must appear BEFORE Step 2
    (``git diff origin/master..<COMMIT_HASH>``) in the rendered prompt.

    Guards against the regression that triggered kaart
    b0a1e1110bb24e2fbdc7f95b1cb43420: a reviewer that reads the most
    visible diff (``HEAD..origin/master``) before the commit-anchored
    ``git show --stat`` treats every file in that diff as "files I
    changed", including files that landed on origin/master during the
    session via a parallel chore-PR merge. The acceptance criterion for
    that card is an *explicit* ordered recipe — Step 1 = scope,
    Step 2 = context — so a future editor that swaps the order, merges
    the two bullets into one, or drops the ``--stat`` flag must trip
    this guard rather than silently regress the safety net.

    Checks both mirrors (engineer.md raw + dispatch-rendered string) so
    the drift guard catches the same regression in either location.
    """
    text = SOURCES[source_name]()
    show_stat_idx = text.find("git show <COMMIT_HASH> --stat")
    diff_idx = text.find("git diff origin/master..<COMMIT_HASH>")
    assert show_stat_idx != -1, (
        f"{source_name}: missing Step 1 anchor 'git show <COMMIT_HASH> "
        f"--stat' — the new explicit ordering makes the `--stat` flag "
        f"part of the scope-authority contract, not optional prose."
    )
    assert diff_idx != -1, (
        f"{source_name}: missing Step 2 anchor 'git diff "
        f"origin/master..<COMMIT_HASH>'"
    )
    assert show_stat_idx < diff_idx, (
        f"{source_name}: Step 1 (git show --stat) must appear BEFORE "
        f"Step 2 (git diff origin/master..<COMMIT_HASH>) so the "
        f"reviewer reconstructs scope first, context second. Found "
        f"`--stat` at offset {show_stat_idx}, diff at offset {diff_idx}."
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
    # Kaart 491c7ba1: the "diff against origin/master" input was reframed
    # into two tighter contract inputs — explicit commit-hash + the
    # SHA-anchored ``git diff origin/master..<hash>`` command. Both must
    # be required so a future shrink of the invariants list still trips
    # this coverage sanity.
    required_inputs = {
        "input: card title",
        "input: card description",
        "input: explicit commit hash",
        "input: SHA-anchored diff command against origin/master",
    }
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


# Anchor for the "where does the FCR prompt actually live" callout. Kept at
# module scope so a future editor renaming the callout heading trips one
# obvious knob rather than hunting through assertion bodies.
FCR_MIRROR_CALLOUT_ANCHOR = "Canonieke FCR-mirror"


def _normalized(text: str) -> str:
    """Collapse all runs of whitespace so assertions survive re-wrapping.

    The callout is prose inside a numbered Markdown list, so its line
    breaks and indentation shift whenever someone re-flows the paragraph.
    Matching on normalized text keeps the guard about *content* rather
    than about where the wrap points happen to fall.
    """
    return " ".join(text.split())


def test_engineer_md_names_itself_as_canonical_fcr_mirror() -> None:
    """engineer.md §6 must name itself the canonical FCR mirror and must
    explicitly rule out ``CLAUDE.md``.

    Kaart ``549ef4d6…``: a prior self-improve card (``b0a1e111…``)
    described the FCR mirror as living in "``CLAUDE.md`` Session-end
    workflow section". It does not — ``CLAUDE.md`` carries no FCR prompt
    at all, so a reader grepping it finds nothing and has to fall back to
    a repo-wide ``grep -rln "Feature-Compliance-Review (FCR) als pre-Done"``
    to locate the real mirror. Every future FCR card copying that pattern
    inherits the same dead pointer.

    The fix is a callout in the persona itself, so the next card author
    reads the right paths instead of copying the wrong one. This guard
    pins all four of its load-bearing claims: the callout exists, it sits
    inside §6 where a reader of the FCR step will actually see it, it
    names both real mirrors plus the drift guard, and it says in so many
    words that ``CLAUDE.md`` is not one of them.
    """
    body = _engineer_md_body()
    normalized = _normalized(body)

    callout_idx = body.find(FCR_MIRROR_CALLOUT_ANCHOR)
    assert callout_idx != -1, (
        f"engineer.md: missing the canonical-FCR-mirror callout "
        f"({FCR_MIRROR_CALLOUT_ANCHOR!r}). Without it, the next author of "
        f"an FCR self-improve card has no in-persona pointer to the real "
        f"mirrors and will copy the stale 'CLAUDE.md' framing."
    )

    # The callout is only useful where the FCR step is read — between the
    # FCR block and the Werkomgeving section (same window the sibling
    # test pins for the FCR step itself).
    fcr_idx = body.lower().find("feature-compliance")
    werkomgeving_idx = body.find("Werkomgeving in worktree")
    assert fcr_idx < callout_idx < werkomgeving_idx, (
        f"engineer.md: the canonical-FCR-mirror callout must live inside "
        f"§6 alongside the FCR step (between offsets {fcr_idx} and "
        f"{werkomgeving_idx}); found it at {callout_idx}."
    )

    # Both real mirrors + the drift guard must be named by path, so the
    # callout doubles as the grep recipe the card author needs.
    for label, path in (
        ("the persona mirror itself", ".claude/agents/engineer.md"),
        ("the dispatcher mirror", "_build_ship_instructions"),
        ("the dispatcher module", "backend/app/kanban/dispatch.py"),
        ("the drift guard", "backend/tests/test_fcr_prompt_drift.py"),
    ):
        assert path in normalized, (
            f"engineer.md: canonical-FCR-mirror callout does not name "
            f"{label} ({path!r}); the callout is the grep recipe for the "
            f"next FCR card author, so every real location must appear."
        )

    # The negative half of the claim — this is the actual bug being fixed,
    # so it gets an exact-sentence assertion rather than a loose keyword.
    assert (
        "`CLAUDE.md` is een losse repo-oriëntatiedoc en draagt de "
        "FCR-prompt **niet**" in normalized
    ), (
        "engineer.md: the canonical-FCR-mirror callout must state "
        "explicitly that CLAUDE.md does NOT carry the FCR prompt. That "
        "negative claim is the whole point of kaart 549ef4d6… — a callout "
        "that merely lists the right paths still lets the next author "
        "assume CLAUDE.md is a third mirror."
    )


def test_claude_md_does_not_carry_the_fcr_prompt() -> None:
    """Premise guard for the callout above: ``CLAUDE.md`` really must not
    contain the FCR prompt.

    The callout asserts a *negative* fact about a file it does not own.
    If someone later inlines the FCR prompt into ``CLAUDE.md`` — a third
    mirror, un-guarded by ``SOURCES`` — the callout silently becomes a
    lie and the drift detector would not notice, because ``CLAUDE.md``
    is not in the registry. Failing here forces that editor to either
    back the change out or add ``CLAUDE.md`` to ``SOURCES`` and update
    the callout in the same commit.
    """
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Feature-Compliance-Review" not in claude_md, (
        "CLAUDE.md now contains the FCR prompt, contradicting the "
        "canonical-mirror callout in .claude/agents/engineer.md §6. "
        "Either revert that addition, or add CLAUDE.md to SOURCES here "
        "and update the callout to name three mirrors."
    )