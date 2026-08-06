# backend/tests/test_kanban_auto_close_summary.py
"""Auto-close summary roll-up — `close_parent_if_all_children_done`.

Acceptance criteria (kaart 068845bd…):

1. The auto-close no longer posts the fixed placeholder string
   "All subtasks reached Done — auto-closed from Awaiting Subtasks.".
2. When the parent already carries its own `**Summary:** ...` comment
   (the analyst's outcome-summary posted before the card was parked in
   `Awaiting Subtasks`), those sentences remain leading in
   `enrich_done_info` — the auto-close does NOT silently supersede them.
3. The auto-close posts a roll-up per child: child title + the first
   sentence of that child's `done_summary` + the child's deliverable
   refs (kind + ref), so the parent is a self-contained record of
   *gedane stappen* en *opgeleverd werk* — also when children disappear
   via Clear-Done-sweep.
4. The roll-up is in Dutch and leads with one sentence of
   *productbetekenis* (kaart-conventie §5a).
5. **Tests must NOT pass on a tautology** — asserting only the absence
   of the old placeholder string is green for any text-shaped
   replacement. We assert *content* (parent outcome + child titles +
   deliverable refs) instead.
"""
import pytest
import pytest_asyncio

from app.kanban import service
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


# Old fixed placeholder that the bug used to post verbatim. The auto-close
# must NOT emit this string anywhere — but this assertion alone is a
# tautology (see acceptance #5); the real proof is the content
# assertions below.
_OLD_PLACEHOLDER = (
    "All subtasks reached Done — auto-closed from Awaiting Subtasks."
)


async def _seed_parent_with_prior_summary(
    s, *, parent_title: str, parent_summary: str, parent_column: str = "Awaiting Subtasks"
) -> str:
    """Create a parent in `Awaiting Subtasks` and post a `**Summary:**`
    comment on it (the analyst's outcome-summary, posted before the
    card got parked). Returns the parent id."""
    parent = await apply_operation(s, op_type="create", entity_type="card",
        project_key="A", entity_id=None,
        payload={"title": parent_title, "column": parent_column})
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="A", entity_id=parent,
        payload={"text": f"**Summary:** {parent_summary}"})
    return parent


async def _seed_done_child(
    s, parent: str, *, title: str, child_summary: str,
    deliverables: list[tuple[str, str]] | None = None,
) -> str:
    """Create a child in Done with its own `**Summary:**` comment and
    (optionally) one or more attached deliverables (kind, ref pairs).
    Returns the child id."""
    child = await apply_operation(s, op_type="create", entity_type="card",
        project_key="A", entity_id=None,
        payload={"title": title, "column": "Done", "parent_card_id": parent})
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="A", entity_id=child,
        payload={"text": f"**Summary:** {child_summary}"})
    for kind, ref in (deliverables or []):
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="A", entity_id=child,
            payload={"kind": kind, "ref": ref})
    return child


def _all_comments_text(ops) -> list[str]:
    return [o.payload.get("text") or "" for o in ops if o.op_type == "comment"]


def _summary_comments(ops) -> list[str]:
    return [
        t for t in _all_comments_text(ops) if t.startswith("**Summary:**")
    ]


def _non_summary_comments(ops) -> list[str]:
    return [
        t for t in _all_comments_text(ops)
        if not t.startswith("**Summary:**")
        # Parent's own analyst-summary was already there before the
        # auto-close fired; count it via the `_summary_comments` list,
        # not here. We want the *new* non-Summary comments.
    ]


@pytest.mark.asyncio
async def test_close_parent_rollup_preserves_parent_summary_when_present():
    """Acceptance #2: parent's prior `**Summary:**` outcome-sentence is
    preserved as the *leading sentence* of the new roll-up Summary
    comment. The previous bug replaced it with an empty placeholder;
    the new behavior embeds it verbatim so the analyst's wording stays
    on the Done-banner.
    """
    parent_outcome = (
        "De klacht over leesbaarheid is opgesplitst in twee assen: "
        "formulering en weergave."
    )
    async with KanbanSessionLocal() as s:
        parent = await _seed_parent_with_prior_summary(
            s, parent_title="analyse-leesbaarheid",
            parent_summary=parent_outcome)
        await _seed_done_child(s, parent, title="conventie §5",
            child_summary="De product-taal-regel is gecodificeerd in §5.")
        await _seed_done_child(s, parent, title="markdown-render",
            child_summary="DoneSummaryBanner rendert nu via MarkdownRenderer.")
        await s.commit()

        closed = await service.close_parent_if_all_children_done(s, parent)
        await s.commit()
        assert closed is True
        assert (await service.get_card(s, parent)).column == "Done"

        ops = await service.card_activity(s, parent)
        all_texts = _all_comments_text(ops)
        joined = "\n---\n".join(all_texts)

        # Parent's outcome sentence MUST appear verbatim in the activity
        # feed (the prior Summary comment is preserved, plus embedded
        # in the new leading roll-up Summary).
        assert parent_outcome in joined, (
            "Parent's prior outcome sentence must remain visible in the "
            "activity feed; the auto-close must not silently supersede it."
        )

        # `enrich_done_info` returns the most recent `**Summary:**` —
        # that is the roll-up, which now LEADS with the parent's prior
        # outcome sentence (verbatim). The prior Summary comment itself
        # is still in the feed but no longer the latest.
        summary_texts = _summary_comments(ops)
        assert len(summary_texts) == 2, (
            f"Expected two **Summary:** comments (parent's prior + new "
            f"roll-up); got {len(summary_texts)}: {summary_texts}"
        )
        latest_summary = summary_texts[-1]
        # Strip the `**Summary:** ` prefix and assert the parent's
        # outcome-sentence is the first line of the leading roll-up.
        leading = latest_summary[len("**Summary:** "):].splitlines()[0].strip()
        assert leading == parent_outcome, (
            f"Latest Summary's leading line must be the parent's prior "
            f"outcome-sentence verbatim; got {leading!r}, "
            f"expected {parent_outcome!r}"
        )


@pytest.mark.asyncio
async def test_close_parent_rollup_includes_child_titles_and_deliverable_refs():
    """Acceptance #3: the roll-up contains each child's title, the first
    sentence of that child's done_summary, and its deliverable refs.
    This is the non-tautological proof — old placeholder was a 12-word
    string, new roll-up must surface the children's work.
    """
    parent_outcome = "Auto-close mag de echte samenvatting niet wissen."
    child_a_summary = (
        "De echte-summary-rollup landt in close_parent_if_all_children_done "
        "met kind-uitkomsten en deliverable-refs."
    )
    child_b_summary = (
        "De conventie §5 is uitgebreid met de drie-delen-vorm uit §2.1."
    )
    async with KanbanSessionLocal() as s:
        parent = await _seed_parent_with_prior_summary(
            s, parent_title="auto-close-bug",
            parent_summary=parent_outcome)
        await _seed_done_child(
            s, parent, title="kind-A-rollup",
            child_summary=child_a_summary,
            deliverables=[
                ("branch", "k-bug-auto-clos-e098"),
                ("pr", "https://github.com/u/r/pull/42"),
            ])
        await _seed_done_child(
            s, parent, title="kind-B-conventie",
            child_summary=child_b_summary,
            deliverables=[("note", "docs/cockpit/kanban-conventions.md §5")])
        await s.commit()

        await service.close_parent_if_all_children_done(s, parent)
        await s.commit()

        ops = await service.card_activity(s, parent)
        summary_texts = _summary_comments(ops)
        # The roll-up is the LATEST `**Summary:**` comment posted by the
        # auto-close — `enrich_done_info` would surface this text.
        rollup = summary_texts[-1]
        # Strip the prefix so substring checks match against the roll-up
        # body, not the literal `**Summary:** ` label.
        rollup_body = rollup[len("**Summary:** "):]

        # Parent's outcome sentence must be in the roll-up — it is the
        # leading one sentence of productbetekenis (acceptance #4).
        assert parent_outcome in rollup_body, (
            f"Roll-up must lead with the parent's outcome-sentence; "
            f"rollup_body was:\n{rollup_body}"
        )

        # Each child title appears in the roll-up — a self-contained
        # record of *opgeleverd werk*, even after Clear-Done-sweep.
        assert "kind-A-rollup" in rollup_body, (
            f"Roll-up missing first child title; rollup:\n{rollup_body}"
        )
        assert "kind-B-conventie" in rollup_body, (
            f"Roll-up missing second child title; rollup:\n{rollup_body}"
        )

        # First sentence of each child's done_summary appears in the
        # roll-up so the reader sees the child's contribution without
        # opening the child card.
        assert child_a_summary.split(".")[0] + "." in rollup_body, (
            f"Roll-up missing first sentence of child A summary; "
            f"expected '{child_a_summary.split('.')[0]}.' in:\n{rollup_body}"
        )
        assert child_b_summary.split(".")[0] + "." in rollup_body, (
            f"Roll-up missing first sentence of child B summary; "
            f"expected '{child_b_summary.split('.')[0]}.' in:\n{rollup_body}"
        )

        # Each attached deliverable ref appears in the roll-up.
        assert "k-bug-auto-clos-e098" in rollup_body, (
            f"Roll-up missing branch deliverable ref; rollup:\n{rollup_body}"
        )
        assert "https://github.com/u/r/pull/42" in rollup_body, (
            f"Roll-up missing PR deliverable ref; rollup:\n{rollup_body}"
        )
        assert "docs/cockpit/kanban-conventions.md §5" in rollup_body, (
            f"Roll-up missing note deliverable ref; rollup:\n{rollup_body}"
        )


@pytest.mark.asyncio
async def test_close_parent_rollup_is_the_summary_when_parent_has_none():
    """Acceptance #1+#4 — when the parent has no prior `**Summary:**`
    (rare but possible — a freshly-parked parent whose analyst never
    posted an outcome before parkeren), the roll-up IS the
    `**Summary:**` comment that `enrich_done_info` will surface.
    """
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "analyse-zonder-prior-summary",
                     "column": "Awaiting Subtasks"})
        await _seed_done_child(s, parent, title="kind-X",
            child_summary="Kind X is afgerond.")
        await _seed_done_child(s, parent, title="kind-Y",
            child_summary="Kind Y is afgerond.")
        await s.commit()

        await service.close_parent_if_all_children_done(s, parent)
        await s.commit()

        ops = await service.card_activity(s, parent)
        summary_texts = _summary_comments(ops)
        # Parent has no prior Summary, so the roll-up is the only one.
        assert len(summary_texts) == 1, (
            f"Parent without prior Summary must receive exactly one "
            f"Summary comment (the roll-up); got {len(summary_texts)}"
        )
        rollup = summary_texts[0][len("**Summary:** "):]

        # Roll-up must surface both child titles (acceptance #3) so the
        # parent remains a self-contained record of opgeleverd werk.
        assert "kind-X" in rollup
        assert "kind-Y" in rollup

        # The old fixed placeholder is gone.
        assert _OLD_PLACEHOLDER not in rollup, (
            "Auto-close must not post the old fixed placeholder."
        )


@pytest.mark.asyncio
async def test_close_parent_rollup_never_posts_old_placeholder_phrase():
    """Acceptance #1 — across *all* shapes (parent has prior Summary or
    not; children have deliverables or not), the auto-close never posts
    the old fixed placeholder string. This is the positive proof that
    the machine-string is gone.
    """
    # Shape 1: parent without prior Summary, plain children.
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "shape-1", "column": "Awaiting Subtasks"})
        await _seed_done_child(s, parent, title="a", child_summary="klaar.")
        await _seed_done_child(s, parent, title="b", child_summary="klaar.")
        await s.commit()
        await service.close_parent_if_all_children_done(s, parent)
        await s.commit()
        ops = await service.card_activity(s, parent)
        for t in _all_comments_text(ops):
            assert _OLD_PLACEHOLDER not in t, (
                f"Old placeholder leaked in shape-1 comment: {t!r}"
            )

    # Shape 2: parent with prior Summary, children with deliverables.
    async with KanbanSessionLocal() as s:
        parent = await _seed_parent_with_prior_summary(
            s, parent_title="shape-2",
            parent_summary="Parent-uitkomstzin voor shape 2.")
        await _seed_done_child(s, parent, title="c", child_summary="klaar.",
            deliverables=[("branch", "x-branch")])
        await _seed_done_child(s, parent, title="d", child_summary="klaar.")
        await s.commit()
        await service.close_parent_if_all_children_done(s, parent)
        await s.commit()
        ops = await service.card_activity(s, parent)
        for t in _all_comments_text(ops):
            assert _OLD_PLACEHOLDER not in t, (
                f"Old placeholder leaked in shape-2 comment: {t!r}"
            )