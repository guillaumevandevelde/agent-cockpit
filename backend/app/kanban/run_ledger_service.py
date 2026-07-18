"""Per-card run ledger — stitches task/context/files/tests/outcome+model
from existing durable sources into a typed `RunLedger`.

No new data flow: every step reads a source that already exists for other
reasons (the card row, the dispatch-prompt builder, a `git diff` against the
card's `branch` deliverable, the `iteration-loop` skill's local progress
file, and the activity-feed comment prefixes). See
docs/cockpit/run-ledger-decision.md §5 for the full design rationale and
kanban card aa8158e3 for the acceptance criteria.

Every step is best-effort: a missing source (Backlog card with no branch
yet, a merged-and-gc'd worktree, no iteration-loop run) yields an
`available=False` step with an explanatory `note`, never a 500 — the same
contract `dispatch_usage_service.get_card_usage` already established for
`GET /cards/{cid}/usage`.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.kanban import service
from app.kanban.dispatch import (
    build_card_prompt,
    extract_impediment_answer,
    extract_revisit_question,
    get_ship_mode,
    resolve_phase,
)
from app.kanban.schemas import (
    RunLedger,
    RunLedgerContextStep,
    RunLedgerFileChange,
    RunLedgerFilesStep,
    RunLedgerOutcomeStep,
    RunLedgerTaskStep,
    RunLedgerTestsStep,
)
from app.kanban.session_cleanup import _get_project_path

logger = logging.getLogger(__name__)

_GIT_DIFF_TIMEOUT = 10.0

# Most-recent-comment-wins prefix scan for the outcome step. Reuses the
# private prefix constants `service.py` already defines for its own
# newest-first activity scans (`enrich_done_info`, `impediment_status_for_card`)
# rather than redefining them — see CLAUDE.md's test-doubles-convention note
# on patching/reading at the source of truth. `_OUTCOME_PREFIX` has no
# existing constant to reuse (mcp_server.py only inlines the literal).
_OUTCOME_PREFIX = "**Outcome:** "
_OUTCOME_SCAN_PREFIXES = (
    (_OUTCOME_PREFIX, "outcome"),
    (service._DONE_SUMMARY_PREFIX, "summary"),
    (service._RESOLUTION_ANSWER_PREFIX, "resolution"),
    (service._IMPEDIMENT_QUESTION_PREFIX, "impediment_question"),
)


def _branch_deliverable(card) -> str | None:
    return next((d.ref for d in (card.deliverables or []) if d.kind == "branch"), None)


def _pr_deliverable_ref(card) -> str | None:
    return next((d.ref for d in (card.deliverables or []) if d.kind == "pr"), None)


def _extract_outcome(activity) -> tuple[str | None, str | None]:
    """Newest-first scan for the most recent outcome-shaped comment.

    Mirrors the "most recent signal wins" idiom used by
    `service.impediment_status_for_card` / `dispatch.extract_revisit_question`.
    Returns (text, source) or (None, None) when no matching comment exists
    (e.g. a card still in Backlog/Doing).
    """
    for op in reversed(list(activity)):
        if op.op_type != "comment":
            continue
        text = op.payload.get("text") or ""
        for prefix, source in _OUTCOME_SCAN_PREFIXES:
            if text.startswith(prefix):
                return text[len(prefix):], source
    return None, None


def _extract_impediment_question(activity) -> str | None:
    for op in reversed(list(activity)):
        if op.op_type != "comment":
            continue
        text = op.payload.get("text") or ""
        if text.startswith(service._IMPEDIMENT_QUESTION_PREFIX):
            return text[len(service._IMPEDIMENT_QUESTION_PREFIX):]
    return None


async def _build_context_step(session, card, activity) -> RunLedgerContextStep:
    """Reconstruct the dispatch prompt deterministically (no persistence —
    decision doc §5 step 2). `persona` is omitted on purpose: it's a large
    static per-project file, not per-run context."""
    phase = resolve_phase(card)
    ship_mode = await get_ship_mode(session, card.project_key)
    impediment_question = _extract_impediment_question(activity)
    impediment_answer = extract_impediment_answer(activity)
    revisit_question = extract_revisit_question(activity)
    prompt = build_card_prompt(
        card, persona=None, ship_mode=ship_mode, phase=phase,
        impediment_question=impediment_question,
        impediment_answer=impediment_answer,
        revisit_question=revisit_question,
    )
    return RunLedgerContextStep(
        available=True, prompt=prompt, phase=phase, ship_mode=ship_mode,
        impediment_question=impediment_question,
        impediment_answer=impediment_answer,
        revisit_question=revisit_question,
    )


async def _run_git_diff_numstat(
    project_path: str, branch: str,
) -> tuple[list[RunLedgerFileChange], str | None]:
    """Best-effort per-file diffstat of `branch` against `origin/master`.

    Uses `--numstat` rather than the `--stat` form named in the acceptance
    criteria: numstat is the tab-separated, machine-parseable equivalent
    needed to fill the typed `RunLedgerFileChange` list, whereas `--stat`
    produces a formatted text table (truncated paths, no reliable
    delimiter) that isn't safely parseable. Same diffstat data, structured
    for the API contract.

    Returns (files, error) — error is a short human string on failure
    (missing/pruned branch ref, not a repo, timeout, …); never raises.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", project_path, "diff", "--numstat",
            f"origin/master...{branch}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_GIT_DIFF_TIMEOUT,
        )
    except FileNotFoundError:
        return [], "git binary not found"
    except TimeoutError:
        logger.warning("run-ledger: git diff timed out for %s @ %s", project_path, branch)
        return [], "git diff timed out"
    if proc.returncode != 0:
        return [], (stderr.decode(errors="replace").strip() or "git diff failed")

    files: list[RunLedgerFileChange] = []
    for line in stdout.decode(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins_raw, del_raw, path = parts
        files.append(RunLedgerFileChange(
            path=path,
            insertions=int(ins_raw) if ins_raw.isdigit() else 0,
            deletions=int(del_raw) if del_raw.isdigit() else 0,
        ))
    return files, None


async def _build_files_step(card) -> RunLedgerFilesStep:
    branch = _branch_deliverable(card)
    if branch is None:
        pr_ref = _pr_deliverable_ref(card)
        note = (
            "no branch deliverable yet" if pr_ref is None
            else f"only a pr deliverable ({pr_ref}) — no local branch ref to diff"
        )
        return RunLedgerFilesStep(available=False, note=note)

    project_path = await _get_project_path(card.project_key)
    if project_path is None:
        return RunLedgerFilesStep(available=False, branch=branch,
            note="project path not registered")

    files, error = await _run_git_diff_numstat(project_path, branch)
    if error is not None:
        return RunLedgerFilesStep(available=False, branch=branch, note=error)

    return RunLedgerFilesStep(
        available=True, branch=branch, files=files,
        files_changed=len(files),
        insertions_total=sum(f.insertions for f in files),
        deletions_total=sum(f.deletions for f in files),
    )


def _iteration_state_path(project_path: str, branch: str, card_id: str) -> Path:
    # Kanban-dispatched sessions run in `<project_path>/.claude/worktrees/<branch>`
    # (dispatch._resume_target_from_cwd), and the iteration-loop skill writes
    # its progress file to `.claude/state/iteration-<card-id>.txt` relative to
    # that worktree — see .claude/skills/iteration-loop/SKILL.md. Routinely
    # gone post-merge: worktree-gc removes the whole worktree once the branch
    # is merged and the card is Done, since the file is gitignored.
    return Path(project_path) / ".claude" / "worktrees" / branch / ".claude" / "state" / f"iteration-{card_id}.txt"


def _read_iteration_lines(project_path: str, branch: str, card_id: str) -> list[str]:
    path = _iteration_state_path(project_path, branch, card_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()]


async def _build_tests_step(card) -> RunLedgerTestsStep:
    ci_url = _pr_deliverable_ref(card)
    branch = _branch_deliverable(card)
    if branch is None:
        return RunLedgerTestsStep(available=False, ci_url=ci_url,
            note="no branch deliverable — no worktree to read iteration state from")

    project_path = await _get_project_path(card.project_key)
    if project_path is None:
        return RunLedgerTestsStep(available=False, ci_url=ci_url,
            note="project path not registered")

    lines = _read_iteration_lines(project_path, branch, card.id)
    if not lines:
        return RunLedgerTestsStep(available=False, ci_url=ci_url,
            note="no iteration-loop progress file found (worktree may already be gc'd)")

    last_line = lines[-1]
    status = last_line.rsplit("|", 1)[-1].strip() if "|" in last_line else None
    return RunLedgerTestsStep(
        available=True, status=status, iteration_count=len(lines),
        last_line=last_line, ci_url=ci_url,
    )


async def build_run_ledger(session, card_id: str) -> RunLedger | None:
    """Assemble the full `RunLedger` for `card_id`, or None when the card
    itself doesn't exist (caller maps that to a 404)."""
    card = await service.get_card(session, card_id)
    if card is None:
        return None

    activity = await service.card_activity(session, card_id)
    task = RunLedgerTaskStep(title=card.title, description=card.description or "")
    context = await _build_context_step(session, card, activity)
    files = await _build_files_step(card)
    tests = await _build_tests_step(card)

    outcome_text, outcome_source = _extract_outcome(activity)
    _, completed_at = await service.enrich_done_info(session, card_id)
    outcome = RunLedgerOutcomeStep(
        column=card.column, outcome_text=outcome_text,
        outcome_source=outcome_source, model=card.model,
        completed_at=completed_at,
    )

    return RunLedger(
        card_id=card.id, task=task, context=context, files=files,
        tests=tests, outcome=outcome,
        usage_url=f"/api/v1/kanban/cards/{card.id}/usage",
    )
