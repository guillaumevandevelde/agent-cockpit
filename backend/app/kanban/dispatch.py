"""Auto-dispatch: spawn a Claude session for unclaimed Analysis/Todo cards.

The dispatcher claims a card *as the session that will work it* (claim-before-spawn,
so racing ticks/devices produce exactly one winner), moves it to Doing, then spawns
via a pluggable transport (tmux today, podman later). See docs/cockpit/kanban-dispatch-spec.md.
"""
from __future__ import annotations

import logging
import re
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol

from app.kanban.models import KanbanCard, KanbanMeta
from app.kanban.operations import ClaimRejected, apply_operation
from app.kanban.project_key import resolve_project_key
from app.kanban.service import get_card, list_cards

logger = logging.getLogger(__name__)

META_PREFIX = "autodispatch:"
CLAIMANT_PREFIX = "agent:"
SHIPMODE_PREFIX = "shipmode:"
SHIP_MODES = ("pull-request", "direct")
DEFAULT_SHIP_MODE = "pull-request"


# ---- enablement: device-local, stored in KanbanMeta (not part of the op-log) ----

async def is_autodispatch_enabled(session, project_key: str) -> bool:
    row = await session.get(KanbanMeta, META_PREFIX + project_key)
    return bool(row and row.value == "1")


async def set_autodispatch(session, project_key: str, enabled: bool) -> None:
    key = META_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        row = KanbanMeta(key=key, value="1" if enabled else "0")
        session.add(row)
    else:
        row.value = "1" if enabled else "0"
    await session.flush()


async def list_autodispatch_projects(session) -> list[str]:
    from sqlalchemy import select
    rows = (await session.execute(select(KanbanMeta))).scalars().all()
    return [
        r.key[len(META_PREFIX):]
        for r in rows
        if r.key.startswith(META_PREFIX) and r.value == "1"
    ]


async def get_ship_mode(session, project_key: str) -> str:
    row = await session.get(KanbanMeta, SHIPMODE_PREFIX + project_key)
    if row and row.value in SHIP_MODES:
        return row.value
    return DEFAULT_SHIP_MODE


async def set_ship_mode(session, project_key: str, mode: str) -> None:
    if mode not in SHIP_MODES:
        raise ValueError(f"unknown ship mode: {mode}")
    key = SHIPMODE_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=mode))
    else:
        row.value = mode
    await session.flush()


# ---- persona helpers -------------------------------------------------------

_PERSONA_BY_COLUMN = {
    "Analysis": "kanban-analyst.md",
    "Todo": "kanban-developer.md",
}


def _persona_filename(column: str) -> Optional[str]:
    return _PERSONA_BY_COLUMN.get(column)


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + len("\n---\n"):]
    return text


def _read_persona(project_path: str, column: str) -> Optional[str]:
    filename = _persona_filename(column)
    if not filename:
        return None
    return _read_persona_file(project_path, filename)


def _read_persona_file(project_path: str, filename: str) -> Optional[str]:
    path = Path(project_path) / ".claude" / "agents" / filename
    try:
        return _strip_frontmatter(path.read_text()).strip()
    except OSError:
        return None


def _persona_for_card(project_path: str, card, column: str) -> Optional[str]:
    """An explicit per-card agent overrides the column default. `column` is passed
    explicitly because the card may already have been moved to "Doing" by the caller."""
    agent = getattr(card, "agent", None)
    if agent:
        return _read_persona_file(project_path, f"{agent}.md")
    return _read_persona(project_path, column)


# ---- prompt ----------------------------------------------------------------

def build_card_prompt(card, *, persona: Optional[str], ship_mode: str) -> str:
    preamble = (persona.strip() + "\n\n") if persona else ""
    return (
        f"{preamble}"
        "You are picking up a Kanban card from the Claude Cockpit board. "
        'It is already claimed by you and moved to "Doing".\n\n'
        f"# {card.title}\n"
        f"{getattr(card, 'description', '') or ''}\n\n"
        f"Ship mode: {ship_mode}\n\n"
        "Work autonomously to completion, following your role instructions above. "
        "Use the `cockpit-kanban` MCP tools (`move_card`, `attach_deliverable`, "
        "`comment`) to update the card exactly as those instructions direct. If you are "
        "blocked or your tests fail, `comment` explaining why and leave the card in "
        '"Doing".'
    )


# ---- transport -------------------------------------------------------------

class SpawnTransport(Protocol):
    def __call__(self, *, directory: str, prompt: str, session_name: str) -> dict: ...


def worktree_transport(*, directory: str, prompt: str, session_name: str) -> dict:
    """Default transport: create a worktree off origin/master, then spawn an
    autonomous (permission-skipping) Claude Code session in it."""
    from app.services.agent_bridge.spawn import spawn_session
    from app.services.providers.base import SpawnCommandOptions

    repo = directory
    worktree_path = str(Path(repo) / ".claude" / "worktrees" / session_name)

    subprocess.run(["git", "-C", repo, "fetch", "origin"],
                   capture_output=True, text=True, timeout=60, check=True)
    subprocess.run(
        ["git", "-C", repo, "worktree", "add", "-b", session_name,
         worktree_path, "origin/master"],
        capture_output=True, text=True, timeout=60, check=True)

    options = SpawnCommandOptions(
        directory=worktree_path, mode="plain", prompt=prompt,
        skip_permissions=True, worktree_path=worktree_path, repo_path=repo,
    )
    try:
        return spawn_session("claude-code", options, session_name=session_name)
    except Exception:
        # the worktree exists but no session owns it; remove it so it isn't orphaned
        subprocess.run(["git", "-C", repo, "worktree", "remove", worktree_path, "--force"],
                       capture_output=True, text=True, timeout=30)
        raise


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "project"


def _mint_session_name(project_path: str) -> str:
    # Keep the whole name <= 20 chars so the tmux-bridge sanitizer never truncates
    # it: a truncated session name would diverge from the claimant label and the
    # worktree branch, breaking cleanup. "k-" + slug(<=13) + "-" + 4 hex = <=20.
    slug = (_slug(Path(project_path).name)[:13].rstrip("-")) or "project"
    return f"k-{slug}-{uuid.uuid4().hex[:4]}"


def _project_is_busy(cards: Iterable[KanbanCard]) -> bool:
    return any(
        c.column == "Doing" and (c.claimed_by or "").startswith(CLAIMANT_PREFIX)
        for c in cards
    )


_DISPATCH_COLUMNS = ("Todo", "Analysis")  # Todo drains first, then Analysis


def _next_card(cards: Iterable[KanbanCard]) -> Optional[KanbanCard]:
    cards = list(cards)
    for col in _DISPATCH_COLUMNS:
        col_cards = [c for c in cards if c.column == col and not c.claimed_by]
        if col_cards:
            return col_cards[0]  # list_cards is ordered by rank
    return None


# ---- core ------------------------------------------------------------------

async def _run_card(
    session, *, card, project_key: str, project_path: str, transport: SpawnTransport,
) -> Optional[dict]:
    """Claim+move-to-Doing+spawn one specific card. Returns a result dict, or None if
    the claim was lost. The persona honours an explicit per-card agent over the column."""
    source_column = card.column
    name = _mint_session_name(project_path)
    claimant = CLAIMANT_PREFIX + name

    try:
        await apply_operation(
            session, op_type="claim", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"claimed_by": claimant},
        )
    except ClaimRejected:
        return None  # lost the race; another tick/device took it

    await apply_operation(
        session, op_type="move", entity_type="card", project_key=project_key,
        entity_id=card.id, payload={"column": "Doing"},
    )

    persona = _persona_for_card(project_path, card, source_column)
    ship_mode = await get_ship_mode(session, project_key)
    prompt = build_card_prompt(card, persona=persona, ship_mode=ship_mode)
    try:
        spawned = transport(directory=project_path, prompt=prompt, session_name=name)
    except Exception:
        await apply_operation(
            session, op_type="release", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={},
        )
        await apply_operation(
            session, op_type="move", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"column": source_column},
        )
        logger.exception("spawn failed for card %s in %s", card.id, project_key)
        raise

    logger.info("dispatched card %s (%s) -> session %s", card.id, source_column, name)
    return {"card_id": card.id, "session_name": name, "claimant": claimant,
            "source_column": source_column, "spawned": spawned}


async def dispatch_project(
    session, *, project_key: str, project_path: str, transport: SpawnTransport,
) -> Optional[dict]:
    """Claim+move+spawn the next Analysis/Todo card for one project. Returns a result
    dict or None when there is nothing to do (no candidate card, or project is busy)."""
    cards = await list_cards(session, project_key)
    if _project_is_busy(cards):
        return None

    card = _next_card(cards)
    if card is None:
        return None
    return await _run_card(
        session, card=card, project_key=project_key,
        project_path=project_path, transport=transport,
    )


async def dispatch_card(
    session, *, card_id: str, project_path: str,
    transport: SpawnTransport = worktree_transport,
) -> Optional[dict]:
    """Manually dispatch one specific card now, regardless of the auto-pick toggle or
    the busy cap. Returns the result dict, or None if the card is missing or its claim
    was lost."""
    card = await get_card(session, card_id)
    if card is None:
        return None
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
    )


# ---- project_key -> local path --------------------------------------------

def match_project_paths(
    project_keys: set[str],
    project_paths: Iterable[str],
    *,
    key_of: Callable[[str], str] = resolve_project_key,
) -> dict[str, str]:
    """Map each enabled board key to a local path on this device, by computing the
    project key of each registered path. First match wins."""
    out: dict[str, str] = {}
    for path in project_paths:
        try:
            key = key_of(path)
        except Exception:
            continue
        if key in project_keys and key not in out:
            out[key] = path
    return out


async def run_dispatch_tick(*, transport: SpawnTransport = worktree_transport) -> None:
    """One poll cycle: dispatch the next Analysis/Todo card for every enabled project
    that maps to a local path on this device."""
    from app.kanban.db import KanbanSessionLocal

    async with KanbanSessionLocal() as ks:
        enabled = set(await list_autodispatch_projects(ks))
    if not enabled:
        return

    paths = await _registered_project_paths()
    mapping = match_project_paths(enabled, paths)

    for project_key, project_path in mapping.items():
        async with KanbanSessionLocal() as ks:
            try:
                await dispatch_project(
                    ks, project_key=project_key, project_path=project_path,
                    transport=transport,
                )
                await ks.commit()
            except Exception:
                logger.exception("dispatch tick failed for %s", project_key)


async def _registered_project_paths() -> list[str]:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.database import Project

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Project.path))).scalars().all()
    return list(rows)
