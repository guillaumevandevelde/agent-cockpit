"""Auto-dispatch: spawn a Claude session for unclaimed Analysis/Todo cards.

The dispatcher claims a card *as the session that will work it* (claim-before-spawn,
so racing ticks/devices produce exactly one winner), moves it to Doing, then spawns
via a pluggable transport (tmux today, podman later). See docs/cockpit/kanban-dispatch-spec.md.

When hardware-aware memory limits are reached, cards are queued in PendingQueue
and retried automatically when resources become available.
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
from app.services.memory_monitor import get_memory_status_cached

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

_PERSONA_BY_COLUMN = {}  # Dynamic - loaded from project agents


def _persona_filename(column: str) -> Optional[str]:
    """Get the persona filename for a column. For agent columns, the column name IS the agent name."""
    # Fixed columns don't have personas
    if column in ("Backlog", "Dispatch", "Impediment", "Done"):
        return None
    # Agent columns: column name matches agent filename
    return f"{column}.md"


def _resolve_agent_from_persona(persona: Optional[str]) -> Optional[str]:
    """Extract agent name from persona filename (e.g., 'developer.md' -> 'developer')."""
    if persona and persona.endswith(".md"):
        return persona[:-3]
    return None


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

def _mail_section(handle: Optional[str], pending_mail: Optional[Iterable]) -> str:
    if not handle:
        return ""
    lines = [
        "\n\n# Agent Mail",
        f"Je durable handle in dit project is `{handle}`. Check je inbox met "
        "`check_inbox`; vraag gericht context met `request_context`; draag werk over "
        "met `handoff`.",
    ]
    pending = list(pending_mail or [])
    if pending:
        lines.append("\nOpenstaande berichten voor jou op deze card (al gemarkeerd als gelezen):")
        for m in pending:
            label = "Handoff" if m.kind == "handoff" else "Context request"
            lines.append(f"\n**{label} van `{m.from_handle}` — {m.subject}**\n{m.body}")
    return "\n".join(lines)


def build_card_prompt(card, *, persona: Optional[str], ship_mode: str,
                      impediment_question: Optional[str] = None,
                      handle: Optional[str] = None,
                      pending_mail: Optional[Iterable] = None) -> str:
    preamble = (persona.strip() + "\n\n") if persona else ""
    impediment_section = ""
    if impediment_question:
        impediment_section = (
            "\n\n## IMPEDIMENT\n"
            "A previous agent was blocked on this card. Their question:\n"
            f"> {impediment_question}\n\n"
            "Please address this question or clarify what's needed before proceeding.\n"
        )
    return (
        f"{preamble}"
        "You are picking up a Kanban card from the Claude Cockpit board. "
        'It is already claimed by you and moved to "Doing".\n\n'
        f"# {card.title}\n"
        f"{getattr(card, 'description', '') or ''}\n"
        f"{impediment_section}\n"
        f"Ship mode: {ship_mode}\n\n"
        "Work autonomously to completion, following your role instructions above. "
        "Use the `cockpit-kanban` MCP tools (`move_card`, `attach_deliverable`, "
        "`comment`) to update the card exactly as those instructions direct. If you are "
        "blocked or need clarification from another agent, use `status: impediment` with a "
        "clear `question` field explaining what you need."
        f"{_mail_section(handle, pending_mail)}"
    )


def build_dispatch_prompt(card, *, project_path: str, available_agents: list[str]) -> str:
    """Build a prompt for the dispatch agent to triage and route a card."""
    agent_list = ", ".join(available_agents) if available_agents else "developer"
    return (
        "You are the dispatch agent for the Claude Cockpit kanban board. "
        "Your job is to analyze a new card and route it to the most appropriate agent.\n\n"
        f"## Available Agents\n{agent_list}\n\n"
        f"## Card to Route\n"
        f"# {card.title}\n"
        f"{getattr(card, 'description', '') or ''}\n\n"
        "## Instructions\n"
        "1. Analyze the card title and description\n"
        "2. Choose the most appropriate agent from the list above\n"
        "3. Use the `dispatch_to_agent` MCP tool to route the card\n\n"
        "Consider:\n"
        "- What type of work is this? (code, testing, review, analysis, etc.)\n"
        "- Which agent has the right expertise?\n"
        "- If unsure, default to 'developer'\n\n"
        "After dispatching, your session will end automatically."
    )


# ---- transport -------------------------------------------------------------

class SpawnTransport(Protocol):
    def __call__(self, *, directory: str, prompt: str, session_name: str) -> dict: ...


def worktree_transport(*, directory: str, prompt: str, session_name: str) -> dict:
    """Default transport: create a worktree off origin/master, then spawn an
    autonomous (permission-skipping) Claude Code session in it.
    
    Raises MemoryLimitExceeded if hardware memory limits are reached.
    """
    from app.services.agent_bridge.spawn import spawn_session
    from app.services.providers.base import SpawnCommandOptions
    from app.services.scheduling.session_registry import session_registry

    # Check memory limits before spawning
    if not session_registry.can_add_session():
        status = get_memory_status_cached()
        raise MemoryLimitExceeded(
            f"Session limit reached ({session_registry.session_count}/{session_registry.effective_max_sessions}). "
            f"Memory: {status.usage_percent:.0%} used, {status.available_bytes / (1024*1024):.0f}MB available."
        )

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


def _mint_session_name(project_path: str, card_title: str = "") -> str:
    # Keep the whole name <= 20 chars so the tmux-bridge sanitizer never truncates
    # it: a truncated session name would diverge from the claimant label and the
    # worktree branch, breaking cleanup. "k-" + slug(<=13) + "-" + 4 hex = <=20.
    # Prefer card title over project path for clarity.
    source = card_title if card_title else Path(project_path).name
    slug = (_slug(source)[:13].rstrip("-")) or "card"
    return f"k-{slug}-{uuid.uuid4().hex[:4]}"


def _project_is_busy(cards: Iterable[KanbanCard]) -> bool:
    """Check if project has any cards in agent columns (not Backlog, Impediment, or Done)."""
    from app.kanban.schemas import COLUMNS
    return any(
        c.column not in COLUMNS and (c.claimed_by or "").startswith(CLAIMANT_PREFIX)
        for c in cards
    )


def _claimant_session(card: KanbanCard) -> Optional[str]:
    """The tmux session name behind an `agent:` claim, or None for unclaimed cards
    and human (`me@ui`) claims — those are never reaped."""
    claimant = card.claimed_by or ""
    if claimant.startswith(CLAIMANT_PREFIX):
        return claimant[len(CLAIMANT_PREFIX):]
    return None


def _live_sessions() -> Optional[set[str]]:
    """Names of tmux sessions alive on this device, or None when tmux cannot be
    queried. Returning None (not an empty set) on an *ambiguous* failure is the
    whole point: the reaper must never mistake a transient `tmux` hiccup for "every
    session is dead" and release live claims. A clean "no server running" maps to
    an empty set, since that genuinely means zero live sessions."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        if "no server running" in (result.stderr or "").lower():
            return set()
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


_DISPATCH_COLUMNS = ("Backlog", "Dispatch")  # Dispatch from Backlog (new cards) and Dispatch (triage)


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
    impediment_question: Optional[str] = None,
    agent_override: Optional[str] = None,
) -> Optional[dict]:
    """Claim+move-to-agent-column+spawn one specific card. Returns a result dict, or None if
    the claim was lost. The persona honours an explicit per-card agent over the column."""
    source_column = card.column
    name = _mint_session_name(project_path, card.title)
    claimant = CLAIMANT_PREFIX + name

    try:
        await apply_operation(
            session, op_type="claim", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"claimed_by": claimant},
        )
    except ClaimRejected:
        return None  # lost the race; another tick/device took it

    # Handle Dispatch column: spawn dispatch agent for triage
    if source_column == "Dispatch" and not agent_override:
        # Get available agents from project
        agents_dir = Path(project_path) / ".claude" / "agents"
        available_agents = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else ["developer"]
        
        prompt = build_dispatch_prompt(card, project_path=project_path, available_agents=available_agents)
        try:
            spawned = transport(directory=project_path, prompt=prompt, session_name=name)
        except Exception:
            await apply_operation(
                session, op_type="release", entity_type="card", project_key=project_key,
                entity_id=card.id, payload={},
            )
            logger.exception("spawn failed for dispatch card %s in %s", card.id, project_key)
            raise
        
        logger.info("dispatched triage for card %s -> session %s", card.id, name)
        return {"card_id": card.id, "session_name": name, "claimant": claimant,
                "source_column": source_column, "spawned": spawned, "dispatch_agent": True}

    # Determine target agent column: agent_override > card.agent > persona fallback
    if agent_override:
        target_agent = agent_override
    else:
        persona = _persona_for_card(project_path, card, source_column)
        target_agent = getattr(card, "agent", None) or _resolve_agent_from_persona(persona) or "developer"
    
    await apply_operation(
        session, op_type="move", entity_type="card", project_key=project_key,
        entity_id=card.id, payload={"column": target_agent},
    )

    # Register the durable mail identity for this role + its live session, then
    # collect any pending handoff/context mail to warm-start the prompt.
    from app.kanban import mail
    await mail.ensure_identity(session, project_key, target_agent, agent_session=name)
    pending_mail = await mail.pending_for_card(session, project_key, card.id, target_agent)
    for m in pending_mail:
        await mail.mark_read(session, m.id, target_agent)

    # Load persona for the target agent
    persona = _read_persona_file(project_path, f"{target_agent}.md")
    ship_mode = await get_ship_mode(session, project_key)
    prompt = build_card_prompt(card, persona=persona, ship_mode=ship_mode,
        impediment_question=impediment_question, handle=target_agent,
        pending_mail=pending_mail)
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


async def reap_stale_claims(
    session, *, project_key: str, cards: Iterable[KanbanCard], live_sessions: set[str],
) -> int:
    """Release `agent:` claims on cards in agent columns whose tmux session is gone.

    A dispatched session that dies (crash, manual close, reboot) without moving its
    card out of an agent column would otherwise keep `_project_is_busy` True forever, silently
    wedging auto-dispatch for the whole project — the "auto-pick stopped working"
    symptom. Releasing the dead claim frees the per-project cap so the next card is
    picked up; the orphaned card is left in its agent column (now unclaimed) for a human to
    re-rank. Human (`me@ui`) claims are never touched. Returns the number reaped."""
    from app.kanban.schemas import COLUMNS
    reaped = 0
    for card in cards:
        if card.column in COLUMNS:  # Skip fixed columns (Backlog, Impediment, Done)
            continue
        name = _claimant_session(card)
        if name is None or name in live_sessions:
            continue
        await apply_operation(
            session, op_type="release", entity_type="card",
            project_key=project_key, entity_id=card.id, payload={},
        )
        logger.info("reaped stale claim on card %s (dead session %s)", card.id, name)
        reaped += 1
    return reaped


async def dispatch_project(
    session, *, project_key: str, project_path: str, transport: SpawnTransport,
    live_sessions: Optional[set[str]] = None,
) -> Optional[dict]:
    """Claim+move+spawn the next Analysis/Todo card for one project. Returns a result
    dict or None when there is nothing to do (no candidate card, or project is busy).

    When `live_sessions` is provided, stale `agent:` claims on Doing cards whose
    session is no longer alive are reaped first, so a dead session can never wedge
    the busy cap. Passing None skips reaping (used by unit tests that exercise the
    cap directly)."""
    cards = await list_cards(session, project_key)
    if live_sessions is not None:
        if await reap_stale_claims(
            session, project_key=project_key, cards=cards, live_sessions=live_sessions,
        ):
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
    agent_override: Optional[str] = None,
) -> Optional[dict]:
    """Manually dispatch one specific card now, regardless of the auto-pick toggle or
    the busy cap. Returns the result dict, or None if the card is missing or its claim
    was lost. If agent_override is provided, use that agent instead of the card's agent."""
    card = await get_card(session, card_id)
    if card is None:
        return None
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
        agent_override=agent_override,
    )


async def dispatch_impediment_card(
    session, *, card_id: str, project_path: str, target_agent: str,
    impediment_question: str,
    transport: SpawnTransport = worktree_transport,
) -> Optional[dict]:
    """Dispatch an impediment card to a specific agent for resolution.
    
    Args:
        card_id: The ID of the impediment card
        project_path: Path to the project
        target_agent: The agent to dispatch to (analyst, developer, testing, code-review)
        impediment_question: The question that needs to be answered
        transport: The spawn transport to use
    
    Returns:
        Result dict or None if dispatch failed
    """
    card = await get_card(session, card_id)
    if card is None:
        return None
    
    if card.column != "Impediment":
        logger.warning("Card %s is not in Impediment column, cannot dispatch as impediment", card_id)
        return None
    
    # Move card to Doing for the target agent
    await apply_operation(
        session, op_type="move", entity_type="card", project_key=card.project_key,
        entity_id=card.id, payload={"column": "Doing"},
    )
    
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
        impediment_question=impediment_question,
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
    that maps to a local path on this device.
    
    Also retries queued cards that were rejected due to memory limits.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.services.scheduling.pending_queue import pending_queue

    # First, try to retry queued cards if memory is available
    await _retry_queued_cards(transport)

    async with KanbanSessionLocal() as ks:
        enabled = set(await list_autodispatch_projects(ks))
    if not enabled:
        return

    paths = await _registered_project_paths()
    mapping = match_project_paths(enabled, paths)
    if not mapping:
        return

    live_sessions = _live_sessions()  # one tmux query per tick, shared across projects

    for project_key, project_path in mapping.items():
        async with KanbanSessionLocal() as ks:
            try:
                result = await dispatch_project(
                    ks, project_key=project_key, project_path=project_path,
                    transport=transport, live_sessions=live_sessions,
                )
                await ks.commit()
                
                # If dispatch failed due to memory, queue the card for retry
                if result is None:
                    await _maybe_queue_next_card(
                        ks, project_key=project_key, project_path=project_path,
                    )
            except MemoryLimitExceeded as e:
                logger.warning(f"Memory limit reached for {project_key}: {e}")
                await _queue_card_on_memory_limit(
                    ks, project_key=project_key, project_path=project_path,
                )
            except Exception:
                logger.exception("dispatch tick failed for %s", project_key)


class MemoryLimitExceeded(Exception):
    """Raised when a spawn is rejected due to memory limits."""
    pass


async def _retry_queued_cards(transport: SpawnTransport) -> None:
    """Attempt to dispatch queued cards when memory is available."""
    from app.kanban.db import KanbanSessionLocal
    from app.services.scheduling.pending_queue import pending_queue

    status = get_memory_status_cached()
    if status.is_critical:
        return  # Still under memory pressure

    retryable = pending_queue.get_retryable_cards()
    if not retryable:
        return

    logger.info(f"Memory available ({status.usage_percent:.0%} used), retrying {len(retryable)} queued cards")

    for card in retryable:
        try:
            async with KanbanSessionLocal() as ks:
                card_data = await get_card(ks, card.card_id)
                if card_data is None:
                    logger.info(f"Card {card.card_id} no longer exists, removing from queue")
                    pending_queue.dequeue(card.card_id)
                    continue

                # Check if card is still in a dispatchable state
                if card_data.column not in ("Backlog",) or card_data.claimed_by:
                    logger.info(f"Card {card.card_id} is no longer dispatchable, removing from queue")
                    pending_queue.dequeue(card.card_id)
                    continue

                result = await _run_card(
                    ks, card=card_data, project_key=card.project_key,
                    project_path=card.project_path, transport=transport,
                    agent_override=card.agent_override,
                    impediment_question=card.impediment_question,
                )
                await ks.commit()

                if result is not None:
                    pending_queue.dequeue(card.card_id)
                    logger.info(f"Successfully dispatched queued card {card.card_id}")
                else:
                    pending_queue.mark_retry(card.card_id)
        except Exception as e:
            logger.exception(f"Retry failed for card {card.card_id}: {e}")
            pending_queue.mark_retry(card.card_id)


async def _maybe_queue_next_card(
    session, *, project_key: str, project_path: str,
) -> None:
    """Check if we should queue the next card for this project."""
    status = get_memory_status_cached()
    if not status.is_warning:
        return  # Memory is fine, no need to queue

    # Get the next card that would have been dispatched
    cards = await list_cards(session, project_key)
    next_card = _next_card(cards)
    if next_card is None:
        return

    from app.services.scheduling.pending_queue import pending_queue
    pending_queue.enqueue(
        card_id=next_card.id,
        project_key=project_key,
        project_path=project_path,
    )


async def _queue_card_on_memory_limit(
    session, *, project_key: str, project_path: str,
) -> None:
    """Queue the next card when memory limit is reached."""
    cards = await list_cards(session, project_key)
    next_card = _next_card(cards)
    if next_card is None:
        return

    from app.services.scheduling.pending_queue import pending_queue
    pending_queue.enqueue(
        card_id=next_card.id,
        project_key=project_key,
        project_path=project_path,
    )


async def _registered_project_paths() -> list[str]:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.database import Project

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Project.path))).scalars().all()
    return list(rows)
