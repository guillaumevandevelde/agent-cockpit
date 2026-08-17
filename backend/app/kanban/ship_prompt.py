"""Prompt construction for dispatched kanban cards.

Extracted from dispatch.py to give the prompt-building cluster (formerly
~2100 lines mixed into the dispatch coordinator) a single-responsibility
module of its own. Backward-compatible: dispatch.py re-exports the public
names so existing callers (``from app.kanban.dispatch import build_card_prompt``)
keep working unchanged.

The cluster has one job: turn a (card, persona, phase, ship-mode, ...) tuple
into the markdown prompt the agent session opens with. It does not decide
*which* card to dispatch, *which* transport to use, or *whether* to reap a
stuck session -- those live elsewhere.
"""
from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time
from pathlib import Path

from app.kanban.operations import apply_operation
from app.kanban.prompt_injectors import resolve_active_injectors
from app.kanban.service import (
    answer_gate,
    card_activity,
    enrich_done_info,
    get_card,
    latest_gate_answer,
)

logger = logging.getLogger(__name__)


# Plan-context outcomes for _plan_context_section / _resolve_plan_for_child.
PLAN_OK = "ok"
PLAN_NO_REF = "no_ref"
PLAN_DANGLING_PARENT = "dangling_parent"
PLAN_MISSING_ON_PARENT = "missing_on_parent"
PLAN_MALFORMED = "malformed"


# Constants duplicated from dispatch.py to avoid circular imports.
# These are simple string values that don't change.
CLAIMANT_PREFIX = "agent:"
CEREMONY_PROFILE_DEFAULT = "code"
_CEREMONY_PROFILES = {"code", "knowledge"}

_IMPEDIMENT_ANSWER_PREFIX = "**Resolution:** "
_REVISIT_PREFIX = "**Revisit:** "


# Two helpers live in dispatch.py: _effective_resume_cli_id (line 214) and
# _claimant_session (line 5063). We import them lazily inside the functions
# that need them so import-time stays cycle-free.

def extract_impediment_answer(activity) -> str | None:
    """Return the text of the latest `**Resolution:** <answer>` comment on a
    card's activity feed, or None when no such comment exists.

    Mirrors `extract_revisit_question`: walk the feed in reverse (newest
    first) so, when a human refines their answer across multiple resolve
    rounds, the *latest* resolution wins. Anything that's not a `comment`
    op is skipped; the prefix match is on `payload["text"]`.
    """
    for op in reversed(list(activity)):
        if op.op_type != "comment":
            continue
        text = (op.payload.get("text") or "")
        if text.startswith(_IMPEDIMENT_ANSWER_PREFIX):
            return text[len(_IMPEDIMENT_ANSWER_PREFIX):]
    return None


def compose_impediment_answer(gate_answer: str | None,
                              free_text: str | None) -> str | None:
    """Merge the two substrates a human answer can arrive on into the single
    ``impediment_answer`` string the prompt renders.

    Two independent channels exist and an operator can use both in one
    resolve round:

    * a **structured gate choice** (``report_impediment(options=[...])`` →
      ``service.answer_gate``), which lives in the ``kanban_gates`` table and
      posts *no* activity comment, and
    * **free text**, stamped as a durable ``**Resolution:** <text>`` comment by
      ``router.resolve_impediment``.

    Precedence: the gate pick is the decision (it's the structured artefact
    from the dedicated UI), the free text is supporting context — so when both
    are present they are rendered as a labelled pair rather than one
    overwriting the other (kanban card c3419f63: the choice never reached the
    resumed session at all, because the downstream reader only knew about the
    comment). Identical values collapse to one line: an operator who typed
    exactly what they clicked shouldn't see it echoed twice.

    Returns None when neither channel carries anything, which keeps
    ``build_card_prompt`` on its "please address this question" framing.
    """
    gate = (gate_answer or "").strip()
    text = (free_text or "").strip()
    if gate and text and gate != text:
        return f"Chosen option: {gate}\n\nAdditional context: {text}"
    return gate or text or None


def extract_revisit_question(activity) -> str | None:
    """Return the text of the latest `**Revisit:** <note>` comment on a
    card's activity feed, or None when no such comment exists.

    Mirrors the `**Impediment:**` extraction in router.resolve_impediment:
    walk the feed in reverse (newest first) so multiple reopen rounds
    return the *latest* rebuttal instead of the oldest one.

    `activity` is the op-log KanbanOp list returned by
    `service.card_activity`. Anything that's not an op of type `comment`
    is skipped; the prefix match is on `payload["text"]`.
    """
    for op in reversed(list(activity)):
        if op.op_type != "comment":
            continue
        text = (op.payload.get("text") or "")
        if text.startswith(_REVISIT_PREFIX):
            return text[len(_REVISIT_PREFIX):]
    return None


def _build_attachments_section(card) -> str:
    """Render the ``## Screenshots`` section listing each attachment's absolute
    on-disk path, so the spawned session can open them with its ``Read`` tool
    (Claude Code's Read renders images). Empty string when the card carries no
    attachments — every legacy card round-trips unchanged.

    Reads ``card.attachments`` defensively (``getattr``) so unit tests can pass
    a lightweight card stub without the ORM relationship.
    """
    attachments = getattr(card, "attachments", None) or []
    if not attachments:
        return ""
    lines = ["\n## Screenshots\n",
             "The human attached the following image(s) to this card. Use your "
             "`Read` tool on each absolute path to view them — they carry "
             "context for the task:\n"]
    for att in attachments:
        path = getattr(att, "storage_path", "") or ""
        if not path:
            continue
        filename = getattr(att, "filename", "") or "attachment"
        lines.append(f"- `{path}` ({filename})")
    lines.append("")
    return "\n".join(lines)


def _build_spec_doc_line(card) -> str:
    """Render a single ``**Brondoc (spec_doc):** <pad>`` line when
    ``card.meta['spec_doc']`` (SPEC_DOC_META_KEY) is a non-empty string. Empty
    string otherwise.

    The analyst-persona sets this forward *implements*-link on every child
    card that updates a ``docs/cockpit/*.md`` doc; without this rendering the
    executor's ship-stap 3 ("voeg een `✅ Geïmplementeerd (kaart <id>)`-regel
    toe aan het brondoc") is blind to which doc to update and the
    bijwerk-stap gets skipped or lands on the wrong file (kanban card
    87ced87b…). Tolerates ``card.meta is None`` / missing key / non-string
    value (defensive — analysts *should* write a string but dispatch must not
    500 on a malformed legacy row)."""
    from app.kanban.schemas import SPEC_DOC_META_KEY
    meta = getattr(card, "meta", None) or {}
    raw = meta.get(SPEC_DOC_META_KEY)
    if not isinstance(raw, str):
        return ""
    spec_doc = raw.strip()
    if not spec_doc:
        return ""
    return f"**Brondoc (spec_doc):** `{spec_doc}`\n"


def _build_prior_branch_warning(project_path: str, prior_session_name: str | None) -> str:
    """Render a warning block when a prior dispatch left unmerged commits behind.

    Closes the "re-dispatch starts cold" gap (kanban card ff2d03fce…): when
    a session is interrupted after `git commit` but before the merge, the
    reaper eventually releases the claim and the dispatcher spawns a fresh
    worktree for the same card. Without this hint, that fresh session has
    no signal that its predecessor already shipped commits that just need
    to land on master — so it redoes the work and the two diverge.

    Pure synchronous helper (uses ``subprocess.run`` against the project
    repo, NOT the worktree path — the worktree may already be GC'd by the
    time we run this check). Returns ``""`` in three cases, so callers can
    treat the empty string as the explicit "no warning" sentinel and
    prepend only when non-empty:

      - ``prior_session_name`` is falsy (no prior claim found in the op-log,
        or the card was never picked up before).
      - the prior branch doesn't exist on the remote (was force-pushed
        away, never pushed, or its session was killed before pushing).
      - the prior branch has zero commits ahead of ``origin/master``
        (already merged in a concurrent merge, or the commit was empty).
      - any subprocess / repo error — fail open, never wedge dispatch on a
        transient git hiccup.

    Acceptance criteria (from the card): the rendered block must name both
    the branch and the commit count so a re-dispatched agent can act on it
    with one `git log origin/master..<branch>` inspection rather than
    rediscovering the gap from scratch.
    """
    if not prior_session_name or not project_path:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", project_path, "log", "--oneline",
             f"origin/master..{prior_session_name}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return ""
    count = len(lines)
    return (
        f"## PRID-BRANCH-WAARSCHUWING\n"
        f"**Let op:** een eerdere sessie (`{prior_session_name}`) liet "
        f"{count} commit{'s' if count != 1 else ''} achter die nog niet "
        f"gemerged zijn. Inspecteer die branch eerst vóór je opnieuw begint "
        f"— mogelijk is het werk al af en hoef je alleen te shippen/verifiëren:\n\n"
        f"```\n"
        f"git log origin/master..{prior_session_name} --oneline\n"
        f"git diff origin/master..{prior_session_name} --stat\n"
        f"```\n"
        f"\nAls de branch al precies doet wat de kaart vraagt, ga dan direct "
        f"door naar de ship-stappen hieronder (in plaats van het werk te "
        f"herbouwen). Is de branch achterhaald of conflicterend, dan mag je "
        f"opnieuw beginnen — maar bevestig dat expliciet in een "
        f"`**Self-improve:**` comment op deze kaart.\n"
    )


async def _resolve_prior_branch_warning(
    session, *, card, project_path: str,
) -> str:
    """Build a prior-branch warning for ``card`` if a previous dispatch
    left commits behind, otherwise return ``""``.

    Glue between ``_build_prior_branch_warning`` (the pure git-aware
    renderer) and the kanban op-log (which knows whether the card was
    ever picked up before). Walks the op-log backwards, finds the latest
    ``claim`` op whose ``claimed_by`` starts with ``agent:`` and whose
    session name is NOT the current session (the new claim that the caller
    is about to commit is excluded — we want the *previous* session's
    branch, not the one the freshly-spawned session is creating right
    now), and feeds that name to the helper.

    Async + DB-bound because it queries the op-log via ``card_activity``,
    same shape as the revisit/resume resolvers above.

    Returns ``""`` (the explicit no-warning sentinel) when:
      - the card has no prior claim (first dispatch, or manual restart
        after a manual release — both common),
      - the prior branch has nothing ahead of ``origin/master`` (already
        merged, never pushed, or GC'd),
      - the op-log query fails (fail open — a transient DB hiccup must
        never wedge dispatch).

    Wired into ``_run_card`` so the warning reaches every dispatch path
    (auto-tick, manual ``dispatch_card``, ``redispatch_card``, ``dispatch_impediment_card``).
    """
    from app.kanban.service import card_activity

    try:
        activity = await card_activity(session, card.id)
    except Exception:
        logger.debug(
            "could not read op-log for prior-branch warning (card %s); skipping",
            card.id, exc_info=True,
        )
        return ""
    from app.kanban.dispatch import _claimant_session
    current_session = _claimant_session(card)
    for op in reversed(list(activity)):
        if op.op_type != "claim":
            continue
        claimed_by = (op.payload or {}).get("claimed_by") or ""
        if not claimed_by.startswith(CLAIMANT_PREFIX):
            continue
        session_name = claimed_by[len(CLAIMANT_PREFIX):]
        # Skip the brand-new claim the caller is about to commit — we want
        # the *previous* session's branch, not the new worktree that's
        # still empty.
        if session_name == current_session:
            continue
        return _build_prior_branch_warning(project_path, session_name)
    return ""


def build_card_prompt(card, *, persona: str | None, ship_mode: str,
                      phase: str = "executor",
                      impediment_question: str | None = None,
                      impediment_answer: str | None = None,
                      revisit_question: str | None = None,
                      revisit_prior_decision: dict | None = None,
                      prior_branch_warning: str | None = None,
                      project_path: str | None = None,
                      worktree_path: str | None = None,
                      prompt_injector_caveman: str = "",
                      prompt_injector_ponytail: str = "",
                      ceremony_profile: str = "code") -> str:
    # A card dispatched in the executor phase (no `analyst_agent_id`) can
    # still resolve to the analyst persona via `work_type='analysis'` or
    # `card.agent='analyst'` (the "leaf analyst spike" case — see
    # `is_analyst_leaf_spike`). analyst.md (and its ANALYST_PROMPT
    # fallback) self-scopes for this: the "Verboden" prohibitions are
    # explicitly marked as modus-1-only and the persona's own "Leaf
    # design-deliverable" section states the modus-2 contract, so no
    # dispatch-level override is needed to reconcile it with the executor
    # ship workflow injected below. See kanban card c2b478ca396a473287aa0c04a79890e2
    # for the two-modi framing and fbe7937e99484941b196bf2ebc0866f6 for the
    # removal of the (now redundant) per-dispatch override preamble.
    preamble = (persona.strip() + "\n\n") if persona else ""
    # Prompt-injectors (kaart d0446fd8…). The slices come from the
    # kanban-side resolver (``app.kanban.prompt_injectors.resolve_active_injectors``)
    # and are bound to the system-prompt layer only — kaarttekst,
    # persona-contract, ship-instructies en impediment/revisit-secties
    # blijven onaangeraakt. Empty strings when the per-lane flag or the
    # board kill-switch is off. The slices sit *between* the persona and
    # the rest of the prompt because Claude's prompt-cache key treats
    # the entire tail as cacheable input; keeping the injectors in the
    # prefix + the variability downstream means an unchanged-session
    # cache hit survives both injectors being on across calls (the
    # resolver is pure — see ``tests/test_prompt_injectors.py::test_resolver_returns_byte_stable_output_for_same_inputs``).
    if prompt_injector_caveman or prompt_injector_ponytail:
        injector_blocks: list[str] = []
        if prompt_injector_caveman:
            injector_blocks.append(prompt_injector_caveman.rstrip())
        if prompt_injector_ponytail:
            injector_blocks.append(prompt_injector_ponytail.rstrip())
        if injector_blocks:
            preamble = preamble + "\n\n---\n\n" + "\n\n---\n\n".join(injector_blocks) + "\n\n"
    impediment_section = ""
    if impediment_question:
        impediment_section = (
            "\n\n## IMPEDIMENT\n"
            "A previous agent was blocked on this card. Their question:\n"
            f"> {impediment_question}\n\n"
        )
        if impediment_answer:
            # A human answered the blocker via /resolve-impediment: a structured
            # gate choice, a `**Resolution:**` comment, or both merged by
            # `compose_impediment_answer`. Surface it as authoritative so the
            # resumed session acts on the decision instead of re-asking.
            # Blockquote every line — a merged answer is multi-line, and a bare
            # `> ` on the first line only would leave the rest reading as loose
            # prose outside the quote.
            quoted = "\n".join(
                f"> {line}" if line.strip() else ">"
                for line in str(impediment_answer).splitlines()
            )
            impediment_section += (
                "A human has since answered this — treat the answer as an "
                "authoritative decision and proceed accordingly:\n"
                f"{quoted}\n\n"
            )
        else:
            impediment_section += (
                "Please address this question or clarify what's needed "
                "before proceeding.\n"
            )

    revisit_section = ""
    if revisit_question:
        # Mirror of `impediment_section`. The prior-decision dict carries the
        # Done summary + deliverable refs so the re-picked-up session has
        # enough context to revise without re-reading every comment. When
        # None or empty, only the rebuttal is rendered — that's the safe
        # fallback for cards without the (optional) decision enrichment.
        parts = [
            "\n\n## REVISIT",
            "A previous agent completed this card and a human has reopened it "
            "with the following rebuttal. Treat this as a request to revise "
            "the prior decision, not a brand-new task.\n",
            f"> {revisit_question}\n",
        ]
        prior = revisit_prior_decision or {}
        prior_lines = []
        if prior.get("summary"):
            prior_lines.append(f"- **Previous summary:** {prior['summary'].strip()}")
        prior_deliverables = prior.get("deliverables") or []
        if prior_deliverables:
            refs = "\n".join(
                f"  - `{d.get('kind', '?')}: {d.get('ref', '?')}`"
                for d in prior_deliverables
            )
            prior_lines.append(f"- **Previous deliverables:**\n{refs}")
        if prior_lines:
            parts.append("\nFor context, the prior decision referenced:\n\n"
                         + "\n".join(prior_lines) + "\n")
        parts.append(
            "\nPlease re-read the prior decision (deliverable docs in git) "
            "and revise or uphold it with reasoning, then ship the update.\n"
        )
        revisit_section = "".join(parts)

    # Standardised session-end workflow — provider-agnostic, works with any
    # coding agent (Claude Code, OpenCode, Codex CLI, …). Executor/engineer
    # sessions run tests → ship (merge/PR) → attach the deliverable → retro →
    # move the card to Done. Analyst sessions never ship code (planning-only,
    # exits via move_parent → Done) so they get a lighter retro-then-move
    # workflow instead of the full engineer ship instructions.
    if phase == "analyst":
        ship_instructions = _build_analyst_session_end_instructions()
    elif getattr(card, "agent", None) == "reviewer":
        ship_instructions = _build_reviewer_session_end_instructions()
    elif ceremony_profile == "knowledge":
        # Knowledge profile: same machinery, lighter recipe. The branching
        # lives here so analysts and reviewers keep their existing shape;
        # knowledge is the only third lane.
        ship_instructions = _build_knowledge_ship_instructions(
            ship_mode, project_path=project_path,
        )
    else:
        ship_instructions = _build_ship_instructions(
            ship_mode, project_path=project_path,
        )
    problem_flag_instructions = _build_problem_flag_instructions()
    mcp_fallback_instructions = _build_mcp_fallback_instructions()
    worktree_safety_callout = _build_worktree_safety_callout(
        project_path=project_path, worktree_path=worktree_path,
    )
    attachments_section = _build_attachments_section(card)
    spec_doc_line = _build_spec_doc_line(card)

    return (
        f"{preamble}"
        "You are picking up a Kanban card from the Agent Cockpit board. "
        'It is already claimed by you and moved to "Doing".\n\n'
        f"Host card id: {getattr(card, 'id', '') or ''}\n"
        f"# {card.title}\n"
        f"{getattr(card, 'description', '') or ''}\n"
        f"{spec_doc_line}"
        f"{attachments_section}"
        f"{prior_branch_warning or ''}\n"
        f"{impediment_section}\n"
        f"{revisit_section}\n"
        f"Ship mode: {ship_mode}\n\n"
        "Work autonomously to completion, following your role instructions above. "
        "Use the `cockpit-kanban` MCP tools (`move_card`, `attach_deliverable`, "
        "`comment`) to update the card exactly as those instructions direct. If you are "
        "blocked, use `report_impediment` with a clear question explaining what you need."
        f"\n\n{mcp_fallback_instructions}"
        f"\n\n{problem_flag_instructions}"
        f"\n\n{worktree_safety_callout}"
        f"\n## Session-end workflow\n"
        "When your work on this card is complete, follow these steps in order:\n\n"
        f"{ship_instructions}"
    )


def _build_mcp_fallback_instructions() -> str:
    """REST fallback for when the `cockpit-kanban` MCP tools fail with JSON-RPC
    `-32602` (Invalid request parameters).

    Root cause (confirmed; see app/kanban/mcp_health.py failure-mode B): the
    agent completes its MCP `initialize` handshake, then the backend restarts
    or reconnects (dev `--reload`, a supervisor restart, a crash-restart). The
    SSE stream reconnects but the *server-side* session state was never
    re-initialized, so the fresh session answers **every** subsequent request —
    including `ping` — with a generic ``-32602``. The payload the agent sent is
    fine; retrying often clears it once the client re-initializes. A session
    that doesn't know the REST equivalents can burn several turns rediscovering
    endpoint paths, or worse strand a finished card in its dispatch column. See
    kanban card 7b1d0a91 for the full postmortem.

    Two follow-up gaps from kanban card 939a9770 (a session where -32602 was
    100% of calls, not an intermittent race, and the REST fallback carried the
    whole session) are closed here: the list endpoint's `{"items": [...]}`
    envelope is spelled out (three parse attempts failed on the assumption it
    returned a bare list), `POST /cards` is listed so filing a follow-up card
    doesn't need endpoint archaeology, and the retry advice now caps at one
    failed retry per session instead of one per call.

    Analyst-fase coverage (kanban card a254b3111a2340478da726eb8fd015b9): an
    analyst session with MCP down must still be able to finish decomposition.
    `POST /cards/{id}/plan-attachment` is the REST mirror of
    `add_plan_attachment` — wiring a `plan_ref` deliverable on every child is
    **not optional**, even when `depends_on_graph={}` (an independent child
    without a `plan_ref` is silently held by the `awaiting_plan_ref` dispatch
    gate and never runs). `POST /cards` must also expose `parent_card_id` and
    `metadata`, since the `decomposed`-gate on the parent's Done-move refuses
    the move with `no_children` when a child is missing `parent_card_id`."""
    return (
        "## If a `cockpit-kanban` MCP call fails with `-32602`\n"
        "`-32602` (Invalid request parameters) from a `mcp__cockpit-kanban__*` "
        "tool is usually an intermittent MCP handshake race, **not** a bad "
        "payload — retry the same call once. If that retry also fails, treat "
        "MCP as **down for the rest of this session**: stop retrying per call "
        "and go straight to REST for every subsequent board update (a broken "
        "session handshake fails 100% of calls, not intermittently, and the "
        "per-call retry never clears it). The REST API is at "
        "`http://localhost:8000/api/v1/kanban` (same board, same effect):\n"
        "- `POST /cards/{id}/comment` — body `{\"text\": \"…\"}`\n"
        "- `POST /cards/{id}/move` — body "
        "`{\"column\": \"…\", \"summary\": \"…\", \"outcome\": \"…\"}`. The REST "
        "move shares the gate with the MCP tool (kaart efbb82e6…): a `Done` "
        "move without `summary` is refused with 422 `summary_required`; an "
        "analysis-card `Done` move without `outcome` is refused with 422 "
        "`outcome_required`; an out-of-enum `outcome` is refused with 422 "
        "`invalid_outcome` plus the `allowed` list. `column=\"Impediment\"` "
        "is rejected with 422 (`use_report_impediment`); the Impediment route "
        "is `report_impediment`'s only, see kaart b8e3ac8b… decision A.\n"
        "- `POST /cards/{id}/deliverables` — body `{\"kind\": \"branch|pr|commit|link|note\", \"ref\": \"…\"}`\n"
        "- `GET /cards?project_key=<key>&column=<col>` — list cards. Returns "
        "`{\"items\": [...]}` — an object, **not** a bare list; index `.items` "
        "before iterating (`jq '.items[]'`)\n"
        "- `POST /cards` — create a card; body `{\"project_key\": \"…\", "
        "\"title\": \"…\", \"description\": \"…\", \"column\": \"Backlog\", "
        "\"work_type\": \"…\", \"parent_card_id\": \"…\", \"metadata\": {…}}` "
        "(`column` defaults to `Backlog`; `parent_card_id` is required when "
        "creating a child of an analyst decomposition (a child without it makes "
        "the parent's `decomposed`-Done-move fail with `no_children`); "
        "`metadata` carries analyst/parent-tag context; an unknown "
        "`project_key` is rejected with 404 `unknown_project_key` — resolve it "
        "first, don't guess)\n"
        "- `POST /cards/{id}/plan-attachment` — body "
        "`{\"plan_markdown\": \"…\", \"child_card_ids\": [\"…\"], "
        "\"depends_on_graph\": {\"<child_id>\": [\"<sibling_id>\", …]}}` "
        "(REST mirror of `add_plan_attachment`; **not optional** — every "
        "decomposition child MUST get a `plan_ref` deliverable, even when "
        "`depends_on_graph={}`. Without it the `awaiting_plan_ref` dispatch "
        "hold keeps the child silent: it looks unclaimed and unstarted but "
        "never dispatches. Returns `{\"parent_card_id\": \"…\", "
        "\"plan_deliverable_id\": \"…\", "
        "\"child_card_ids\": [\"…\"], "
        "\"plan_refs\": {\"<child_id>\": \"<plan_ref_deliverable_id>\", …}}` — "
        "the `plan_refs` map echoes the freshly wired `plan_ref` deliverable "
        "id per child, so the write is verifiable from the response itself. "
        "Treat a non-empty `plan_refs[<child_id>]` as proof the deliverable "
        "landed; no re-fetch is needed. The MCP `add_plan_attachment` tool "
        "returns the same shape)\n"
        "- `GET /project-key?project_path=<abs path>` — resolve the project "
        "key; returns `{\"project_key\": \"…\"}`\n"
        "\n"
        "## Bij een 503 met `lock_contention`\n"
        "Een kanban-schrijfactie kan vastlopen op databaselock-contentie. De "
        "REST-route antwoordt dan met 503 en "
        "`{\"detail\": {\"reason\": \"lock_contention\", "
        "\"retry_after_ms\": 500, \"attempts\": 3}}`; de MCP-tool met "
        "`{\"error\": \"lock_contention\", \"retry_after_ms\": 500, …}`. "
        "Wacht `retry_after_ms` milliseconden en doe dezelfde call opnieuw, "
        "maximaal 3 pogingen. Lukt het daarna nog niet: `report_impediment`. "
        "Doe tussendoor geen andere board-mutatie — dat vergroot de contentie. "
        "Contract: `docs/cockpit/agent-failure-response.md`.\n"
    )


def _build_problem_flag_instructions() -> str:
    """Standing reminder to file (not just mention) problems noticed outside the
    assigned card's scope. A skill at ``.claude/skills/flag-problem/SKILL.md``
    has the full dedupe/project-key procedure when the agent has filesystem
    access; this inlines the essential steps for parity with the ship
    instructions above, which work the same way for the same reason."""
    return (
        "## Noticed a problem outside this card's scope?\n"
        "If you hit a bug, a stale doc, or a workflow gap that isn't the task "
        "above, don't just mention it in chat — it vanishes when this session "
        "ends. File it: resolve this repo's real project key first — call the "
        "`resolve_project_key` MCP tool with this repo's working directory. "
        "The older `curl .../kanban/project-key` recipe is rejected by the "
        "context-mode `Bash|curl` hook on this box and silently fails "
        "(kaart `161d63b2…`); do not fall back to it. Guessing the key "
        "silently creates an invisible parallel board. "
        "Then check `list_cards` on `Backlog`/`Impediment` for an existing "
        "card describing the same root cause, and either `comment` on it "
        "with what's new or `create_card` (column `Backlog`, title "
        "`[problem] <summary>`) if none exists. See the `flag-problem` skill "
        "for the full procedure. Keep this quick — don't let it derail the "
        "card you were actually dispatched for.\n"
    )


def _build_worktree_safety_callout(
    project_path: str | None = None,
    worktree_path: str | None = None,
) -> str:
    """Top-of-prompt callout forbidding writes to the canonical checkout path.

    Background — kanban card 513e37a1a86e41db8b6af8423292f6b6: a dispatched
    analyst session edited two docs via the absolute path
    ``<project_path>/docs/cockpit/...`` instead of its worktree path.
    ``Edit`` succeeded because the committed content matched in both
    checkouts, so ``old_string`` resolved; the change landed on top of a
    concurrent session's uncommitted work in the main checkout. The persona
    doc already warns against ``cd <project_path>/...`` for shell commands
    but says nothing about Write/Edit — an agent reading the card description
    (which references ``/home/vdvgu/claude-cockpit/...`` for canonical
    filenames) easily constructs an absolute *write* path that bypasses the
    worktree.

    This callout is the in-prompt mirror of the persona-doc guidance: it
    names the safe pattern, names the forbidden one, and names the tools that
    can clobber (``Write`` / ``Edit`` / ``MultiEdit``). It is rendered above
    the ``## Session-end workflow`` heading so it lands in the agent's
    early context, not buried under later steps — same parity principle as
    the ship-instructions inline.

    ``project_path`` and ``worktree_path`` are interpolated when the
    dispatcher knows them (kanban card a962b209aea4489680c15de3562eb8bb).
    Before this card the callout hardcoded the meta project's
    ``/home/vdvgu/claude-cockpit`` and the ``<branch>`` placeholder — those
    values are *wrong* for any non-meta dispatched project and silently
    coaxed an agent on a throwaway product project into writing its
    deliverable into the meta project's tree. Pass ``None`` (the legacy
    fallback) only when the dispatcher hasn't resolved a project yet —
    kept as a default so pre-existing callers and tests keep working.

    The forbidden canonical path here is the project's *own* main checkout,
    not the meta project tree: a card dispatched against project X must
    not be allowed to write into project X's shared checkout either, for
    exactly the same concurrent-session reason.

    When ``worktree_path`` is ``None`` the callout deliberately drops the
    "spawned in a git worktree at …" framing — that framing is only true
    for the worktree transport. Resume sessions, sandcastle sessions, and
    headless sessions do NOT run in a freshly-minted host-side worktree;
    naming a fabricated path (the legacy ``<branch>`` placeholder was a
    fallback for unresolved cases but reads as a real claim to the agent)
    tells the agent a lie about its actual cwd. The forbidden canonical
    path guidance still applies — concurrent dispatched sessions on this
    project can still have uncommitted work in the main checkout.
    """
    canonical_main = project_path or "/home/vdvgu/claude-cockpit"
    # Branch into two templates: when the dispatcher knows the worktree
    # path, render the full claim ("spawned in a git worktree at …");
    # otherwise render a neutral "your shell cwd is …" framing so resume
    # / sandcastle / headless sessions don't get a false claim.
    if worktree_path:
        scope_intro = (
            f"You were spawned in a git worktree at ``{worktree_path}`` "
            "(see your shell's cwd). Your **only** writable surface is "
            "that worktree root."
        )
        right_example = (
            f"absolute ``{worktree_path}/docs/cockpit/foo.md``"
        )
    else:
        scope_intro = (
            "Your shell's cwd is your writable surface for this card — "
            "it is **not** a freshly-minted git worktree (resume / "
            "sandcastle / headless transports skip the worktree step), "
            "so write into that cwd and nowhere else."
        )
        right_example = (
            "relative ``docs/cockpit/foo.md`` from your shell's cwd"
        )

    return (
        "## Worktree scope — write only inside your worktree\n"
        f"{scope_intro} **Never** call ``Write``, ``Edit``, ``MultiEdit``, "
        f"or ``NotebookEdit`` with an absolute path that resolves to "
        f"``{canonical_main}/...`` outside your writable surface — that "
        "is the shared canonical checkout where ``master`` is checked "
        "out, and concurrent dispatched sessions may have uncommitted "
        "work there. A write to that path silently lands on top of "
        "someone else's changes (kanban card "
        "513e37a1a86e41db8b6af8423292f6b6 was a near-clobber from "
        "exactly this).\n\n"
        "Concretely:\n"
        "- **Right:** ``docs/cockpit/foo.md``, ``backend/app/x.py``, or "
        f"{right_example}.\n"
        f"- **Wrong:** ``{canonical_main}/docs/cockpit/foo.md`` — "
        "this resolves to the *main* checkout, not your writable "
        "surface, even though the file content is identical.\n\n"
        f"Same rule for shell: don't ``cd {canonical_main}/...`` "
        "and run a write from there — see the persona's *Werkomgeving in "
        "worktree* section for the broader cwd-safety rules. Read paths to "
        "the canonical checkout are fine; only writes are forbidden.\n"
    )


async def _resolve_revisit(session, card) -> tuple[str | None, dict | None]:
    """Look up the latest `**Revisit:**` rebuttal for a card (None when no
    reopen has happened) plus a small "prior decision" envelope that the
    `## REVISIT` prompt section consumes.

    Returns `(question, prior_decision_dict)` where `prior_decision_dict`
    carries the Done summary + the deliverable refs. Both are None when the
    card has no Revisit comment (the common case for non-reopened cards).

    The prior-decision envelope is intentionally a small dict (not a
    structured object) so callers don't need to import service-layer types;
    `build_card_prompt` consumes it directly.
    """
    from app.kanban import service as svc

    activity = await svc.card_activity(session, card.id)
    revisit = extract_revisit_question(activity)
    if revisit is None:
        return None, None

    done_summary, _ = await svc.enrich_done_info(session, card.id)
    # Refresh the card to pick up deliverables (the session may have stale
    # relationship state after _make_card creates the row without the
    # deliverable eager-load).
    fresh = await svc.get_card(session, card.id)
    deliverables = []
    if fresh is not None:
        for d in (fresh.deliverables or []):
            deliverables.append({"kind": d.kind, "ref": d.ref})
    return revisit, {
        "summary": done_summary or "",
        "deliverables": deliverables,
    }


async def _resolve_impediment(session, card) -> tuple[str | None, str | None]:
    """Look up the latest ``**Impediment:**`` question and the human's answer
    to it, reading **both** answer substrates: the structured gate choice
    (``kanban_gates``) and the free-text ``**Resolution:**`` comment.

    Returns ``(question, answer)``; both are None when the card has no
    impediment question (the common case — only cards that went through the
    resolve-impediment flow carry one). Mirrors ``_resolve_revisit`` in
    shape and intent: cheap (one short scan of an already-materialised op-log)
    and only contributes to the prompt when the question actually exists, so
    ordinary cards see no IMPEDIMENT section.

    Plumbed through ``dispatch_project`` (auto-tick) so a card that just
    landed in Backlog via ``dispatch_impediment_card`` (kaart af951ad70...
    — Resolve impediment moet eerst naar Backlog, niet meteen naar engineer
    kolom) still gets the human's question + authoritative decision in the
    next spawned session's prompt. Without this, dropping the card in
    Backlog would silently lose the impediment context.

    **Why the gate lookup lives here** (kaart c3419f63): the resolve endpoint
    computes the answer correctly but ``dispatch_impediment_card`` only parks
    the card on Backlog — the spawn happens a tick later and re-derives the
    context from here. Reading only the ``**Resolution:**`` comment therefore
    dropped every gate-only resolve (``service.answer_gate`` posts no comment),
    so an operator who clicked "Postgres" and typed nothing saw the resumed
    agent re-ask the settled question. The gate query is skipped entirely when
    the card has no impediment question, so an ordinary card carrying an
    ``open_gate`` answer can't grow a phantom IMPEDIMENT answer.
    """
    from app.kanban import service as svc

    activity = await svc.card_activity(session, card.id)
    # Walk newest-first; the latest Resolution wins on re-resolve (matches
    # router.resolve_impediment's priority rules). The Impediment question
    # itself doesn't change across resolve rounds, but walking in the same
    # direction keeps the two extractors symmetric.
    free_text = extract_impediment_answer(activity)
    question = None
    for op in reversed(list(activity)):
        if op.op_type != "comment":
            continue
        text = (op.payload.get("text") or "")
        if text.startswith(svc._IMPEDIMENT_QUESTION_PREFIX):
            question = text[len(svc._IMPEDIMENT_QUESTION_PREFIX):]
            break
    if question is None:
        return None, None
    gate_answer = await svc.latest_gate_answer(session, card.id)
    return question, compose_impediment_answer(gate_answer, free_text)


async def _stamp_resume_target(session, *, card, project_key: str,
                               project_path: str) -> None:
    """Best-effort resume: if the previous agent claim points at a session
    whose worktree + vendor session record still exist, persist
    `resume_session_id`/`resume_project_folder` on the card so the spawn
    below picks the resume transport.

    Used by `dispatch_project` (auto-tick) right before picking up a
    reopened card, so the agent session that revisits the decision can
    literally continue where the prior one left off. Failure is silent
    by design — analyst cards routinely GC their worktree after merging,
    so a None fallback is the expected path. The dispatcher then runs a
    fresh session; the agent rebuilds context from the `## REVISIT`
    prompt-injected material instead.

    No-op when the card has no `agent:` claim (e.g. it was never picked
    up, only commented on by hand) — there's no prior session to resume.
    """
    from app.kanban.operations import apply_operation
    from app.kanban.session_recovery import _resolve_resume_target
    from app.kanban.dispatch import _effective_resume_cli_id

    claimant = card.claimed_by or ""
    if not claimant.startswith(CLAIMANT_PREFIX):
        return
    session_name = claimant[len(CLAIMANT_PREFIX):]
    target = _resolve_resume_target(
        project_path,
        session_name,
        cli_id=_effective_resume_cli_id(card),
    )
    if target is None:
        return
    resume_session_id, resume_project_folder = target
    await apply_operation(
        session, op_type="update", entity_type="card",
        project_key=project_key, entity_id=card.id,
        payload={"resume_session_id": resume_session_id,
                 "resume_project_folder": resume_project_folder},
    )
    logger.info(
        "reopen: stamped resume target on card %s (session %s -> %s)",
        card.id, session_name, resume_session_id,
    )


# Statuses returned by `_resolve_plan_for_child`. Distinct values let
# `_plan_context_section` render an accurate diagnosis (was the parent
# deleted, or was the plan simply never written?) instead of one generic
# "kon niet worden geladen" message. See kanban card 4a03565d ("Dispatch
# PLAN CONTEXT reports 'plan-attachment kon niet worden geladen' while a
# valid plan_ref deliverable exists") for the originating complaint.
PLAN_OK = "ok"
PLAN_NO_REF = "no_plan_ref"                  # child carries no plan_ref deliverable
PLAN_DANGLING_PARENT = "dangling_parent"     # plan_ref present, parent card gone
PLAN_MISSING_ON_PARENT = "plan_missing_on_parent"  # parent alive, plan deliverable absent
PLAN_MALFORMED = "malformed_ref"             # plan_ref JSON doesn't parse or lacks required keys


def _plan_context_section(*, status: str, plan_markdown: str | None,
                          plan_deliverable_id: str | None,
                          parent_card_id: str | None,
                          card_description: str | None = None) -> str:
    """Build the PLAN CONTEXT preamble that the executor sees in its prompt.

    On success, embeds the plan markdown verbatim so the executor can follow
    the analyst's steps. On failure, renders a status-specific diagnosis and
    picks the right nudge:

    - When the card carries its own self-sufficient description (the analyst
      wrote enough context in the title/description that the work can be
      reconstructed from the source material), the placeholder tells the
      executor to **proceed using the card description**, post a
      `**Self-improve:**` note on the card, and only fall back to
      `report_impediment` if the card is genuinely un-actionable without the
      plan.
    - When the card has no description (or an empty one), the placeholder
      steers to `report_impediment` directly — without the analyst's plan
      the executor has no source of truth and would otherwise burn context
      guessing.

    Previously this helper unconditionally pushed the executor to
    `report_impediment` even for cards that were self-sufficient from their
    own source doc, which forced every decomposed-family card with a
    dangling parent into a needless blocker session.
    """
    if status == PLAN_OK:
        return (
            f"PLAN CONTEXT — read this first\n"
            f"Plan deliverable: {plan_deliverable_id}\n"
            f"Parent card: {parent_card_id}\n\n"
            f"{plan_markdown}\n\n"
            f"---\n"
            f"Bovenstaande is het plan van de analyst. Volg deze stappen, "
            f"tenzij je tijdens het werk ontdekt dat het plan niet klopt — "
            f"gebruik dan report_impediment.\n"
        )

    # Failure modes — status-specific diagnosis, so the executor (and the
    # operator reading the transcript) can tell whether the parent was
    # deleted, the plan was never written, or the ref is corrupt.
    if status == PLAN_DANGLING_PARENT:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: de parent-kaart "
            f"`{parent_card_id}` van deze kaart bestaat niet meer "
            "(verwijderd of nooit aangemaakt), waardoor het plan-attachment "
            f"(`plan_deliverable_id={plan_deliverable_id}`) niet meer "
            "bereikbaar is. Dit is meestal een gevolg van het verwijderen "
            "van de analyst-parent nadat de kind-kaarten al waren aangemaakt."
        )
    elif status == PLAN_MISSING_ON_PARENT:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: de parent-kaart "
            f"`{parent_card_id}` bestaat, maar het plan-attachment "
            f"`{plan_deliverable_id}` is daar niet (meer) op te vinden. "
            "De analyst heeft het plan dus niet (of niet meer) gekoppeld."
        )
    elif status == PLAN_MALFORMED:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: het `plan_ref`-deliverable "
            "op deze kaart is misvormd (geen parseerbare JSON, of mist "
            "`parent_card_id`/`plan_deliverable_id`). De kind-kaart verwijst "
            "dus naar een onbruikbare referentie."
        )
    elif status == PLAN_NO_REF:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: deze kind-kaart heeft "
            "geen `plan_ref`-deliverable (de analyst heeft het plan niet "
            "gekoppeld via `add_plan_attachment`)."
        )
    else:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: onbekende fout tijdens "
            f"het laden van het plan-attachment (status={status})."
        )

    # Soften the guidance: only steer to report_impediment when the card is
    # genuinely un-actionable. A non-empty description means the analyst or
    # the card author wrote enough context in the title/description to
    # reconstruct the work from the source material — that path keeps the
    # executor productive and surfaces a `**Self-improve:**` note so the
    # dispatcher can clean up the dangling ref.
    description = (card_description or "").strip()
    if description:
        guidance = (
            "\n\nDe kaartbeschrijving hierboven bevat genoeg context om deze "
            "kaart alsnog op te pakken. Ga door met de implementatie op "
            "basis van die beschrijving en post onderaan een "
            "`**Self-improve:**` comment op deze kaart zodat de dispatch-"
            "loop de dangle opruimt. ALLEEN als de kaart zonder plan echt "
            "niet uitvoerbaar is: gebruik dan "
            "`mcp__cockpit-kanban__report_impediment`."
        )
    else:
        guidance = (
            "\n\nDe kaart heeft geen beschrijving die het werk draagt, dus "
            "is het plan-attachment de enige bron van waarheid. Gebruik "
            "`mcp__cockpit-kanban__report_impediment` om dit te signaleren."
        )
    return diag + guidance + "\n"


async def _resolve_plan_for_child(session, card) -> tuple[str, str | None, str | None, str | None]:
    """Return ``(status, plan_markdown, plan_deliverable_id, parent_card_id)``
    for a child card that holds a ``plan_ref`` deliverable.

    Looks up the ``plan_ref`` deliverable on the child, parses it for the
    parent_card_id and plan_deliverable_id, fetches the parent, and pulls
    the actual plan markdown from the parent's ``plan`` deliverable. The
    status distinguishes why resolution failed so ``_plan_context_section``
    can render an accurate diagnosis instead of one generic "could not be
    loaded" message:

    - ``PLAN_OK``                       — plan found and resolved
    - ``PLAN_NO_REF``                   — child carries no ``plan_ref`` deliverable
    - ``PLAN_DANGLING_PARENT``          — parent card no longer exists (deleted, never written)
    - ``PLAN_MISSING_ON_PARENT``        — parent exists, but the referenced ``plan`` deliverable isn't on it
    - ``PLAN_MALFORMED``                — ``plan_ref`` JSON doesn't parse or lacks required keys

    Async because resolving the plan needs a DB roundtrip (parent card
    lookup) — the brief's draft had this as a sync helper, which would
    have crashed the first time an executor session was dispatched.
    """
    plan_refs = [d for d in getattr(card, "deliverables", []) or []
                 if d.kind == "plan_ref"]
    if not plan_refs:
        return (PLAN_NO_REF, None, None, None)
    # A child should have at most one plan_ref, but be defensive: pick the
    # first and treat the rest as a soft sign of corruption (we still
    # surface the resolution attempt under PLAN_OK or the appropriate
    # failure status — never silently swallow an extra plan_ref).
    d = plan_refs[0]
    try:
        ref = json.loads(d.ref)
    except (TypeError, ValueError):
        return (PLAN_MALFORMED, None, None, None)
    parent_id = ref.get("parent_card_id")
    plan_id = ref.get("plan_deliverable_id")
    if not parent_id or not plan_id:
        return (PLAN_MALFORMED, None, plan_id, parent_id)
    parent = await get_card(session, parent_id)
    if parent is None:
        return (PLAN_DANGLING_PARENT, None, plan_id, parent_id)
    for pd in parent.deliverables:
        if pd.id == plan_id and pd.kind == "plan":
            return (PLAN_OK, pd.ref, plan_id, parent_id)
    return (PLAN_MISSING_ON_PARENT, None, plan_id, parent_id)


async def _load_ceremony_profile(project_path: str | None) -> str:
    """Read the ``ceremony_profile`` column from the registry ``projects`` row.

    Cross-database lookup: dispatch lives on the kanban DB session, but the
    ``Project`` model sits in the registry DB (see ``app.database.Base`` vs
    ``app.kanban.db.Base``). We open a fresh registry session for the read —
    a single column projection, no joins — so the cost is one short async
    query per spawn. Acceptable on a per-spawn hot path; not suitable for
    per-tick. Returns ``CEREMONY_PROFILE_DEFAULT`` when the project is not
    registered, when the column value is missing/empty (pre-migration row on
    a registry that hasn't run the revision yet), or on any DB error.

    Defensive: an empty/invalid value is coerced to the default rather than
    rejecting the spawn, because rejecting would silently strand a card in
    Doing. The default value ships in the column's ``server_default`` so a
    well-migrated DB never returns empty.
    """
    if not project_path:
        return CEREMONY_PROFILE_DEFAULT
    try:
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models.database import Project

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(Project.ceremony_profile).where(
                        Project.path == project_path
                    )
                )
            ).scalar_one_or_none()
        if not row:
            return CEREMONY_PROFILE_DEFAULT
        if row not in _CEREMONY_PROFILES:
            # Unknown value — could be a typo from a manual PATCH, or a future
            # profile the running code doesn't know yet. Fall back to the
            # default; the schema layer (``CeremonyProfile`` Literal) is the
            # guard for new writes, this guard is the safety net for legacy
            # rows.
            return CEREMONY_PROFILE_DEFAULT
        return row
    except Exception:
        logger.warning(
            "Could not load ceremony_profile for %s; defaulting to %r",
            project_path,
            CEREMONY_PROFILE_DEFAULT,
            exc_info=True,
        )
        return CEREMONY_PROFILE_DEFAULT


def _build_ship_instructions(ship_mode: str, project_path: str | None = None,
                              ceremony_profile: str = CEREMONY_PROFILE_DEFAULT) -> str:
    """Build the standardised session-end workflow instructions.

    These instructions are provider-agnostic: they work the same for Claude Code,
    OpenCode, Codex CLI, or any other coding agent that spawns in a git worktree.
    A skill at ``.claude/skills/git-ship/SKILL.md`` mirrors this logic when the
    agent has filesystem access.

    The first block in the returned string is a pre-ship
    ``Feature-Compliance-Review (FCR)`` step — a subagent-call with cleared
    context that validates the implementation against the card spec BEFORE the
    numbered ship workflow runs. This mirrors engineer.md §6 (and the kanban
    decision doc ``reviewer-agent-decision.md``); the drift guard
    ``backend/tests/test_fcr_prompt_drift.py`` enforces that the prompt
    text stays identical across both mirrors (drift-val: kaart ``d9447e49``).
    """
    # Pre-ship: Feature-Compliance-Review (FCR) — reviewed by a fresh-context
    # subagent before the numbered ship workflow begins. The prompt text must
    # stay byte-identical to the engineer.md mirror; update both in lockstep.
    #
    # Note on wording: avoid the literal ``move_card`` token here — the ship
    # workflow's last step is the canonical "move card to Done" call, and
    # ``_build_ship_instructions`` ordering tests use ``index('move_card')``
    # to find that step. A mention of ``move_card`` in this pre-ship block
    # would mask the real step behind an earlier match.
    feature_compliance_review = (
        "**Pre-ship: Feature-Compliance-Review (FCR) als pre-Done subagent-call** "
        "— `/code-review` / `iteration-loop verify` lezen de oorspronkelijke "
        "kaart-spec niet; deze stap vult dat gat. **Vóór je de kaart naar Done "
        "verplaatst**, draai je een subagent-call met **cleared context** die de "
        "implementatie toetst aan de oorspronkelijke kaart-spec: kaart-titel, "
        "kaart-beschrijving, en — expliciet — de huidige commit-hash die de "
        "implementatie bevat (typisch `git rev-parse HEAD`, door jou letterlijk "
        "meegegeven in de subagent-prompt; default: voor een sessie die net een "
        "FCR-triggerende commit heeft gemaakt).\n\n"
        "   **Voorkeur-volgorde van subagent-type** — kies het type op basis van "
        "wat de FCR moet doen. De `Agent`-tool default (`general-purpose`) trekt "
        "de hele toolset mee en kan bij kaarten met een lange beschrijving "
        "(>~2k tekens) of een grote diff-context **falen op \"Prompt is too "
        "long\"**; in de praktijk kost dat 1–3 retries of de agent breekt de "
        "FCR-stap af. Gebruik daarom standaard het smallere type:\n\n"
        "   1. **`Explore`** (default) — read-only, smalle toolset, past binnen "
        "élke prompt-lengte. Voor de standaard compliance-check (diff vs. "
        "kaart-beschrijving) is dit genoeg en het is wat je in ~95% van de "
        "feature-kaarten gebruikt. Bewust gekozen na een observatie dat twee "
        "opeenvolgende `general-purpose`-FCR-calls faalden en een derde poging "
        "met `Explore` meteen slaagde.\n"
        "   2. **`Plan`** — als de FCR een ontwerp-element of refactor-impact "
        "moet beoordelen en de bredere Plan-toolset nodig is.\n"
        "   3. **`general-purpose`** — alleen wanneer de FCR-shell-uitvoering "
        "nodig heeft die `Explore`/`Plan` niet bieden (bv. een commando draaien "
        "om een deliverable te valideren). Wees je bewust van de context-cap: "
        "combineer kaart-context en diff-context liever in twee kleinere calls "
        "dan in één grote, en val terug op een smaller type zodra je merkt dat "
        "de prompt tegen de limiet aan loopt.\n\n"
        "   Voer letterlijk deze prompt uit (eerste regel: vul `<COMMIT_HASH>` "
        "in met de letterlijke SHA van de implementatie-commit — typisch `git "
        "rev-parse HEAD` direct vóór deze subagent-call; geef het commando door "
        "in plaats van de SHA als je de hash niet beschikbaar hebt):\n\n"
        "   > Je reviewt een feature-implementatie tegen zijn oorspronkelijke\n"
        "   > specificatie. Inputs: de oorspronkelijke kaart-titel, -beschrijving, en\n"
        "   > — expliciet — de huidige commit-hash die de implementatie bevat.\n"
        "   > Vraag: doet de implementatie wat er gevraagd werd?\n"
        "   >\n"
        "   > **Bron-van-waarheid: de commit-hash, niet je eigen HEAD of de\n"
        "   > werkboom-state.** Jouw sessie draait in een geïsoleerde werkboom\n"
        "   > gebaseerd op `origin/master`, waar jouw HEAD identiek is aan\n"
        "   > `origin/master`. Reconstrueer de implementatie in twee stappen —\n"
        "   > de eerste is je **authoritative scope**, de tweede alleen context:\n"
        "   >\n"
        "   > - **Step 1 — scope (authoritative):** `git show <COMMIT_HASH> --stat`.\n"
        "   >   List de files/paden die deze commit zelf heeft veranderd. **Elke\n"
        "   >   file die hier NIET in staat is geen onderdeel van jouw review** —\n"
        "   >   hoort niet in je blocker-set, niet in je OK-redenering. Dit is\n"
        "   >   je scope; alles wat hierbuiten valt is scope-creep, niet jouw zaak.\n"
        "   > - **Step 2 — context:** `git diff origin/master..<COMMIT_HASH>`.\n"
        "   >   Cumulatieve delta tegen de origin/master-baseline. **Waarschuwing:**\n"
        "   >   `origin/master` kan tijdens de sessie bewogen hebben (parallel\n"
        "   >   chore-PR die merged is tussen commit en ship). Files die hierin\n"
        "   >   verschijnen maar NIET in `git show <HASH> --stat` staan zijn op\n"
        "   >   `origin/master` geland, niet door deze commit — negeer ze als\n"
        "   >   scope, niet als review-target.\n"
        "   >\n"
        "   > Dat is de *enige* manier waarop je de implementatie in deze set-up te\n"
        "   > zien krijgt; een lege diff met non-empty requirements is per definitie\n"
        "   > een reviewer-blokkade, geen OK.\n"
        "   >\n"
        "   > **Actionable refusal — twee verschillende fouten, twee verschillende\n"
        "   > retoures.**\n"
        "   > 1. **Unresolvable commit-hash.** Als `<COMMIT_HASH>` ontbreekt in deze\n"
        "   >    prompt, niet-resolveert via `git show <COMMIT_HASH>`, of beide\n"
        "   >    diff-commando's leeg zijn waar implementatie te verwachten is: stop\n"
        "   >    dan met een **actionable foutmelding**\n"
        "   >    (`unresolvable commit-hash: <wat er ontbreekt of niet matcht>`) en\n"
        "   >    **geen content-oordeel**. Een false-OK op een onresolveerbare hash\n"
        "   >    is precies de falsified-verdict die we hiermee voorkomen.\n"
        "   > 2. **Out-of-scope review.** Als je blocker-set files noemt die NIET in\n"
        "   >    `git show <COMMIT_HASH> --stat` voorkomen, dan zit je scope\n"
        "   >    verkeerd — die files horen niet bij deze implementatie. Retourneer\n"
        "   >    **`out-of-scope review: <files> not in <COMMIT_HASH>`** in plaats\n"
        "   >    van een content-oordeel. Een false-positive scope-creep-blokkade\n"
        "   >    (files van een parallelle merge op origin/master) is precies het\n"
        "   >    false-blokkade dat we hiermee voorkomen.\n"
        "   >\n"
        "   > Specifiek:\n"
        "   > - Elke requirement/bullet uit de beschrijving is geïmplementeerd.\n"
        "   > - De API/UI matcht de specificatie (naamgeving, gedrag, edge cases).\n"
        "   > - De implementatie integreert zonder siblings te breken.\n"
        "   > - Het deliverable dat in de samenvatting geclaimd wordt, is\n"
        "   >   daadwerkelijk aanwezig.\n"
        "   > - Wanneer de fix een `whitespace-nowrap`- of `h-*`-override is op een\n"
        "   >   shared primitive (zoals de gedeelde `<Button>`-primitive in deze\n"
        "   >   codebase, waar `h-8` anders via `twMerge` blijft staan): controleer\n"
        "   >   dat de resulterende geometrie aan de eis voldoet — een\n"
        "   >   className-assertion op `whitespace-normal` / `break-words` /\n"
        "   >   `h-auto` is geen bewijs dat de layout klopt (kaart `d9abcf44…`, regression\n"
        "   >   `da7716e5…` → fix `51ae48a6`/`f7b2609b`). De canonieke evidence is\n"
        "   >   een geometrie-assertion (`scrollWidth <= clientWidth + 1` voor\n"
        "   >   geen-overflow, `offsetHeight >= 2 * lineHeight` voor daadwerkelijke\n"
        "   >   wrap), of in jsdom een stub van diezelfde properties plus een\n"
        "   >   negatieve controle die bewijst dat de test de regression zou\n"
        "   >   vangen.\n"
        "   > - Wanneer de kaart een auto-recovery in een error-handler\n"
        "   >   beschrijft: verify dat de recovery in het uitvoeringspad zit\n"
        "   >   (dezelfde `if`-blok als de fout-detectie), niet als prose of\n"
        "   >   commentaar ná een `exit 1` (kanban-kaart `efb8187b…` /\n"
        "   >   `c06a3a2a…`; conventie: `docs/cockpit/recipe-writing-conventions.md`).\n"
        "   >\n"
        "   > Output: OK om te shippen, OF een lijst met blokkerende issues met\n"
        "   > `file:line`-refs. Dit is een **feature-compliance-check**, geen\n"
        "   > code-quality-check — die is al apart gelopen via `/code-review`.\n\n"
        "   **Carve-out — docs-only / analyst leaf-spike:** De FCR is een "
        "*feature-compliance*-check op een **code-diff**. Heeft je kaart geen "
        "feature-diff om te reviewen — een analyst leaf-spike "
        "(`work_type='analysis'`, geen `analyst_agent_id`) of een docs-only "
        "deliverable waarvan het resultaat een `docs/cockpit/*.md`-analyse is, "
        "zonder API/UI en zonder siblings om te breken — dan sla je de "
        "subagent-FCR **over** (spawn dus géén review-subagent; dat respecteert "
        "ook de top-level \"spawn geen agents tenzij gevraagd\"-richtlijn) en doe "
        "je in plaats daarvan een **inline** compliance-check tegen de "
        "kaart-eisen: is de gevraagde analyse-breedte gedekt, zijn de gevraagde "
        "artefacten opgeleverd, en zijn de follow-up-kaarten aangemaakt die de "
        "kaart vroeg. Alleen een kaart met een echte code-diff draait de "
        "subagent-FCR hierboven.\n\n"
        "   **Resultaat interpreteren:** OK → ga door naar stap 1 hieronder. "
        "Blokkerende issues → fix die eerst in dezelfde sessie (geen nieuwe "
        "kaart — FCR-blokkades zijn van jou, niet van het bord), herhaal de "
        "FCR tot `OK`, en ga dan pas naar de ship-stappen.\n\n"
    )

    sync = (
        "1. **Sync** — `git fetch origin` so you are up to date with the remote.\n"
    )
    # ``project_path`` interpolation (kanban card a962b209…): the dispatched
    # project is not always the meta project — pin the ``node_modules``
    # symlink to the *dispatched* project's main checkout, not the hardcoded
    # ``/home/vdvgu/claude-cockpit`` that only held for the meta project.
    # The legacy fallback (``project_path=None``) keeps the hardcoded string
    # so pre-existing tests/observers still match.
    #
    # Bash-quoting (kaart a962b209… blocker 2): ``project_path`` can contain
    # spaces or shell metacharacters (the dispatch target may live under
    # ``/scratch/scratchpad/My Project/...``). Both the `test -d` probe and
    # the `ln -s` source must wrap the path in double quotes; an unquoted
    # path with a space silently turns `[ -d /foo bar/... ]` into a syntax
    # error and `ln -s /foo bar/...` into a symlink to ``/foo``.
    frontend_root = (
        project_path.rstrip('/') if project_path
        else "/home/vdvgu/claude-cockpit"
    )
    nm_path = f"{frontend_root}/frontend/node_modules"
    bin_path = f"{nm_path}/.bin"
    # Pre-quoted forms for the bash snippets below. ``shlex.quote`` (kaart
    # a962b209… blocker C) wraps the path in single quotes and escapes any
    # embedded single quotes — a path like ``/tmp/prod$1/claude-cockpit``
    # survives variable expansion, command substitution, and embedded
    # double quotes, where a bare double-quote wrapper would still let
    # ``$``/``\```/``"`` through to the shell. Single-quoted shell strings
    # also tolerate spaces without the awkward escape sequences a manual
    # wrapper would have to grow. The legacy fallback path contains no
    # metacharacters, so ``shlex.quote`` produces an equivalent result.
    nm_q = shlex.quote(nm_path)
    bin_q = shlex.quote(bin_path)
    # Main-checkout path for the post-push local-master sync (kanban card
    # 5e83b6e0… fourth iteration). The dispatcher inlines ``project_path`` =
    # the canonical checkout where ``master`` is checked out, pre-quoted via
    # ``shlex.quote`` (kaart a962b209… blocker C: single-quote-wrapped paths
    # survive spaces, ``$``, ``\```, embedded quotes, and backslashes; a bare
    # double-quote wrapper would still let ``$``/``\```/``"`` through).
    # Critical: the bash snippet in the rendered prompt MUST consume the
    # pre-quoted form *bare* — no surrounding double quotes —
    # ``MAIN_CHECKOUT={main_checkout_q}`` renders as
    # ``MAIN_CHECKOUT='/home/vdvgu/claude-cockpit'`` (single quotes
    # stripped by the shell parser, leaving the path as the variable's
    # value). The previous (reverted) implementation inlined the
    # pre-quoted value inside literal double quotes —
    # ``MAIN_CHECKOUT="<main-checkout>"`` rendered as
    # ``MAIN_CHECKOUT="'/home/vdvgu/claude-cockpit'"``, and the shell
    # assigned the literal string ``'/home/vdvgu/claude-cockpit'`` (with
    # attached quotes) to the variable. Every downstream
    # ``git -C "$MAIN_CHECKOUT" …`` then died with
    # ``fatal: cannot change to ``'/home/vdvgu/claude-cockpit'``: No such
    # file or directory``. The fail-open ``2>/dev/null`` on the post-push
    # sync meant the bug was silent — the push landed, the ref-update
    # fired, but the main checkout stayed on the old tree. The
    # ``test_direct_mode_main_checkout_path_is_properly_shlex_quoted`` and
    # ``test_direct_mode_post_push_sync_uses_main_checkout_var`` tests in
    # ``backend/tests/test_kanban_dispatch.py`` pin both the safe form and
    # the broken form (as absent) so a future editor can't toggle back.
    # The legacy ``project_path=None`` fallback uses the hardcoded meta
    # project root, matching the ``frontend_root`` behaviour above.
    main_checkout_q = shlex.quote(frontend_root)
    tests = (
        "2. **Run frontend checks yourself before shipping (only when the branch "
        "touches ``frontend/``)** — there is no pre-push gate; nothing blocks a "
        "red push.  First check whether this branch changed any frontend code, "
        "and run ``npm run lint && npm run build`` only if it did — a docs-/"
        "backend-only branch would otherwise pay a multi-minute ``npm ci`` + "
        "build for zero frontend coverage:\n"
        "   ```bash\n"
        "   git fetch origin -q\n"
        "   FRONTEND_TOUCHED=$( { BASE=$(git merge-base HEAD origin/master); "
        "git diff --name-only \"$BASE\" -- frontend/; "
        "git ls-files --others --exclude-standard -- frontend/; } | head -1 )\n"
        "   if [ -n \"$FRONTEND_TOUCHED\" ]; then\n"
        "     # Fresh worktrees have no node_modules (gitignored). Fast path: "
        "when ``frontend/package-lock.json`` is unchanged vs origin/master, "
        f"symlink the main checkout's already-installed ``{nm_path}`` "
        "instead of paying a multi-minute ``npm ci``. Fall back to ``npm ci`` "
        "when the lockfile diverges (frontend deps changed) or main's "
        "``node_modules`` is absent / itself missing ``.bin/`` (partial).\n"
        "     # Card 15cc257d… also handled the partial-install trap: an "
        "interrupted ``npm ci`` leaves some scoped dirs but no ``.bin/``, which "
        "makes ``npm run lint`` die with ``eslint: not found`` and blocks a "
        "plain symlink. Move the partial aside (``mv``, not ``rm`` — ``rm`` is "
        "deny-listed) before bootstrapping.\n"
        "     # Card 9b7c2a98… (revisit of 4279448c) also handles a subtler "
        "form of corruption: ``.bin/`` is present (so the partial-install "
        "trap above doesn't fire) but a *deeper* transitive dep — e.g. "
        "``acorn``, imported by ``espree``/``eslint`` — is missing, so "
        "``npm run lint`` dies with ``Cannot find module 'acorn'``. The "
        "presence check is necessary but not sufficient; probe the symlinked "
        "install with a quick ``require`` of eslint's main module (forces "
        "Node to resolve the full module graph, so a missing transitive "
        "dep surfaces immediately) and, on probe failure, ``mv`` the corrupt "
        "symlink aside and fall back to ``npm ci``. ``rm`` stays deny-listed "
        "so ``mv`` is the only safe cleanup.\n"
        "     # Note: ``<project-root>`` in the bash below is the absolute path "
        "of the dispatched project's main checkout — never the worktree path "
        "(that tree has no node_modules yet). The dispatcher inlines the exact "
        "string (see ``_build_ship_instructions`` in backend/app/kanban/"
        "dispatch.py, kaart a962b209…). Path is double-quoted so a project "
        "named ``My Project`` or ``prod$1`` doesn't break the test/ln.\n"
        "     ( cd frontend && \\\n"
        "       if [ -d node_modules ] && [ ! -d node_modules/.bin ]; then \\\n"
        "         mv node_modules \"../node_modules.partial-$(date +%s)\" && \\\n"
        "         echo \"moved partial node_modules aside (missing .bin/)\"; \\\n"
        "       fi && \\\n"
        "       if [ ! -d node_modules ]; then \\\n"
        "         BASE=$(git merge-base HEAD origin/master) && \\\n"
        "         if git diff --quiet \"$BASE\" origin/master -- frontend/package-lock.json \\\n"
        f"            && [ -d {bin_q} ]; then \\\n"
        f"           ln -s {nm_q} node_modules && \\\n"
        f"           echo \"bootstrapped frontend/node_modules via symlink (lockfile matches master)\" && \\\n"
        "         # Sanity-probe the symlinked install: ``.bin/``-presence is "
        "necessary but not sufficient (card 9b7c2a98…). ESLint 10 (card "
        "1e6fbe4e…) removed ``lib/cli.js`` as a resolvable subpath, so the "
        "legacy ``require('eslint/lib/cli.js')`` probe always fails on a "
        "healthy install and burns the symlink fast-path for a redundant "
        "``npm ci``. The replacement loads eslint's main module via its "
        "package exports (``require('eslint')`` resolves ``lib/api.js``) "
        "AND resolves the two transitive deps that ``npm run lint`` actually "
        "exercises — ``espree`` (ESLint's parser) and its ``acorn`` dep. "
        "Together those cover the failure class the legacy probe was "
        "guarding against; on probe failure, ``mv`` the corrupt symlink "
        "aside (``rm`` is deny-listed) and fall back to ``npm ci`` — the "
        "outer block has already passed, so the cleanup must re-install "
        "in-place.\n"
        "         if ! node -e \"require('eslint'); require.resolve('espree'); require.resolve('acorn')\" >/dev/null 2>&1; then \\\n"
        "           mv node_modules \"../node_modules.corrupt-$(date +%s)\" && \\\n"
        "           echo \"WARN: symlinked install failed probe (missing transitive dep?) — falling back to npm ci\" && \\\n"
        "           npm ci; \\\n"
        "         fi; \\\n"
        "         else \\\n"
        "           npm ci; \\\n"
        "         fi; \\\n"
        "       fi && \\\n"
        "       npm run lint && npm run build \\\n"
        "     )   # only proceed once green\n"
        "   else\n"
        "     echo 'geen frontend-diff — gate overgeslagen'\n"
        "   fi\n"
        "   ```\n"
        "   A branch that *does* touch ``frontend/`` (including a mixed "
        "frontend+docs diff) runs the gate unconditionally; only a branch with "
        "no ``frontend/`` change skips it.  "
        "Do **not** run backend pytest locally in this repo — that step was removed "
        "deliberately (shared box; concurrent dispatched sessions running full pytest "
        "caused multi-minute stalls / SSH idle-disconnects).  GitHub Actions "
        "(``quality.yml``) runs ruff + pytest against your push and is the backend "
        "gate; it also re-runs the frontend checks as a backstop, but by then the "
        "work may already be merged — it is not a substitute for checking the "
        "frontend yourself first.  If a frontend check fails, fix it, re-run, and "
        "only ship once green.  Never ship a known-red frontend check.\n"
    )
    commit = (
        "3. **Commit your work** — "
        "**Schema/column-rename veegt langs:** als je diff een `ALTER TABLE "
        "... RENAME COLUMN` (of een andere model/Pydantic-schema-rename) "
        "introduceert, draai dan `bash scripts/check-schema-rename-coverage.sh "
        "--strict` en werk elke hit bij vóór de commit. Een gemiste "
        "referentie levert een silent-red test op CI — net zoals "
        "kanban-kaart `ad15e08271c242238db239a90dc559d4` documenteerde voor "
        "commit 558ca55 (de `provider` → `cli` rename shipte met 2 latent-red "
        "tests). Het script zoekt in `backend/app/` én `backend/tests/` op "
        "resterende verwijzingen. "
        "**Bron-analysedoc bijwerken (na een gefilede follow-up):** rondt je "
        "kaart een follow-up af die in zijn beschrijving of "
        "`metadata.facet`/`metadata.parent_card` naar een "
        "`docs/cockpit/*.md`-analyse-/designdoc verwijst, voeg dan **vóór de "
        "commit** een korte `✅ Geïmplementeerd (kaart <id>)`-regel toe aan de "
        "paragraaf van dat doc die de gap beschreef. Zo blijft het doc niet als "
        "\"niets geïmplementeerd, alleen analyse + gefilede gaten\" staan "
        "terwijl zijn eigen follow-ups al gemerged zijn (geobserveerd op de "
        "vier facet-docs van synthese-kaart `c980a926…`: 33 van 35 follow-ups "
        "waren al gemerged terwijl 2 van de 4 docs zich nog als pure analyse "
        "presenteerden). **De bron is `metadata[\"spec_doc\"]`** — als de "
        "kaart-context boven aan deze prompt een regel `**Brondoc (spec_doc):** "
        "…` toont, is dat het docpad dat je moet bijwerken. Geen `spec_doc`-regel "
        "in de prompt én geen analysedoc-verwijzing in beschrijving/facet/"
        "parent_card? Sla deze stap over. **Geen retroactieve verplichting** — "
        "alleen het doc dat jouw kaart raakt; raakt je kaart geen analysedoc, "
        "sla je deze stap over. "
        "make sure every change is committed to the current branch.\n"
    )
    # Visible-UI affordance: one browser count (kanban card 07c05b84152240bdb28c61fec4e840e1).
    # Endpoint-tests en component-tests bewijzen samen niet dat ze
    # verbonden zijn — kaart `d444b2d0…` (agent-bridge →
    # kanban-kaart-navigatie) is twee rondes als "af" geshipt met groene
    # tests terwijl de knop nul keer in de app rendere. Voeg dit alleen
    # toe op de inlined dispatch-prompt wanneer de kaart een **nieuwe
    # zichtbare UI-affordance** oplevert; de conditie moet scherp zijn
    # anders wordt de regel genegeerd of betaalt élke kaart een
    # browser-run (kaart 07c05b84152240bdb28c61fec4e840e1, acceptance #3).
    # Drift-val: deze sectie MOET byte-gelijk zijn met `## 2b. Visible
    # UI affordance` in `.claude/skills/git-ship/SKILL.md`; de drift-test
    # `backend/tests/test_ship_recipe_drift.py` bewaakt minstens de
    # canonieke substrings (`Visible UI affordance`,
    # `localhost:5173`, `count()`).
    ui_browser_count = (
        "2b. **Visible UI affordance: one browser count** — endpoint-tests "
        "en component-tests bewijzen samen niet dat ze verbonden zijn. "
        "Kaart `d444b2d0…` (agent-bridge → kanban-kaart-navigatie) is twee "
        "rondes als \"af\" geshipt met groene tests terwijl de knop nul "
        "keer in de app rendere: `/sessions` had het `card_id`-veld dat de "
        "frontend-test voedde, maar de tegels kwamen uit `/teams` zonder "
        "enrichment. Twee groene tests, twee blinde vlekken, en het enige "
        "dat het had gevonden is één regel Playwright in de draaiende app. "
        "**Worktree-waarschuwing.** Als je in een worktree werkt "
        "(`.claude/worktrees/<branch>/`) doet de gedeelde dev-stack op "
        "`http://localhost:5173` de **hoofd-checkout** (master), niet jouw "
        "diff — ook wanneer geen andere sessie de poorten vasthoudt. Drie "
        "identieke meetwaardes vóór en ná je fix zijn het typerende "
        "symptoom. Gebruik vanuit een worktree **altijd** een eigen Vite op "
        "een vrije poort vanuit je worktree (zie "
        "[`docs/cockpit/isolated-component-preview.md`](./docs/cockpit/isolated-component-preview.md)), "
        "ook wanneer `cockpit.sh start` wel wil starten.\n"
        "   **Wanneer dit geldt.** De kaart voegt een **nieuwe zichtbare "
        "UI-affordance** toe: een knop, link, indicator, paneel, dialoog, "
        "route, of een element dat op het scherm verschijnt wanneer het "
        "er eerder niet was. **Niet** voor: backend-only kaarten, "
        "docs-kaarten, refactors, API-wijzigingen zonder UI-vertaling, "
        "test-coverage, of bug-fixes die niets nieuws tonen. Een bestaand "
        "element aanpassen (kleur, label, hover-state) is geen nieuwe "
        "affordance en valt buiten deze gate. Bij twijfel: als er vóór de "
        "diff géén DOM-element was op pad X en erna wel, telt het.\n"
        "   **Wat je doet.** Eén telling of screenshot die bewijst dat de "
        "affordance in de draaiende app aanwezig is:\n"
        "   ```js\n"
        "   const { chromium } = require('@playwright/test')\n"
        "   const browser = await chromium.launch()\n"
        "   const page = await browser.newPage()\n"
        "   await page.goto('http://localhost:5173/<route>')\n"
        "   const n = await page.locator('<jouw-affordance-selector>').count()\n"
        "   // Verwacht ≥ 1; 0 = de feature bestaat niet in de UI\n"
        "   await browser.close()\n"
        "   ```\n"
        "   Drie regels Playwright tegen de al-draaiende dev-stack zijn "
        "genoeg — `./scripts/cockpit.sh start` of `./scripts/dev.sh` "
        "levert `http://localhost:5173` en Chromium zit al in de tree "
        "(zie `frontend/package.json` → `@playwright/test`). Weigert "
        "`./scripts/cockpit.sh start` omdat een andere sessie de poorten "
        "8000/5173 vasthoudt, volg dan "
        "[`docs/cockpit/isolated-component-preview.md`](./docs/cockpit/isolated-component-preview.md) "
        "om de component in een scratch Vite op een vrije poort te "
        "mounten en dáár de telling te doen — geen wachttijd, geen "
        "gedeelde data. `<jouw-affordance-selector>` is een concrete "
        "`getByRole` / `getByLabel` / `getByTestId` van de affordance die "
        "de kaart belooft; `count()` ≥ 1 is de canonieke assertion. Een "
        "screenshot naar `/tmp/<kaart-id>-<kind>.png` is een geldige "
        "evidence als de affordance geen getalsvorm heeft (een banner is "
        "tekst, geen affordance — `getByText(...)` is geen selector die "
        "telt). Sla de `count()`-uitslag of het pad op in je "
        "`**Summary:**`-comment zodat de reviewer 'm ziet.\n"
    )

    retro_direct = _build_session_retro_step(step_number=7)
    retro_pr = _build_session_retro_step(step_number=7)

    if ship_mode == "direct":
        shipping = (
            "4. **Ship (direct mode)** — merge your branch into master and push. "
            "You are in a linked worktree while ``master`` is checked out in the "
            "main working copy, so checking out ``master`` here fails with "
            "``'master' is already used by worktree at ...``. Merge through a "
            "throwaway detached worktree instead — it never touches your current "
            "checkout:\n"
            "   ```bash\n"
            "   BRANCH=$(git rev-parse --abbrev-ref HEAD)\n"
            "   # Pre-flight: the detached worktree below only sees COMMITTED "
            "state. Uncommitted changes to TRACKED files would merge as a silent "
            "no-op (\"Everything up-to-date\") — abort so you commit them first.\n"
            "   # Tracked-only on purpose: `git ls-files --others "
            "--exclude-standard` used to be part of this condition and blocked "
            "ships on untracked files belonging to OTHER concurrent sessions "
            "sharing this worktree mount (544 files in a foreign "
            "`.tmp-measure-token-saver/` harness dir, kanban card c28e576d…). "
            "Those files can't cause the silent no-op this guard exists for — "
            "the merge never reads them — and since `rm` is deny-listed the only "
            "recovery was `mv`-ing another session's work aside. "
            "`git status --porcelain | grep -v '^??'` keeps every tracked state "
            "(` M`, `M `, `MM`, `A `, `D `) so a `git add` without a "
            "`git commit` is still refused, and drops only the `??` lines.\n"
            "   # The trailing `--` is load-bearing: it separates revisions "
            "from paths. Without it, a file named `HEAD` anywhere in the repo "
            "root makes the argument ambiguous and git exits 128 with `fatal: "
            "ambiguous argument 'HEAD': both revision and filename` — which, "
            "under `if ! ...`, reads as \"tree is dirty\" and aborts EVERY "
            "ship with a bogus uncommitted-changes error (kanban card "
            "7dd8a3dd…). `--` costs nothing and makes the guard immune to "
            "that class.\n"
            "   if ! git diff --quiet HEAD -- || [ -n \"$(git status --porcelain "
            "| grep -v '^??')\" ]; then\n"
            "     echo 'ERROR: uncommitted changes to tracked files in this "
            "worktree — run git add + git commit (step 3), then re-run.' >&2; "
            "exit 1\n"
            "   fi\n"
            "   # Untracked files are advisory, never fatal: a brand-new file you "
            "forgot to `git add` would ship as a silent omission, so list them — "
            "but do NOT exit, because most untracked noise here belongs to a "
            "concurrent session.\n"
            "   UNTRACKED=$(git ls-files --others --exclude-standard)\n"
            "   if [ -n \"$UNTRACKED\" ]; then\n"
            "     echo 'NOTE: untracked files present (not blocking the ship). "
            "If any of these are YOURS and belong in this card, git add + git "
            "commit them now:' >&2\n"
            "     printf '%s\\n' \"$UNTRACKED\" | head -20 >&2\n"
            "   fi\n"
            "   # Throwaway worktree location. Two constraints, pulling in "
            "opposite directions:\n"
            "   #   1. NOT under `mktemp -d` / `/tmp`. The Bash tool's harness "
            "can reap `/tmp` between calls, so a /tmp-resident worktree may "
            "vanish mid-ship: the merge commit lands in a now-missing "
            "checkout, the subsequent `git push` fails with a spurious "
            "non-fast-forward, and the local merge is lost. (kanban card "
            "01aa1ef5…)\n"
            "   #   2. NOT under `.git/worktrees/` either. That was the "
            "previous fix for (1), and it is actively harmful: "
            "`.git/worktrees/<name>/` is ALSO where git keeps its own admin "
            "files (HEAD, index, MERGE_*, commondir, gitdir) for that very "
            "worktree. Placing the CHECKOUT at the same path means the two "
            "overlap — so `git -C \"$WT\" add -A` in the conflict carve-out "
            "staged git's own admin files, and one ship through that branch "
            "committed ten of them to the repo root. From then on every "
            "`git worktree add` checked the tracked copies out over git's "
            "live admin files (\"fatal: .../index: index file smaller than "
            "expected\") and no card could ship at all. (kanban card "
            "7dd8a3dd…)\n"
            "   # `$HOME/.cache/` satisfies both: persistent across Bash "
            "calls (not reaped), and outside every git working tree and "
            "gitdir. Note git still registers the admin slot under "
            "`.git/worktrees/<basename-of-WT>` (where `<basename-of-WT>` is "
            "the branch-derived suffix below) — that is correct and harmless; "
            "only the CHECKOUT must live elsewhere.\n"
            "   SHIP_TMP=\"${HOME}/.cache/cockpit-ship\"\n"
            "   mkdir -p \"$SHIP_TMP\"\n"
            # Slot derived from `$BRANCH`, NOT from `$$` (PID). The Bash tool
            # spawns a fresh shell per call, so `$$` drifts between calls —
            # a recipe split across calls lost the worktree path with
            # `fatal: cannot change to …: No such file or directory` on
            # every `git -C "$WT" …` line. `${BRANCH//\//-}` is stable
            # within a single ship session (`$BRANCH` doesn't change between
            # calls) and gives unique slots across ships of different
            # branches. Same-branch ships serialize via the
            # `git worktree remove --force` at end of each ship.
            "   WT=\"$SHIP_TMP/ship-merge-${BRANCH//\\//-}\"\n"
            "   # Main-checkout path discovery (kanban card 5e83b6e0…,\n"
            "fourth iteration). The ship-worktree is a detached checkout\n"
            "that cannot update `master` itself — only the canonical\n"
            "checkout where `master` is checked out can do that. The\n"
            "dispatcher inlines `project_path` (= the main-checkout path)\n"
            "into the prompt (same source as the `<project-root>` used\n"
            "for the node_modules symlink), pre-quoted via `shlex.quote`\n"
            "(kaart a962b209… blocker C: single-quote-wrapped paths\n"
            "survive spaces, `$`, embedded quotes, and backslashes).\n"
            "Consumed bare — no surrounding double quotes — so the shell\n"
            "strips the single quotes during assignment and `git -C\n"
            "\"$MAIN_CHECKOUT\" …` resolves `$MAIN_CHECKOUT` to the path\n"
            "itself. The skill mirror in `.claude/skills/git-ship/SKILL.md`\n"
            "is self-discovering via `dirname $(git rev-parse\n"
            "--git-common-dir)` because the skill must work without the\n"
            "dispatch prompt. Both forms end up identical on the meta\n"
            "project.\n"
            "   MAIN_CHECKOUT=<main-checkout>\n"
            "   # Merge-base selection + divergence guard (kanban card\n"
            "5e83b6e0…; made ahead-aware 2026-08-05).\n"
            "Step 1 already fetched origin, but to be defensive we fetch\n"
            "again here — this worktree may have been running between step\n"
            "1 and step 4, and a concurrent session could have pushed to\n"
            "origin in that window.\n"
            "   # Label semantics (kanban card 5e83b6e0…, second\n"
            "iteration): in `git rev-list --count A..B`, A..B enumerates\n"
            "commits reachable from B but NOT from A — i.e. commits B has\n"
            "that A doesn't. So `master..origin/master` = commits\n"
            "`origin/master` has that local `master` doesn't = how far\n"
            "local is BEHIND; and `origin/master..master` = the symmetric\n"
            "AHEAD count. An earlier revision had the two swapped, which\n"
            "printed `ahead=2 behind=0` while local master was actually 2\n"
            "BEHIND origin. Don't swap them back.\n"
            "   git fetch origin -q\n"
            "   BEHIND=$(git rev-list --count master..origin/master\n"
            "2>/dev/null || echo \"?\")\n"
            "   AHEAD=$(git rev-list --count origin/master..master\n"
            "2>/dev/null || echo \"?\")\n"
            "   # Three shapes, and only ONE of them is a genuine blocker.\n"
            "The previous revision blocked on two of them, which is what\n"
            "made this guard self-reinforcing on a busy box: the post-push\n"
            "`pull --ff-only` below skips with a WARN whenever the main\n"
            "checkout is dirty (a concurrent agent's in-flight edits),\n"
            "local `master` then falls behind, and *every* subsequent ship\n"
            "tripped the guard — even though nothing was at risk.\n"
            "   if git merge-base --is-ancestor origin/master master\n"
            "2>/dev/null; then\n"
            "     # origin/master is reachable from local master: local is\n"
            "at, or ahead of, origin. Base on LOCAL `master` — on a\n"
            "multi-session box it routinely carries other agents'\n"
            "not-yet-pushed commits, and basing on `origin/master` here\n"
            "would strand them (kanban card 5e83b6e0…).\n"
            "     BASE=master\n"
            "   elif git merge-base --is-ancestor master origin/master\n"
            "2>/dev/null; then\n"
            "     # Behind-only: ahead=0, so local `master` has NO commits\n"
            "that origin/master lacks — there is literally nothing to\n"
            "strand, and the push below is a plain fast-forward. Base on\n"
            "`origin/master` and ship. This is NOT the same as the rejected\n"
            "\"always base on origin/master\" shape: that one also fired when\n"
            "ahead>0, which is the stranding bug.\n"
            "     BASE=origin/master\n"
            "     echo \"NOTE: local master is $BEHIND behind / 0 ahead —\n"
            "basing this ship on origin/master (nothing to strand).\" >&2\n"
            "     echo \"  The main checkout's tree is unchanged; reconcile\n"
            "it at your leisure with:\" >&2\n"
            "     echo \"  git -C <main-checkout> pull --rebase origin\n"
            "master\" >&2\n"
            "   else\n"
            "     # True divergence: BOTH sides have commits the other\n"
            "lacks. A push from either base would be rejected as\n"
            "non-fast-forward, and picking one silently discards the other\n"
            "side's work. This is the only shape that needs a human.\n"
            "     echo \"ERROR: local master has DIVERGED from origin/master\n"
            "— both sides have unique commits.\" >&2\n"
            "     echo \"  ahead=$AHEAD behind=$BEHIND (master vs\n"
            "origin/master)\" >&2\n"
            "     echo \"  Reconcile: git -C <main-checkout> pull --rebase\n"
            "origin master\" >&2\n"
            "     echo \"  Then re-run the ship from this worktree.\n"
            "report_impediment.\" >&2\n"
            "     exit 1\n"
            "   fi\n"
            "   git worktree add --detach \"$WT\" \"$BASE\"\n"
            "   # 0-byte-index guard. A predecessor that aborted mid-ship in "
            "the shared gitdir can leave this slot's `index` truncated to 0 "
            "bytes, and `git worktree add` reports success anyway — the "
            "corruption only surfaces on the next command, as "
            "`fatal: …/index: index file smaller than expected`. Worse, "
            "`git worktree remove --force` then refuses with `is not a "
            "working tree`, so the slot is orphaned and the ship needs a "
            "manual rescue (kanban card 608e2a27…). The checkout already "
            "holds the right tree; only the index needs rebuilding, which "
            "`read-tree HEAD` does from the slot's own HEAD. Detect and "
            "repair here, BEFORE the merge, so the recovery is automatic "
            "instead of ~4 manual tool calls.\n"
            "   WT_GITDIR=$(git -C \"$WT\" rev-parse --absolute-git-dir)\n"
            "   if [ ! -s \"$WT_GITDIR/index\" ]; then\n"
            "     echo \"WARN: 0-byte index in $WT_GITDIR — rebuilding from "
            "HEAD (aborted predecessor in the shared gitdir).\" >&2\n"
            "     if ! git -C \"$WT\" read-tree HEAD; then\n"
            "       echo \"ERROR: read-tree HEAD failed — slot $WT is "
            "unusable; report_impediment.\" >&2\n"
            "       exit 1\n"
            "     fi\n"
            "   fi\n"
            "   if ! git -C \"$WT\" merge --no-ff \"$BRANCH\" -m \"Merge $BRANCH\"; then\n"
            "     # CONFLICT path: try the generated-doc-index carve-out, "
            "otherwise report_impediment. The condition MUST be "
            "machine-checkable — a handwritten conflict always falls "
            "through (kanban card efb8187b…). A *non-empty subset* of "
            "{docs/cockpit/README.md, docs/cockpit/llms.txt} also passes "
            "(kanban card 72db7429…): both files are regenerated from "
            "frontmatter anyway, so a conflict in only one of the two is "
            "the same class as both — `comm -23` over the expected set "
            "surfaces any path that ISN'T a generated file (the actual "
            "exclusion predicate).\n"
            "     CONFLICTED=$(git -C \"$WT\" diff --name-only --diff-filter=U "
            "| LC_ALL=C sort -u)\n"
            "     EXPECTED=$(printf 'docs/cockpit/README.md\\ndocs/cockpit/"
            "llms.txt\\n' | LC_ALL=C sort -u)\n"
            "     NON_GENERATED=$(comm -23 <(printf '%s\\n' \"$CONFLICTED\") "
            "<(printf '%s\\n' \"$EXPECTED\"))\n"
            "     if [ -n \"$CONFLICTED\" ] && [ -z \"$NON_GENERATED\" ]; then\n"
            "       # Subset predicate passed. README.md is *partially* "
            "generated — only the block between "
            "`<!-- BEGIN GENERATED DOC INDEX -->` and "
            "`<!-- END GENERATED DOC INDEX -->` is owned by the regenerate "
            "script; the surrounding hand-curated prose (feature→canonical-doc "
            "mapping, \"Regels\", etc.) must NOT be silently clobbered. So "
            "if README.md is in the conflict set, verify every conflict hunk "
            "sits between the markers — anything outside falls through "
            "(kanban card 72db7429…). The check runs BEFORE the "
            "`checkout --theirs` below clears the merge markers; once "
            "cleared, the hunks are gone and the check has nothing to look "
            "at. The structural invariant in "
            "`backend/tests/test_ship_recipe_drift.py::test_readme_marker_check_sits_between_enumeration_and_open` "
            "pins this order.\n"
            "       if printf '%s\\n' \"$CONFLICTED\" | grep -qx 'docs/cockpit/README.md'; then\n"
            "         README_FILE=\"$WT/docs/cockpit/README.md\"\n"
            "         BEGIN_LINE=$(grep -nF '<!-- BEGIN GENERATED DOC INDEX' "
            "\"$README_FILE\" 2>/dev/null | head -1 | cut -d: -f1)\n"
            "         END_LINE=$(grep -nF '<!-- END GENERATED DOC INDEX -->' "
            "\"$README_FILE\" 2>/dev/null | head -1 | cut -d: -f1)\n"
            "         if [ -z \"$BEGIN_LINE\" ] || [ -z \"$END_LINE\" ]; then\n"
            "           echo \"ERROR: docs/cockpit/README.md missing "
            "BEGIN/END GENERATED DOC INDEX markers — falling back to "
            "report_impediment.\" >&2\n"
            "           printf '  conflicted: %s\\n' $CONFLICTED >&2\n"
            "           echo \"Conflicted worktree left at $WT for inspection "
            "(not removed).\" >&2\n"
            "           exit 1\n"
            "         fi\n"
            "         CONFLICT_LINES=$(grep -nE '^(<<<<<<< |=======$|"
            ">>>>>>> )' \"$README_FILE\" 2>/dev/null || true)\n"
            "         if [ -n \"$CONFLICT_LINES\" ]; then\n"
            "           OUTSIDE=$(awk -F: -v b=\"$BEGIN_LINE\" -v e=\"$END_LINE\" "
            "'$1 < b || $1 > e { print }' <<< \"$CONFLICT_LINES\")\n"
            "           if [ -n \"$OUTSIDE\" ]; then\n"
            "             echo \"ERROR: docs/cockpit/README.md has conflict "
            "hunks outside the generated block — falling back to "
            "report_impediment.\" >&2\n"
            "             printf '  offending lines:\\n%s\\n' \"$OUTSIDE\" >&2\n"
            "             echo \"Conflicted worktree left at $WT for inspection "
            "(not removed).\" >&2\n"
            "             exit 1\n"
            "           fi\n"
            "         fi\n"
            "       fi\n"
            "     else\n"
            "       echo \"ERROR: merge conflict in non-generated files (or "
            "empty conflict set) — falling back to report_impediment.\" >&2\n"
            "       printf '  conflicted: %s\\n' $CONFLICTED >&2\n"
            "       echo \"Conflicted worktree left at $WT for inspection "
            "(not removed).\" >&2\n"
            "       exit 1\n"
            "     fi\n"
            "     # Carve-out: at least one of the two generated doc-index "
            "files is conflicted. `--theirs` clears the merge markers; the "
            "next regenerate step overwrites both files anyway, so `--theirs` "
            "vs `--ours` is moot in practice. The script MUST be invoked "
            "through the worktree path — "
            "`scripts/generate-doc-index.py:78` derives its repo-root from "
            "`Path(__file__).resolve().parent.parent`, so "
            "`./scripts/generate-doc-index.py` would regenerate the calling "
            "shell's tree, not $WT.\n"
            "     git -C \"$WT\" checkout --theirs -- docs/cockpit/README.md "
            "docs/cockpit/llms.txt\n"
            "     \"$WT\"/scripts/generate-doc-index.py\n"
            "     if ! \"$WT\"/scripts/generate-doc-index.py --check --strict; then\n"
            "       echo \"ERROR: generate-doc-index.py --check --strict "
            "failed after regenerate.\" >&2\n"
            "       exit 1\n"
            "     fi\n"
            "     # Stage the two generated files BY NAME, never `add -A`. A "
            "blind `add -A` stages everything under the worktree root, which "
            "is how ten of git's own admin files (HEAD, index, MERGE_*, …) "
            "got committed to the repo root and broke every subsequent ship "
            "(kanban card 7dd8a3dd…). Moving the worktree out of `.git/` "
            "already removes that specific exposure; an explicit path list "
            "closes the class — the carve-out is only ever entitled to commit "
            "the files it just regenerated, so it should only ever be able to "
            "stage those.\n"
            "     git -C \"$WT\" add -- docs/cockpit/README.md "
            "docs/cockpit/llms.txt\n"
            "     git -C \"$WT\" commit --no-edit\n"
            "   fi\n"
            "   if git -C \"$WT\" push origin HEAD:master; then\n"
            "     # Post-push main-checkout sync (kanban card 5e83b6e0…, "
            "fourth iteration). The divergence guard above bases on "
            "local `master`, so a successful push that doesn't also move "
            "local `master` in the main checkout leaves the guard tripped "
            "on every subsequent ship on this multi-session box — even "
            "though the divergence is fully explained by *our own* push. "
            "The cleanest way to sync the main checkout is "
            "`git -C \"$MAIN_CHECKOUT\" pull --ff-only origin master`, "
            "which in one step (a) fast-forwards the local master ref AND "
            "(b) updates the index AND working tree in the main checkout "
            "— so the dev-stack (`cockpit.sh`) keeps running against the "
            "latest tree. The throwaway `$WT` is detached HEAD and cannot "
            "update master itself, which is why the sync runs against "
            "`$MAIN_CHECKOUT` where master is actually checked out.\n"
            "     # `git pull --ff-only` REFUSES if the main checkout's "
            "working tree has changes that would be overwritten by the "
            "merge (e.g. a concurrent agent editing a file the merge "
            "also touches) — that is the right default, we do not want "
            "to clobber in-flight edits. Skip-with-WARN is the chosen "
            "trade-off (kanban card 5e83b6e0…, human decision on round 3): "
            "the push already landed on origin, so the only thing the "
            "guard sees is the next ship; the operator runs `git -C "
            "\"$MAIN_CHECKOUT\" pull --ff-only origin master` by hand "
            "once the in-flight edits are committed or stashed. The "
            "earlier round-3 `update-ref`-based fallback quietly bypassed "
            "this WARN (the ref-update almost always succeeded, moving the "
            "ref without the visible warning) — the human-visible drift "
            "stayed, but the signal vanished. Removing the ref-update "
            "fallback makes the WARN visible every time the sync is "
            "skipped, and accepts the trade-off that the divergence guard "
            "will trip on the next ship until the operator reconciles.\n"
            "     if ! git -C \"$MAIN_CHECKOUT\" pull --ff-only origin "
            "master 2>/dev/null; then\n"
            "       echo \"WARN: kon lokale master in hoofd-checkout "
            "niet bijwerken — working tree is vuil of pull weigert. "
            "Sync overgeslagen; volgende ship kan op de divergentie-guard "
            "lopen. Herstel handmatig met 'git -C \\\"$MAIN_CHECKOUT\\\" "
            "pull --ff-only origin master' (los eerst eventuele "
            "conflicten op die de working tree vuil houden).\" >&2\n"
            "     fi\n"
            "   else\n"
            "     # Push rejected (master moved / protected). Keep "
            "`origin/$BRANCH` alive — the pull-request fallback needs it. "
            "Deleting here would strand the work on exactly the path where "
            "the branch is still required.\n"
            "     echo \"WARN: push naar master afgewezen — origin/$BRANCH "
            "bewaard voor de pull-request-fallback.\" >&2\n"
            "   fi\n"
            "   git worktree remove --force \"$WT\"\n"
            "   ```\n"
            "   **Carve-out semantics.** If the merge block hits a conflict, "
            "the script enumerates the conflict set with ``git diff --name-only "
            "--diff-filter=U``. When that set is a **non-empty subset** of "
            "``{docs/cockpit/README.md, docs/cockpit/llms.txt}``, the script "
            "runs the carve-out automatically and the merge completes inline "
            "— no human intervention. ``docs/cockpit/llms.txt`` is fully "
            "regenerated; ``docs/cockpit/README.md`` is regenerated only "
            "between the ``<!-- BEGIN GENERATED DOC INDEX -->` and "
            "``<!-- END GENERATED DOC INDEX -->`` markers, and the carve-out "
            "verifies (via line numbers) that every conflict hunk sits "
            "inside that block — a conflict outside the markers falls through "
            "to ``report_impediment`` (kanban card 72db7429…). Both files are "
            "regenerated by ``scripts/generate-doc-index.py`` from the "
            "frontmatter of ``docs/cockpit/*.md``; concurrent docs-sessions "
            "each regenerate from their own frontmatter snapshot, the merged "
            "frontmatter is the union, and the regenerate inside ``$WT`` "
            "reconciles from that union. **Why the conflict must remain "
            "visible** "
            "(``.gitattributes``-alternative rejected): a ``merge=ours`` rule "
            "for both paths would suppress the conflict entirely and silently "
            "keep master's pre-regeneration index, losing any new frontmatter "
            "added on the branch until someone manually re-runs the script. "
            "The \"conflict → regenerate\" loop is the right pattern — the "
            "conflict acts as a freshness alarm.\n\n"
            "   If the carve-out rejects (a handwritten file is in the conflict "
            "set), the worktree at ``$WT`` is left in its conflicted state for "
            "inspection and the script exits 1. Follow the existing rule, "
            "``report_impediment`` naming all conflicting files so a human "
            "can resolve it; never force-push or discard either side of the "
            "conflict.\n"
            "5. **Attach the deliverable** — ``attach_deliverable`` with "
            "``kind=\"branch\"`` and ``ref=<your-branch-name>``.\n"
            "6. **Clean up the now-dead remote branch** — the merge script "
            "above no longer deletes it inline (kanban card "
            "``692d3522432b…``: the inline delete raced with the attach "
            "above, so the MCP call landed on a ref that was already gone). "
            "Run only after ``attach_deliverable`` returns, so the ref is "
            "still alive while the call is in flight:\n"
            "   ```bash\n"
            "   # Merge landed on master — delete the now-dead remote "
            "branch. GitHub's `delete_branch_on_merge` (enabled "
            "2026-07-07) only fires when a *PR* merges; this route closes "
            "no PR, so without this line every shipped card leaves a "
            "branch on `origin` forever (kanban card 3027671c…: 7 "
            "fully-merged branches piled up over 6 weeks). Guard the "
            "delete on the remote ref actually existing: a direct-mode "
            "branch that never made it to `origin` yields two `error:` "
            "lines from `git push --delete` that read like a failed ship "
            "(kanban card 552036fa…). Fail-open — an already-deleted "
            "branch must not kill the ship. Only the REMOTE branch goes; "
            "the local branch stays, so redispatch/resume off it still "
            "works.\n"
            "   if git ls-remote --exit-code --heads origin \"$BRANCH\" "
            ">/dev/null 2>&1; then\n"
            "     git push origin --delete \"$BRANCH\" || echo \"WARN: kon "
            "origin/$BRANCH niet verwijderen\"\n"
            "   else\n"
            "     echo \"INFO: origin/$BRANCH bestond niet — niets te "
            "verwijderen\"\n"
            "   fi\n"
            "   ```\n"
            + retro_direct +
            "8. **Move the card to Done** — ``move_card`` with ``column=\"Done\"`` "
            "and ``summary=<what you did>``, a few sentences on the work you "
            "completed.  ``summary`` is required for this move; the call is "
            "rejected without it.  **Product-taal** (conventie §5 van "
            "`docs/cockpit/kanban-conventions.md`, kaart `4358fe0a…` + "
            "kaart `8b3ce64c…`): volg de verplichte **drie-delen-vorm** — "
            "één **Uitkomst**-zin die leidt met *productbetekenis* (wat kan "
            "de product owner nu doen / zien / beslissen dat voorheen niet "
            "kon), gevolgd door 2-4 bullets met de engineering-detail "
            "(bestanden, endpoints, tests), en optioneel een "
            "**Rest / nazicht**-sectie. Daarboven gelden de drie "
            "proces-regels: **geen proces-meta** in de mens-gerichte "
            "samenvatting (geen FCR-uitslag, geen session-retro-uitkomst, "
            "geen dedup-boekhouding, geen audit-log-archeologie — die horen "
            "in de activity-feed), **jargon = naam + waarom** (een interne "
            "component noem je alleen met wat 'ie voor de lezer betekent), "
            "en lead-with-product-meaning in elke openingszin. Voorbeeld: "
            "niet \"POST /usage/subscription + SubscriptionUsageCard.tsx\", "
            "wél \"Product owner kan nu het abonnementsverbruik zien op de "
            "Usage-pagina (POST /usage/subscription + "
            "SubscriptionUsageCard.tsx)\". Een kale engineering-summary "
            "voldoet aan de gate maar niet aan de product-taal-conventie. "
            "Voor een ``report_impediment`` met ``options``: druk de opties "
            "uit als **producttrade-offs**, niet als implementatie-forks.  "
            "The backend will kill this session and remove the worktree.\n"
        )
    else:
        shipping = (
            "4. **Ship (pull-request mode)** — push your branch, open a PR, and "
            "queue it to merge automatically once checks pass:\n"
            "   ```bash\n"
            "   gh auth status || { echo 'gh unavailable — manual PR needed'; exit 1; }\n"
            "   git push -u origin HEAD\n"
            "   gh pr create --draft --base master --fill\n"
            "   gh pr ready\n"
            "   gh pr merge --auto --squash\n"
            "   ```\n"
            "   Capture the PR URL from ``gh pr create`` output.\n"
            "   If ``gh`` is unavailable: push the branch, ``comment`` with the "
            "branch name and note that a manual PR is needed, then stop here — "
            "do not move the card to Done.\n"
            "5. **Wait for the merge gate** — poll until the PR merges or a "
            "check fails; do not skip this, the card's next step depends on it:\n"
            "   ```bash\n"
            "   ITER=0\n"
            "   while true; do\n"
            "     DATA=$(gh pr view --json state,mergeStateStatus,statusCheckRollup)\n"
            "     STATE=$(echo \"$DATA\" | jq -r '.state')\n"
            "     MERGE_STATUS=$(echo \"$DATA\" | jq -r '.mergeStateStatus')\n"
            "     echo \"PR state: $STATE mergeStateStatus=$MERGE_STATUS\"\n"
            "     if [ \"$STATE\" = \"MERGED\" ]; then\n"
            "       break\n"
            "     fi\n"
            "     if [ \"$STATE\" = \"CLOSED\" ]; then\n"
            "       echo 'PR was closed without merging'; exit 1\n"
            "     fi\n"
            "     # mergeStateStatus=BLOCKED also just means \"checks still running\" "
            "— only a genuinely failed/cancelled/timed-out check is a real failure.\n"
            "     FAILED=$(echo \"$DATA\" | jq '[.statusCheckRollup[]? | "
            "select((.conclusion // .status // .state // \"\") | "
            "test(\"FAILURE|ERROR|CANCELLED|TIMED_OUT|ACTION_REQUIRED\"; \"i\"))] "
            "| length')\n"
            "     if [ \"$FAILED\" -gt 0 ]; then\n"
            "       echo 'A required check failed'; exit 1\n"
            "     fi\n"
            "     if [ \"$MERGE_STATUS\" = \"DIRTY\" ]; then\n"
            "       echo 'PR has merge conflicts with the base branch'; exit 1\n"
            "     fi\n"
            "     ITER=$((ITER + 1))\n"
            "     if [ \"$ITER\" -ge 40 ]; then\n"
            "       echo 'Timed out after ~20 minutes waiting for PR to merge'; "
            "exit 1\n"
            "     fi\n"
            "     sleep 30\n"
            "   done\n"
            "   ```\n"
            "6. **Attach the deliverable** — ``attach_deliverable`` with "
            "``kind=\"pr\"`` and ``ref=<PR-URL>`` (or ``kind=\"branch\"`` if no PR).\n"
            + retro_pr +
            "8. **Move the card** — if the PR merged, ``move_card`` with "
            "``column=\"Done\"`` and ``summary=<what you did>``, a few sentences "
            "on the work you completed (``summary`` is required for this move; "
            "the call is rejected without it).  **Product-taal** (de "
            "product-taal-conventie §5 van "
            "`docs/cockpit/kanban-conventions.md`, kaart `4358fe0a…` + "
            "kaart `8b3ce64c…`): volg de verplichte **drie-delen-vorm** — "
            "één **Uitkomst**-zin die leidt met *productbetekenis*, gevolgd "
            "door 2-4 bullets met de engineering-detail, en optioneel een "
            "**Rest / nazicht**-sectie. Daarboven gelden de drie "
            "proces-regels: **geen proces-meta** in de mens-gerichte "
            "samenvatting (geen FCR-uitslag, geen session-retro-uitkomst, "
            "geen dedup-boekhouding, geen audit-log-archeologie — die horen "
            "in de activity-feed), **jargon = naam + waarom** (een interne "
            "component noem je alleen met wat 'ie voor de lezer betekent), "
            "en lead-with-product-meaning in elke openingszin. Een kale "
            "engineering-summary voldoet aan de gate maar niet aan de "
            "product-taal-conventie.  If the poll loop exited because a "
            "check failed, the PR was closed, or the wait timed out, call "
            "``report_impediment`` instead so a human can look at it — do not "
            "move to Done. Voor een ``report_impediment``: ``options: list[str]`` "
            "is binair — laat het veld leeg voor een vrije-tekstvraag, óf lever "
            "precies 4 mee (de Impediment-UI toont steeds 4 keuze-knoppen; bij "
            "1-3 of 5+ weigert `mcp_server.report_impediment` met "
            "`invalid_option_count`). Druk ``options`` als *producttrade-offs* "
            "uit, niet als implementatie-forks.\n"
        )

    # ``<main-checkout>`` is a placeholder for the canonical checkout where
    # ``master`` is checked out — interpolated from ``project_path`` above
    # via the pre-quoted ``main_checkout_q``. The skill mirror in
    # ``.claude/skills/git-ship/SKILL.md`` is self-discovering via
    # ``dirname $(git rev-parse --git-common-dir)`` because the skill
    # must work without the dispatch prompt. Both forms end up identical
    # on the meta project (``/home/vdvgu/claude-cockpit``).
    return (
        feature_compliance_review + sync + tests + ui_browser_count + commit + shipping
    ).replace("<main-checkout>", main_checkout_q)


def _build_session_retro_step(step_number: int = 6) -> str:
    """Step injected before ``move_card → Done`` (after ``attach_deliverable``
    for executor/engineer cards; directly before the parent move for analyst
    cards).

    Inlines the headless-trim version of the ``session-retro`` skill so the
    step works for any spawned agent (whether or not it can read the skill
    files). Mirrors the source of truth at
    ``.claude/skills/session-retro/SKILL.md`` — keep them in sync.

    Wired for both phases: executor/engineer cards run it after shipping,
    analyst cards run it right before the ``move_parent → Done`` exit (see
    ``_build_analyst_session_end_instructions``).

    The ``step_number`` argument lets the caller pick the right place in the
    numbered sequence — 6 in direct mode (attach=5, move=7), 7 in
    pull-request mode (attach=6, move=8), 1 in the analyst flow (move=2).
    """
    return (
        f"{step_number}. **Run the session-end retro** — invoke the "
        "``session-retro`` skill "
        "(read ``.claude/skills/session-retro/SKILL.md`` for the full procedure). "
        "It walks this session backwards, applies a four-pass filter "
        "(systemic, materieel, actionable, novel), dedupes against existing "
        "Backlog/Impediment cards, and files 0–N ``[self-improve]`` cards. Even "
        "a clean session gets a no-op ``comment`` on this card so a follow-up "
        "sweeper can see the retro ran. Keep it light — under a minute, "
        "~3–5 tool calls; don't burn the ship budget writing lengthy "
        "descriptions.\n"
    )


def _build_knowledge_ship_instructions(ship_mode: str,
                                        project_path: str | None = None) -> str:
    """Lighter session-end workflow for ``ceremony_profile == "knowledge"``.

    Same machinery as ``_build_ship_instructions`` (worktree, direct merge to
    master, deliverable attach, retro, ``move_card → Done``), but the
    decisions on §4 of ``cockpit-richting-decision.md`` drop most of the
    ceremony that exists only because code work needs it:

    - **No FCR subagent.** A fresh-context compliance subagent is overkill
      for a markdown deliverable — the researcher can do the same check
      inline in three questions.
    - **No frontend lint/build.** Knowledge work does not touch
      ``frontend/``; the local pre-ship gate from §2 is skipped.
    - **No new-UI affordance browser count.** Knowledge work adds docs and
      notes, not DOM elements.
    - **No PR mode.** The board does not expose PR for knowledge projects:
      even when the dispatch-prompt header says ``Ship mode: pull-request``,
      this profile forces direct merge. The opener is told once why.
    - **Deliverable kind is ``note``** — a short title or doc-path. Code
      work attaches ``branch`` / ``pr`` / ``commit``; knowledge work
      attaches a human-readable pointer to the produced artefact.

    The detailed bash (sync, ``git worktree add``, the carve-out handling,
    the post-push main-checkout sync) lives in
    ``.claude/skills/git-ship/SKILL.md`` — the prompt only points at it,
    so this builder stays small. Mirrored by the researcher persona
    (``researcher.md``) which is the human-readable copy; update both
    together.
    """
    # ``cockpit-richting-decision.md`` §4 commits to *one* machinery: the
    # researcher persona reads the same skill the engineer persona reads,
    # just with a tighter filter on which steps to run. Pulling the bash
    # inline here as well would double-maintain two ~190-line blocks
    # (drift-val: kaart ``d9447e49``); pointing at the skill instead keeps
    # this builder under a screen and the skill as the single source of
    # truth.
    skill_path = ".claude/skills/git-ship/SKILL.md"
    effective_ship_mode = ship_mode
    pr_forced_note = ""
    if ship_mode == "pull-request":
        # PR mode is not a valid choice for knowledge work — the spec is
        # "geen PR" (no PR). Downgrade to direct merge with one explicit
        # line in the prompt so the researcher does not re-litigate it.
        effective_ship_mode = "direct"
        pr_forced_note = (
            "\n> **Note.** Your dispatch header said "
            f"``Ship mode: {ship_mode}``, but the project carries the "
            "knowledge ceremony profile which does not support PR mode. "
            "Treat the effective mode as ``direct`` — merge to master in "
            "one step, no draft PR. Section §4 of "
            "``cockpit-richting-decision.md`` is the source for that "
            "choice; this profile is the user-facing shape of §4.\n"
        )

    sync = (
        "1. **Sync** — `git fetch origin` so you are up to date with the "
        "remote.\n"
    )
    inline_compliance_check = (
        "**Inline compliance-check (replaces the FCR subagent).** "
        "Before shipping, walk through these three questions inline — the "
        "FCR subagent is intentionally *not* part of the knowledge "
        "profile (the cost/benefit doesn't pay back on a markdown "
        "deliverable), and a self-check is the right size for this profile:\n"
        "  1. **Spec coverage.** Does every requirement/bullet from the "
        "card description land in the deliverable? If a sub-bullet is "
        "missing, fix it before shipping — don't ship a partial.\n"
        "  2. **Product meaning.** Can the owner of this knowledge repo "
        "now do / see / decide something they could not before? "
        "Document it in your ``move_card → Done`` summary (the "
        "product-taal-conventie §5 in "
        "``docs/cockpit/kanban-conventions.md`` already requires the "
        "lead-with-product-meaning sentence — keep it).\n"
        "  3. **Deliverable ref.** Is the value you pass to "
        "``attach_deliverable(kind=\"note\", ref=…)`` a concrete pointer "
        "the next reader can open — a doc path, a section heading, a "
        "decision line — not \"see commit\"? The card row is the only "
        "place this ref lives once the session ends.\n"
        "If any answer is \"no\" or \"can't tell\": fix first, or "
        "``report_impediment`` with the open question. Don't ship a card "
        "you can't pass these three.\n"
    )
    no_frontend_checks = (
        "2. **Frontend checks are skipped for this profile.** The "
        "pre-ship frontend lint + build (skill §2) exists for code work "
        "that touches ``frontend/``; knowledge work produces markdown and "
        "docs, so the gate is intentionally absent. If your deliverable "
        "does touch code for some reason, run those checks yourself "
        "before shipping — the gate is skipped *by default*, not by "
        "policy.\n"
        "   Likewise, the new-UI affordance browser count (skill §2b) is "
        "skipped — knowledge work adds documents and decisions, not DOM "
        "elements.\n"
    )
    commit_step = (
        "3. **Commit** — `git add -A && git commit -m \"<descriptive "
        "summary>\"`. Keep the message focused on *what changed and why*; "
        "the project owner reads commits, not just docs.\n"
    )
    ship_step = (
        f"4. **Ship (mode = {effective_ship_mode}).** Follow `{skill_path}` "
        f"§4{'a' if effective_ship_mode == 'direct' else 'b'} end-to-end. "
        "The skill is provider-agnostic and mirrors the dispatch-prompt "
        "recipe; the merge-to-master (or PR) bash lives there so we don't "
        "duplicate ~190 lines of git plumbing here.\n"
    )
    deliverable_step = (
        "5. **Attach the deliverable** — "
        "``attach_deliverable(kind=\"note\", ref=\"<title or doc path>\")`` "
        "with the dispatch MCP tool (or the REST fallback when MCP is "
        "down). The note ref is the canonical handle the next reader of "
        "this card will look for — a doc path (``docs/cockpit/foo.md``), "
        "a section heading, or a decision line. Do **not** attach a "
        "branch / commit / PR for knowledge work — those kinds are "
        "reserved for the code profile.\n"
    )
    retro_step = (
        "6. **Run the session-end retro** — invoke the ``session-retro`` "
        "skill (read ``.claude/skills/session-retro/SKILL.md``; the "
        "knowledge profile is lighter on engineering detail but the "
        "four-pass filter and the dedupe pass still apply). Even a clean "
        "session gets a no-op ``comment`` so the retro sweeper sees it. "
        "Keep it under a minute, ~3–5 tool calls.\n"
    )
    done_step = (
        "7. **Move the card to Done** — ``move_card(column=\"Done\", "
        "summary=<what you delivered>)``. ``summary`` is required (the "
        "call is rejected without it). **Product-taal** (conventie §5 in "
        "``docs/cockpit/kanban-conventions.md``, kaart ``4358fe0a…`` + "
        "kaart ``8b3ce64c…``): lead with one *productbetekenis*-zin "
        "(what can the owner now see/decide that they could not before); "
        "follow with 2–4 bullets naming the doc/notitie that carries the "
        "deliverable; **no** process-meta in the human-facing summary "
        "(no FCR verdict, no retro outcome, no dedupe bookkeeping — "
        "those belong in the activity-feed). A bare \"wrote a doc\" "
        "passes the ``summary_required`` gate but fails the "
        "product-taal-conventie.\n"
    )
    return (
        f"{pr_forced_note}"
        f"{sync}"
        f"{inline_compliance_check}"
        f"{no_frontend_checks}"
        f"{commit_step}"
        f"{ship_step}"
        f"{deliverable_step}"
        f"{retro_step}"
        f"{done_step}"
    )


def _build_analyst_session_end_instructions() -> str:
    """Session-end workflow for analyst-phase cards.

    Analyst sessions are planning-only — no code is shipped, no worktree
    merge happens — so they get a lighter close than
    ``_build_ship_instructions``: run the retro, then the existing
    ``move_card(parent → Done)`` exit. No sync/test/commit/merge steps.
    """
    retro = _build_session_retro_step(step_number=1)
    move = (
        "2. **Move the parent card to Done** — ``move_card`` on the parent "
        "with ``column=\"Done\"`` and a summary of the plan (``summary`` is "
        "required for this move; the call is rejected without it). This is "
        "your exit signal — the backend then kills this session and removes "
        "the worktree. **Product-taal** (conventie §5 van "
        "`docs/cockpit/kanban-conventions.md`, kaart `4358fe0a…` + "
        "kaart `8b3ce64c…`): volg de verplichte **drie-delen-vorm** — één "
        "**Uitkomst**-zin die leidt met *productbetekenis* (wat kan de "
        "product owner nu doen / zien / beslissen dat voorheen niet kon), "
        "gevolgd door 2-4 bullets (kind-kaart-titels of deliverable-refs als "
        "opsomming), en optioneel een **Rest / nazicht**-sectie. De "
        "engineering-detail (welke persona-kolom, welke agent-rol) hoort in "
        "de kind-kaarten en in de bullets, niet in de openingszin. "
        "Daarboven gelden de drie proces-regels: **geen proces-meta** in de "
        "mens-gerichte samenvatting (geen FCR-uitslag, geen "
        "session-retro-uitkomst, geen dedup-boekhouding, geen "
        "audit-log-archeologie — die horen in de activity-feed of in "
        "retro-kaarten), **jargon = naam + waarom** (een interne component "
        "noem je alleen met wat 'ie voor de lezer betekent), en "
        "lead-with-product-meaning in elke openingszin. Een kale \"Plan "
        "opgesplitst in N taken\" voldoet aan de gate maar niet aan de "
        "product-taal-conventie. Voor een ``report_impediment``: "
        "``options: list[str]`` is binair — laat het veld leeg voor een "
        "vrije-tekstvraag, óf lever precies 4 mee (de Impediment-UI toont "
        "steeds 4 keuze-knoppen; bij 1-3 of 5+ weigert "
        "`mcp_server.report_impediment` met `invalid_option_count`). Druk de "
        "``options`` als **producttrade-offs** uit, niet als implementatie-"
        "forks.\n"
    )
    return retro + move


def _build_reviewer_session_end_instructions() -> str:
    """Session-end workflow for reviewer-phase cards (independent pre-Done gate).

    The reviewer is an *independent* gate: it reads the original card spec plus
    the work the engineer produced and decides whether the card may reach Done.
    It never writes code, merges, or ships — those already happened in the
    engineer session; re-doing them here would defeat the point of an
    independent reviewer. So this is deliberately NOT
    ``_build_ship_instructions``: no sync/test/commit/merge steps, just
    review → approve (Done) or reject (Impediment).

    Kept in sync with ``.claude/agents/reviewer.md`` — the persona body carries
    the same contract; update both together. See
    ``docs/cockpit/reviewer-agent-decision.md`` (REVISED 2026-07-18).
    """
    return (
        "You are the **independent reviewer**. This card was completed by "
        "another agent and routed to you *before* it may reach Done. Your job "
        "is a feature-compliance + consistency gate — **not** to write, fix, "
        "merge, or ship code. Follow these steps:\n\n"
        "1. **Read the original request** — the card title + description above "
        "are the wish (`de gestelde wens`). Note every requirement/bullet.\n"
        "2. **Find what was built** — call ``get_card`` (MCP) to read the "
        "deliverables and the engineer's ``**Summary:**`` comment. The branch "
        "deliverable names the work; in direct-ship mode the work is already on "
        "``master`` as a ``Merge <branch>`` commit. ``git fetch origin`` first, "
        "then inspect the diff — e.g. find the merge commit "
        "(``git log origin/master --merges --grep=<branch> -1 --format=%H``) "
        "and read ``git show <merge>`` / ``git diff <merge>^1 <merge>``, or "
        "review the open PR when one is attached.\n"
        "3. **Judge two things.** (a) *Compliance*: does the implementation do "
        "what the card asked — every requirement met, naming/behaviour/edge "
        "cases matching the spec, the claimed deliverable actually present? "
        "(b) *Consistency*: does it fit the rest of the application — existing "
        "patterns, conventions, no sibling features broken? Read the "
        "surrounding code to confirm, don't assume.\n"
        "4. **Decide.**\n"
        "   - **In order** → ``move_card`` with ``column=\"Done\"`` and a "
        "``summary`` recording what you verified (``summary`` is required; the "
        "call is rejected without it). This is the only approval path — the "
        "card reaches Done because you, the reviewer, cleared it.\n"
        "   - **Not in order** → ``report_impediment`` with a ``question`` that "
        "states clearly **why it is not in order** (concrete, with "
        "``file:line`` refs where possible) and what must change. Prefer a "
        "short ``options`` list when there's a decision for the human. The card "
        "moves to Impediment and, when the human resolves it, resumes with the "
        "original engineer to fix — then it comes back to you.\n"
        "   Never move a non-compliant card to Done, and never edit the code "
        "yourself to make it pass.\n"
    )



# ---- transport -------------------------------------------------------------
