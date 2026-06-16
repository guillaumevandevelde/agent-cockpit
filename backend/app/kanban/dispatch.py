"""Auto-dispatch: spawn a Claude session for unclaimed Todo cards.

The dispatcher claims a card *as the session that will work it* (claim-before-spawn,
so racing ticks/devices produce exactly one winner), moves it to Doing, then spawns
via a pluggable transport (tmux today, podman later). See docs/cockpit/kanban-dispatch-spec.md.
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol

from app.kanban.models import KanbanCard, KanbanMeta
from app.kanban.operations import ClaimRejected, apply_operation
from app.kanban.project_key import resolve_project_key
from app.kanban.service import list_cards

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
    path = Path(project_path) / ".claude" / "agents" / filename
    try:
        return _strip_frontmatter(path.read_text()).strip()
    except OSError:
        return None


# ---- prompt ----------------------------------------------------------------

def build_card_prompt(card: KanbanCard) -> str:
    return (
        "You are an agent picking up a Kanban card from the Claude Cockpit board.\n"
        "The card is already claimed by you and moved to \"Doing\".\n\n"
        f"# {card.title}\n"
        f"{card.description or ''}\n\n"
        "When finished: use the `cockpit-kanban` MCP tools to move the card to "
        '"Review" (`move_card`) and attach your result (`attach_deliverable`, e.g. a '
        "branch or PR URL). If you cannot complete it, leave a `comment` explaining why."
    )


# ---- transport -------------------------------------------------------------

class SpawnTransport(Protocol):
    def __call__(self, *, directory: str, prompt: str, session_name: str) -> dict: ...


def tmux_transport(*, directory: str, prompt: str, session_name: str) -> dict:
    """Default transport: spawn a Claude Code worktree session in tmux."""
    from app.services.agent_bridge.spawn import spawn_session
    from app.services.providers.base import SpawnCommandOptions

    options = SpawnCommandOptions(
        directory=directory, mode="worktree", worktree_name=session_name,
        prompt=prompt, skip_permissions=False,
    )
    return spawn_session("claude-code", options, session_name=session_name)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "project"


def _mint_session_name(project_path: str) -> str:
    return f"k-{_slug(Path(project_path).name)}-{uuid.uuid4().hex[:4]}"


def _project_is_busy(cards: Iterable[KanbanCard]) -> bool:
    return any(
        c.column == "Doing" and (c.claimed_by or "").startswith(CLAIMANT_PREFIX)
        for c in cards
    )


# ---- core ------------------------------------------------------------------

async def dispatch_project(
    session, *, project_key: str, project_path: str, transport: SpawnTransport,
) -> Optional[dict]:
    """Claim+move+spawn the next Todo card for one project. Returns a result dict
    or None when there is nothing to do (no Todo card, or the project is busy)."""
    cards = await list_cards(session, project_key)
    if _project_is_busy(cards):
        return None

    todo = [c for c in cards if c.column == "Todo" and not c.claimed_by]
    if not todo:
        return None
    card = todo[0]  # list_cards is ordered by rank

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

    prompt = build_card_prompt(card)
    try:
        spawned = transport(directory=project_path, prompt=prompt, session_name=name)
    except Exception:
        # compensate: release the claim and return the card to Todo so it is reusable
        await apply_operation(
            session, op_type="release", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={},
        )
        await apply_operation(
            session, op_type="move", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"column": "Todo"},
        )
        logger.exception("spawn failed for card %s in %s", card.id, project_key)
        raise

    logger.info("dispatched card %s -> session %s", card.id, name)
    return {"card_id": card.id, "session_name": name, "claimant": claimant, "spawned": spawned}


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


async def run_dispatch_tick(*, transport: SpawnTransport = tmux_transport) -> None:
    """One poll cycle: dispatch the next Todo card for every enabled project that
    maps to a local path on this device."""
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
