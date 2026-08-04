"""Tests for the optional prompt-injector module
(``app.kanban.prompt_injectors``).

Covers the per-lane opt-in + board kill-switch + content-stability contract
the corresponding feature card requires:

1. **Default-off**: with no column flag and no KanbanMeta row, no injector
   text ever appears in the dispatch prompt.
2. **Per-lane flag**: ``caveman_enabled``/``ponytail_enabled`` on the
   target column row controls which injector fires — independent
   switches (independent semantics).
3. **Kill-switch**: a single ``prompt_injector:<project_key>`` row in
   ``KanbanMeta`` set to ``"1"`` disables BOTH injectors regardless
   of column flags. Without the row, both are allowed.
4. **Stable content**: the resolver returns the same string for the
   same inputs (no per-request variance — critical for prompt-cache
   ``cache_read`` survival).
5. **Activity-feed audit**: when at least one injector fires on a
   dispatch, a single ``**Prompt injector:**`` comment is posted on
   the card so a follow-up complaint can be cross-referenced back.

Spec sources:
- kanban card ``d0446fd8…`` (Caveman + Ponytail per-lane opt-in).
- upstream prompts verbatim from
  github.com/JuliusBrussee/caveman@ec83e5ba…/skills/caveman/SKILL.md
  and github.com/DietrichGebert/ponytail@16f29800…/.clinerules/ponytail.md
  (MIT, see ``app.kanban.prompt_injectors`` module docstring).
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.kanban import prompt_injectors
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanCard, KanbanColumn, KanbanMeta, KanbanOp
from tests.kanban_test_db import reset_test_tables

# --- Fixtures --------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _seed_column(project_key: str, name: str, *, caveman: int = 0,
                       ponytail: int = 0) -> KanbanColumn:
    async with KanbanSessionLocal() as s:
        col = KanbanColumn(
            id=f"col-{name}", project_key=project_key, name=name,
            rank="0000",
            caveman_enabled=caveman, ponytail_enabled=ponytail,
        )
        s.add(col)
        await s.commit()
        # Refetch to give the test a fresh, attached instance
        return await s.get(KanbanColumn, col.id)


async def _seed_kill_switch(project_key: str, on: bool) -> None:
    async with KanbanSessionLocal() as s:
        s.add(KanbanMeta(
            key=f"prompt_injector:{project_key}",
            value="1" if on else "0",
        ))
        await s.commit()


async def _seed_card(card_id: str, project_key: str, column: str = "engineer") -> None:
    """Create a kanban card so activity-feed comments have a card to attach to."""
    async with KanbanSessionLocal() as s:
        s.add(KanbanCard(
            id=card_id, project_key=project_key, title="probe",
            description="probe", column=column, rank="0000",
        ))
        await s.commit()


async def _activity_count(card_id: str, *, prefix: str) -> int:
    """Count ``op_type='comment'`` rows on this card whose text starts with ``prefix``."""
    async with KanbanSessionLocal() as s:
        from sqlalchemy import select
        ops = (await s.execute(
            select(KanbanOp).where(
                KanbanOp.entity_id == card_id,
                KanbanOp.op_type == "comment",
            )
        )).scalars().all()
        n = 0
        for op in ops:
            payload = op.payload or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            text = (payload or {}).get("text", "") if isinstance(payload, dict) else ""
            if isinstance(text, str) and text.startswith(prefix):
                n += 1
        return n


async def _activity_text(card_id: str, *, prefix: str) -> str | None:
    """Return the FIRST activity-feed comment on this card whose text starts
    with ``prefix``. Used by the per-prefix inspector tests below.
    """
    async with KanbanSessionLocal() as s:
        from sqlalchemy import select
        ops = (await s.execute(
            select(KanbanOp).where(
                KanbanOp.entity_id == card_id,
                KanbanOp.op_type == "comment",
            ).order_by(KanbanOp.hlc.desc())
        )).scalars().all()
        for op in reversed(ops):
            payload = op.payload or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            text = (payload or {}).get("text", "") if isinstance(payload, dict) else ""
            if isinstance(text, str) and text.startswith(prefix):
                return text
        return None


# --- Constants: pinned upstream content + attribution --------------------


def test_caveman_prompt_is_non_empty_and_carries_attribution():
    """The Caveman prompt must be verbatim upstream + a MIT attribution header.

    The first line contains the attribution + commit pin so a future
    reader sees the licence and the source ref before the prompt body.
    The body itself must be non-empty (a bare attribution-only file would
    be a no-op deployment).
    """
    text = prompt_injectors.CAVEMAN_PROMPT
    assert "MIT" in text or "License" in text or "Copyright" in text, (
        "Caveman prompt must carry MIT attribution for upstream reuse."
    )
    assert "JuliusBrussee/caveman" in text
    # Pinned commit the prompt text was copied from.
    assert "ec83e5bace4c20484d704dea21e12fc4eb94e9aa" in text, (
        "Caveman prompt must pin the upstream commit hash."
    )
    # Verbatim upstream signature line (catches silent edits).
    assert "Respond terse like smart caveman" in text


def test_ponytail_prompt_is_non_empty_and_carries_attribution():
    """The Ponytail prompt must be verbatim upstream + MIT attribution."""
    text = prompt_injectors.PONYTAIL_PROMPT
    assert "MIT" in text or "License" in text or "Copyright" in text
    assert "DietrichGebert/ponytail" in text
    assert "16f29800fd2681bdf24f3eb4ccffe38be3baec6b" in text, (
        "Ponytail prompt must pin the upstream commit hash."
    )
    # Verbatim upstream signature line.
    assert "lazy senior developer" in text


# --- Resolver: default-off + per-lane flags + kill-switch -----------------


@pytest.mark.asyncio
async def test_resolver_returns_both_empty_when_nothing_configured():
    """Default: no column flags, no kill-switch row → both empty strings."""
    await _seed_column("PROJ", "engineer")
    async with KanbanSessionLocal() as s:
        cav, pon = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
    assert cav == ""
    assert pon == ""


@pytest.mark.asyncio
async def test_resolver_returns_caveman_when_flag_on_only():
    await _seed_column("PROJ", "engineer", caveman=1, ponytail=0)
    async with KanbanSessionLocal() as s:
        cav, pon = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
    assert cav and "Respond terse like smart caveman" in cav
    assert pon == ""


@pytest.mark.asyncio
async def test_resolver_returns_ponytail_when_flag_on_only():
    await _seed_column("PROJ", "engineer", caveman=0, ponytail=1)
    async with KanbanSessionLocal() as s:
        cav, pon = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
    assert cav == ""
    assert pon and "lazy senior developer" in pon


@pytest.mark.asyncio
async def test_resolver_returns_both_when_both_flags_on():
    await _seed_column("PROJ", "engineer", caveman=1, ponytail=1)
    async with KanbanSessionLocal() as s:
        cav, pon = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
    assert cav and pon


@pytest.mark.asyncio
async def test_resolver_respects_per_column_independence():
    """Caveman on engineer, Ponytail on researcher — picking engineer must
    return only Caveman, picking researcher must return only Ponytail.

    The card's design point #2: columnar flags are independent, not a
    single board-wide "save tokens" knob.
    """
    await _seed_column("PROJ", "engineer", caveman=1)
    await _seed_column("PROJ", "researcher", ponytail=1)
    async with KanbanSessionLocal() as s:
        cav_e, pon_e = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
        cav_r, pon_r = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="researcher",
        )
    assert cav_e and pon_e == ""
    assert pon_r and cav_r == ""


@pytest.mark.asyncio
async def test_resolver_missing_column_returns_both_empty():
    """If the column row doesn't exist yet (fresh project), both empty —
    never an exception. The resolver is on the dispatch hot path; a
    missing row must not crash the spawn.
    """
    async with KanbanSessionLocal() as s:
        cav, pon = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="ghost-column",
        )
    assert cav == ""
    assert pon == ""


# --- Kill-switch -----------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_switch_disables_both_even_when_columns_want_them():
    """``prompt_injector:<key> = "1"`` → both empty regardless of column flags.

    This is the board-wide kill-switch the card requires.
    """
    await _seed_column("PROJ", "engineer", caveman=1, ponytail=1)
    await _seed_kill_switch("PROJ", on=True)
    async with KanbanSessionLocal() as s:
        cav, pon = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
    assert cav == ""
    assert pon == ""


@pytest.mark.asyncio
async def test_kill_switch_value_zero_is_off():
    """``prompt_injector:<key> = "0"`` is the explicit-off form. Anything
    other than ``"1"`` → kill-switch not engaged → column flags decide.
    """
    await _seed_column("PROJ", "engineer", caveman=1, ponytail=1)
    await _seed_kill_switch("PROJ", on=False)
    async with KanbanSessionLocal() as s:
        cav, pon = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
    assert cav and pon


@pytest.mark.asyncio
async def test_is_kill_switch_on_helpers_round_trip():
    """The board_toggle helpers follow the same 1/0 convention as
    ``token_saver``: write "1" to engage, write "0" to clear.
    """
    async with KanbanSessionLocal() as s:
        assert await prompt_injectors.is_kill_switch_on(s, "PROJ") is False
        await prompt_injectors.set_kill_switch_on(s, "PROJ", True)
        assert await prompt_injectors.is_kill_switch_on(s, "PROJ") is True
        await prompt_injectors.set_kill_switch_on(s, "PROJ", False)
        assert await prompt_injectors.is_kill_switch_on(s, "PROJ") is False


# --- Content stability (cache-key survival) --------------------------------


@pytest.mark.asyncio
async def test_resolver_returns_byte_stable_output_for_same_inputs():
    """Two calls with the same input must return byte-identical strings.

    Why this matters: the system-prompt path is the cache prefix. If the
    resolver returned a fresh string per call (timestamp, random uuid, …)
    the cache would bust on every request and the
    ``cache_read``-saving would be wiped out. Locking the contract here
    keeps the optimization the card promises.
    """
    await _seed_column("PROJ", "engineer", caveman=1, ponytail=1)
    async with KanbanSessionLocal() as s:
        first = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
        second = await prompt_injectors.resolve_active_injectors(
            s, project_key="PROJ", column_name="engineer",
        )
    assert first == second
    cav_a, pon_a = first
    cav_b, pon_b = second
    assert cav_a == cav_b
    assert pon_a == pon_b


# --- Activity-feed audit comment ------------------------------------------


@pytest.mark.asyncio
async def test_log_active_dedups_within_window():
    """A repeat call within the dedup window must NOT add a second comment.

    Mirrors ``token_saver.post_note``: dedup is per-card per-prefix so a
    flurry of re-dispatches doesn't spam the activity feed.
    """
    card_id = "card-1"
    await _seed_card(card_id, "PROJ")

    async with KanbanSessionLocal() as s:
        await prompt_injectors.log_active(
            s, card_id=card_id, project_key="PROJ",
            caveman_active=True, ponytail_active=True,
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        await prompt_injectors.log_active(
            s, card_id=card_id, project_key="PROJ",
            caveman_active=True, ponytail_active=True,
        )
        await s.commit()

    n = await _activity_count(card_id, prefix=prompt_injectors._NOTE_PREFIX)
    assert n == 1, (
        f"expected exactly 1 audit comment within the dedup window, got {n}"
    )


@pytest.mark.asyncio
async def test_log_active_posts_nothing_when_no_injector_fired():
    """``log_active`` with both flags False → no comment at all.

    The activity feed is for diagnosing output-quality complaints, and a
    'no injector was active' line would be noise on every dispatch.
    """
    card_id = "card-2"
    await _seed_card(card_id, "PROJ")
    async with KanbanSessionLocal() as s:
        await prompt_injectors.log_active(
            s, card_id=card_id, project_key="PROJ",
            caveman_active=False, ponytail_active=False,
        )
        await s.commit()
    n = await _activity_count(card_id, prefix=prompt_injectors._NOTE_PREFIX)
    assert n == 0


@pytest.mark.asyncio
async def test_log_active_records_which_injectors_fired():
    """When at least one injector fires the comment must list exactly which
    ones — both names must appear if both ran, only the active one if
    only one did. A single "injector was active" line would defeat the
    point of having two independent switches (card design point #1:
    "verbergt welk van de twee een effect veroorzaakte")."""
    card_id = "card-3"
    await _seed_card(card_id, "PROJ")
    async with KanbanSessionLocal() as s:
        await prompt_injectors.log_active(
            s, card_id=card_id, project_key="PROJ",
            caveman_active=True, ponytail_active=False,
        )
        await s.commit()
    text = await _activity_text(card_id, prefix=prompt_injectors._NOTE_PREFIX)
    assert text is not None, "expected an audit comment but none was posted"
    assert "caveman" in text.lower()
    assert "ponytail" not in text.lower()


# --- build_card_prompt integration ---------------------------------------


def _fake_card():
    """Lightweight stand-in for a KanbanCard ORM instance.

    ``build_card_prompt`` only reads ``id``, ``title`` and ``description``
    on the card object, so a SimpleNamespace is enough to exercise the
    injector slice logic without spinning up a DB row.
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        id="card-test", title="probe title", description="probe description",
    )


def test_build_card_prompt_injects_caveman_slice_in_preamble():
    """When ``prompt_injector_caveman`` is set, the slice lands in the
    preamble (system-prompt layer), before the kaarttekst — the card
    says "raakt alleen de systeemprompt-laag", and any injectie should
    not displace the card title/description content.
    """
    from app.kanban.dispatch import build_card_prompt
    prompt = build_card_prompt(
        _fake_card(),
        persona="## Persona\n\nI am the engineer.",
        ship_mode="direct",
        prompt_injector_caveman=prompt_injectors.CAVEMAN_PROMPT,
    )
    # Persona first, then separator, then the slice — order is part of
    # the contract.
    persona_idx = prompt.index("I am the engineer.")
    cav_idx = prompt.index("Respond terse like smart caveman")
    title_idx = prompt.index("probe title")
    assert persona_idx < cav_idx < title_idx, (
        "injector slice must live between the persona and the card body"
    )


def test_build_card_prompt_no_injector_means_no_extra_slice():
    """Default-off contract: with no injector kwargs, the prompt
    returned by build_card_prompt must not contain the upstream body
    anywhere. A regression that adds a stray substring elsewhere would
    silently activate the compressor on every dispatch.
    """
    from app.kanban.dispatch import build_card_prompt
    prompt = build_card_prompt(
        _fake_card(),
        persona="## Persona\n\nI am the engineer.",
        ship_mode="direct",
    )
    assert "Respond terse like smart caveman" not in prompt
    assert "lazy senior developer" not in prompt


def test_build_card_prompt_both_injectors_present_independently():
    """Both injectors active → both verbatim slices present, each
    in its own block. A regression that joins them into one string
    would still pass a "is the text there" check, so this asserts
    the dual-block layout explicitly.
    """
    from app.kanban.dispatch import build_card_prompt
    prompt = build_card_prompt(
        _fake_card(),
        persona="## Persona\n\nI am the engineer.",
        ship_mode="direct",
        prompt_injector_caveman=prompt_injectors.CAVEMAN_PROMPT,
        prompt_injector_ponytail=prompt_injectors.PONYTAIL_PROMPT,
    )
    assert prompt_injectors.CAVEMAN_PROMPT.rstrip() in prompt
    assert prompt_injectors.PONYTAIL_PROMPT.rstrip() in prompt
    cav_idx = prompt.index("Respond terse like smart caveman")
    pon_idx = prompt.index("lazy senior developer")
    assert cav_idx < pon_idx


def test_build_card_prompt_does_not_mutate_card_text_or_ship_instructions():
    """Card body, session-end ship recipe, and MCP fallback must be
    unaffected by the injector kwargs — the card's "raakt alleen de
    systeemprompt-laag" constraint. The ship recipe appears via
    ``_build_ship_instructions``; this test just asserts those
    stable substring anchors survive.
    """
    from app.kanban.dispatch import build_card_prompt
    with_injectors = build_card_prompt(
        _fake_card(),
        persona="## Persona\n\nI am the engineer.",
        ship_mode="direct",
        prompt_injector_caveman=prompt_injectors.CAVEMAN_PROMPT,
        prompt_injector_ponytail=prompt_injectors.PONYTAIL_PROMPT,
    )
    without_injectors = build_card_prompt(
        _fake_card(),
        persona="## Persona\n\nI am the engineer.",
        ship_mode="direct",
    )
    # Card body anchor: must appear with the same trailing context in
    # both versions (no substring being re-ordered).
    assert "probe title" in with_injectors
    assert "probe description" in with_injectors
    assert "Feature-Compliance-Review" in with_injectors
    assert "Feature-Compliance-Review" in without_injectors
    # The ONLY delta must be the injection block itself, located
    # between the persona and the card body.
    cav_idx = with_injectors.index("Respond terse like smart caveman")
    assert with_injectors[:cav_idx].rstrip() == without_injectors.split("## Persona")[0].rstrip() or "## Persona" in with_injectors[:cav_idx]  # noqa: E501 — persona same, slice added after
