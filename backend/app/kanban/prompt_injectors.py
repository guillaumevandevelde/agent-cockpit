"""Optional prompt-injectors for the dispatcher.

Caveman and Ponytail are MIT-licensed upstream plugins that ship a
system-prompt slice to compress output:

- Caveman — "respond terse like smart caveman"; ships in
  https://github.com/JuliusBrussee/caveman (MIT, © Julius Brussee).
- Ponytail — "you are a lazy senior developer"; ships in
  https://github.com/DietrichGebert/ponytail (MIT, © Dietrich Gebert).

The card that motivates this module (kaart ``d0446fd8…``) decided that
both injectors are implemented, with **independent per-lane switches
and a single board-wide kill-switch**. Default-off — no injector fires
unless an operator opts it in for a specific column.

## Granularity

Three layers, evaluated in order, all fail-closed (default off):

1. **Kill-switch**: a single ``prompt_injector:<project_key>`` row in
   ``KanbanMeta`` set to ``"1"`` disables BOTH injectors regardless of
   the column flags. Hot-path read; flipping it takes effect on the
   next dispatch tick without a backend restart.
2. **Per-column flags**: ``KanbanColumn.caveman_enabled`` and
   ``KanbanColumn.ponytail_enabled`` (INTEGER 0/1). Default 0 = off.
   Independent semantics — toggling one does not move the other.
3. **Promotion**: the helper returns both as empty strings when the
   gates are off, so ``build_card_prompt`` sees an empty injection and
   the spawned session runs with the unchanged persona preamble.

## Cache stability

The card warns that a varying system-prompt prefix busts Claude's
``cache_read`` and the output savings get eaten by cache misses. The
resolver is therefore deliberately pure: given the same
``(project_key, column_name)`` inputs and the same DB rows, it must
return byte-identical strings across calls. There is no
timestamp/UUID/random-anything — see
``tests/test_prompt_injectors.py::test_resolver_returns_byte_stable_output_for_same_inputs``.

## Attribution

The MIT licence requires the upstream copyright + permission notice to
accompany any substantial reuse. Each prompt constant begins with a
two-line attribution header pinning the upstream commit so a future
reader can see what version this text came from and that it has not
silently evolved.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.kanban.models import KanbanColumn, KanbanMeta, KanbanOp

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# --- Prompt text: verbatim upstream + MIT attribution header ---------------
#
# The upstream commit SHA is pinned in each attribution header so the
# reader knows which version of the text the slice came from. The text
# body that follows the header line is copied character-for-character
# from the upstream file at that commit; do not edit unless intentionally
# diverging from upstream, and if so update the commit-pin + the source
# row in ``docs/cockpit/decisions.md`` (the decision-register row this
# card adds).


CAVEMAN_PROMPT = """\
Source: github.com/JuliusBrussee/caveman @ ec83e5bace4c20484d704dea21e12fc4eb94e9aa (2026-08-04).
Licence: MIT © Julius Brussee. Verbatim reuse permitted under MIT with this attribution preserved.

Respond terse like smart caveman. All technical substance stay. Only fluff die.

## Persistence

ACTIVE EVERY RESPONSE. No revert after many turns. No filler drift. Still active if unsure. Off only: "stop caveman" / "normal mode".

Default: **full**. Switch: `/caveman lite|full|ultra|wenyan-lite|wenyan-full|wenyan-ultra|off`.

## Rules

Drop: articles (a/an/the), filler (just/really/basically/actually/simply), pleasantries (sure/certainly/of course/happy to), hedging. Fragments OK. Short synonyms (big not extensive, fix not "implement a solution for"). No tool-call narration, no decorative tables/emoji, no dumping long raw error logs unless asked — quote shortest decisive line. Standard well-known tech acronyms OK (DB/API/HTTP); never invent new abbreviations (cfg/impl/req/res/fn) — tokenizer split them same as full word: zero token saved, reader still decode. Full word cheaper AND clearer. No causal arrows (→) either — own token, save nothing. Technical terms exact. Code blocks unchanged. Errors quoted exact.

Never drop not/never/no/only/except — flip meaning worse than any token saved. Numbers, units exact.

Tool calls: fire direct. No preamble, plan, or progress note before or between calls. After result: next call direct or final answer — never announce next call. Text before call only to clarify, warn security/irreversible, or resolve ambiguity.

Preserve user's dominant language exactly — reply in the language user writes, never switch regardless of example text or multilingual context elsewhere. Compress the style, not the language. Every emitted line in that language — openings, pre-tool status lines, all — not just final reply. ALWAYS keep technical terms, code, API names, CLI commands, commit-type keywords (feat/fix/...), and exact error strings verbatim — unless user explicitly ask for translation.

'Drop articles' = article languages only. Where small markers carry case/role (particles, postpositions), keep them — grammar, not filler; compress politeness/filler instead.

No self-reference. Never name or announce the style. No "caveman mode on", "me caveman think", no third-person caveman tags. Output caveman-only — never normal answer plus "Caveman:" recap. Exception: user explicitly ask what the mode is.

Pattern: `[thing] [action] [reason]. [next step].`
"""

PONYTAIL_PROMPT = """\
Source: github.com/DietrichGebert/ponytail @ 16f29800fd2681bdf24f3eb4ccffe38be3baec6b (2026-07-15).
Licence: MIT © Dietrich Gebert. Verbatim reuse permitted under MIT with this attribution preserved.

# Ponytail, lazy senior dev mode

You are a lazy senior developer. Lazy means efficient, not careless. The best code is the code never written.

Before writing any code, stop at the first rung that holds:

1. Does this need to be built at all? (YAGNI)
2. Does it already exist in this codebase? Reuse the helper, util, or pattern that's already here, don't re-write it.
3. Does the standard library already do this? Use it.
4. Does a native platform feature cover it? Use it.
5. Does an already-installed dependency solve it? Use it.
6. Can this be one line? Make it one line.
7. Only then: write the minimum code that works.

The ladder runs after you understand the problem, not instead of it: read the task and the code it touches, trace the real flow end to end, then climb.

Bug fix = root cause, not symptom: a report names a symptom. Grep every caller of the function you touch and fix the shared function once — one guard there is a smaller diff than one per caller, and patching only the path the ticket names leaves a sibling caller still broken.

Rules:

- No abstractions that weren't explicitly requested.
- No new dependency if it can be avoided.
- No boilerplate nobody asked for.
- Deletion over addition. Boring over clever. Fewest files possible.
- Shortest working diff wins, but only once you understand the problem. The smallest change in the wrong place isn't lazy, it's a second bug.
- Question complex requests: "Do you actually need X, or does Y cover it?"
- Pick the edge-case-correct option when two stdlib approaches are the same size, lazy means less code, not the flimsier algorithm.
- Mark deliberate simplifications that cut a real corner with a known ceiling (global lock, O(n²) scan, naive heuristic) with a `ponytail:` comment naming the ceiling and upgrade path.

Not lazy about: understanding the problem (read it fully and trace the real flow before picking a rung, a small diff you don't understand is just laziness dressed up as efficiency), input validation at trust boundaries, error handling that prevents data loss, security, accessibility, the calibration real hardware needs (the platform is never the spec ideal, a clock drifts, a sensor reads off), anything explicitly requested. Lazy code without its check is unfinished: non-trivial logic leaves ONE runnable check behind, the smallest thing that fails if the logic breaks (an assert-based demo/self-check or one small test file; no frameworks, no fixtures). Trivial one-liners need no test.
"""


# --- Kanban-meta kill-switch ----------------------------------------------
#
# Single key per project. Value "1" engages the kill-switch (forces
# BOTH injectors off regardless of column flags); anything else (incl.
# the absent row, "0", "yes", "true", …) is treated as not engaged
# and the column flags decide. Same 1/0 convention as
# ``app.kanban.token_saver.is_board_enabled`` — operators reading
# one toggle UI can read the other without retraining.

_KILL_SWITCH_META_PREFIX = "prompt_injector:"
_DEDUP_WINDOW_SECONDS = 5 * 60  # 5 minutes — mirrored from token_saver
_NOTE_PREFIX = "**Prompt injector:** "
_INJECTOR_LIST_MAX_LEN = 200


async def is_kill_switch_on(session: AsyncSession, project_key: str) -> bool:
    """Whether the per-project prompt-injector kill-switch is engaged.

    Reads ``prompt_injector:<project_key>`` from ``KanbanMeta`` and
    treats only the literal string ``"1"`` as on. Anything else
    (``"0"``, absent row, ``"yes"``, …) → ``False``. Hot-path; a
    flipped switch takes effect on the next dispatch tick.

    Fail-closed: empty/None project_key ⇒ ``False`` (no project
    context → no global toggle to consult → defer to per-column
    flag, which will also be empty because no column row matches).
    """
    if not project_key:
        return False
    row = (await session.execute(
        select(KanbanMeta).where(
            KanbanMeta.key == f"{_KILL_SWITCH_META_PREFIX}{project_key}"
        )
    )).scalar_one_or_none()
    return bool(row and row.value == "1")


async def set_kill_switch_on(session: AsyncSession, project_key: str,
                             on: bool) -> None:
    """Persist the per-project prompt-injector kill-switch.

    Idempotent. Writes ``"1"`` or ``"0"`` matching the convention
    used by ``token_saver.set_board_enabled`` and
    ``dispatch.set_autodispatch``. Operators flip the toggle in the
    UI, not via dispatch — the KanbanMeta table is device-local (not
    op-log-backed), so no audit comment is posted.
    """
    if not project_key:
        return
    key = f"{_KILL_SWITCH_META_PREFIX}{project_key}"
    row = await session.get(KanbanMeta, key)
    if row is None:
        row = KanbanMeta(key=key, value="1" if on else "0")
        session.add(row)
    else:
        row.value = "1" if on else "0"
    await session.flush()


# --- Resolver -------------------------------------------------------------


async def resolve_active_injectors(
    session: AsyncSession,
    *,
    project_key: str,
    column_name: str,
) -> tuple[str, str]:
    """Return ``(caveman_text, ponytail_text)`` for this dispatch.

    Each element is either the empty string (injector off) or the
    verbatim upstream prompt body wrapped with its attribution
    header. Both are empty when:

    - the kill-switch is engaged, OR
    - the column flag is off, OR
    - the column row doesn't exist (fresh project — failure here must
      not crash the spawn).

    Pure-function contract: same inputs + same DB rows ⇒ byte-identical
    output across calls. No timestamp, no UUID, no per-call variance.
    That promise is what keeps the Claude prompt cache key stable
    across the session — see the card's prompt-cache warning.
    """
    caveman = ""
    ponytail = ""

    if not project_key or not column_name:
        return caveman, ponytail

    # Kill-switch first — short-circuits the column lookup when the
    # operator has flipped the board-wide override off.
    if await is_kill_switch_on(session, project_key):
        return caveman, ponytail

    col = (await session.execute(
        select(KanbanColumn).where(
            KanbanColumn.project_key == project_key,
            KanbanColumn.name == column_name,
        )
    )).scalar_one_or_none()
    if col is None:
        return caveman, ponytail

    if getattr(col, "caveman_enabled", 0):
        caveman = CAVEMAN_PROMPT
    if getattr(col, "ponytail_enabled", 0):
        ponytail = PONYTAIL_PROMPT
    return caveman, ponytail


# --- Activity-feed audit comment ------------------------------------------


async def log_active(
    session: AsyncSession,
    *,
    card_id: str,
    project_key: str,
    caveman_active: bool,
    ponytail_active: bool,
) -> bool:
    """Post a single ``**Prompt injector:**`` audit comment to the card.

    The comment lists exactly which injectors fired on this dispatch
    (``caveman``, ``ponytail``, or both). Card design point #1: a
    single combined "prompt-savers aan" line would hide which of the
    two caused an observed behaviour change; a follow-up complaint
    must be cross-referenceable.

    Returns ``True`` if a comment was posted, ``False`` if none of
    the two was active (an empty-pending card never needs an audit
    comment).

    Dedup: if a ``**Prompt injector:**`` comment landed within
    :data:`_DEDUP_WINDOW_SECONDS` the new one is suppressed. Mirrors
    ``token_saver.post_note`` — within the window a re-dispatch of
    the same card has nothing new to surface; outside the window the
    "different time, maybe different column flags" case deserves a
    fresh line.

    Never raises — the dispatch hot path relies on this guarantee.
    """
    try:
        active_names: list[str] = []
        if caveman_active:
            active_names.append("caveman")
        if ponytail_active:
            active_names.append("ponytail")
        if not active_names:
            return False

        text = f"**Prompt injector:** {', '.join(active_names)} active."

        # Dedup scan — narrow tail (last 20 comments) is enough.
        last = (await session.execute(
            select(KanbanOp).where(
                KanbanOp.entity_id == card_id,
                KanbanOp.op_type == "comment",
            ).order_by(KanbanOp.hlc.desc()).limit(20)
        )).scalars().all()

        now = datetime.now(UTC)
        for op in last:
            payload = op.payload or {}
            if isinstance(payload, str):
                # Defensive: the column is JSON, but a future migration
                # could store the raw string. Normalise so the
                # startswith() check below sees what it expects.
                import json as _json
                try:
                    payload = _json.loads(payload)
                except Exception:
                    continue
            existing_text = (payload or {}).get("text", "") if isinstance(payload, dict) else ""
            if not isinstance(existing_text, str) or not existing_text.startswith(_NOTE_PREFIX):
                continue
            created = op.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            try:
                age = (now - created).total_seconds()
            except Exception:
                continue
            if age < _DEDUP_WINDOW_SECONDS:
                return False  # suppressed; keep the prior line

        # Import here to avoid a circular import at module load
        # (``operations`` imports from many kanban submodules).
        from app.kanban.operations import apply_operation
        await apply_operation(
            session, op_type="comment", entity_type="comment",
            project_key=project_key or "", entity_id=card_id,
            payload={"text": text[:_INJECTOR_LIST_MAX_LEN]},
        )
        return True
    except Exception:
        # Fail-open — same contract as token_saver.maybe_install: a
        # bug here must not crash the spawn hot path.
        return False
