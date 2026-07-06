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
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.kanban.models import KanbanCard, KanbanMeta
from app.kanban.operations import ClaimRejected, apply_operation
from app.kanban.project_key import resolve_project_key
from app.kanban.service import get_card, list_cards
from app.services.memory_monitor import get_memory_status_cached

logger = logging.getLogger(__name__)

META_PREFIX = "autodispatch:"
CLAIMANT_PREFIX = "agent:"
SHIPMODE_PREFIX = "shipmode:"
SKIP_PERMISSIONS_PREFIX = "skip_permissions:"
SHIP_MODES = ("pull-request", "direct")
DEFAULT_SHIP_MODE = "pull-request"
MAX_SESSIONS_PREFIX = "max_sessions:"
# Conservative default for a shared box: the pre-push gate now serializes the heavy
# test/build across sessions, so this mainly bounds concurrent agent processes.
# Override per-project via set_max_sessions.
DEFAULT_MAX_SESSIONS = 3
TRANSPORT_PREFIX = "transport:"
TRANSPORTS = ("worktree", "sandcastle")
DEFAULT_TRANSPORT = "worktree"


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


async def get_skip_permissions(session, project_key: str) -> bool:
    row = await session.get(KanbanMeta, SKIP_PERMISSIONS_PREFIX + project_key)
    if row is None:
        return True  # default: bypass permissions (existing behaviour)
    return row.value == "1"


async def set_skip_permissions(session, project_key: str, enabled: bool) -> None:
    key = SKIP_PERMISSIONS_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        row = KanbanMeta(key=key, value="1" if enabled else "0")
        session.add(row)
    else:
        row.value = "1" if enabled else "0"
    await session.flush()


async def get_max_sessions(session, project_key: str) -> int:
    row = await session.get(KanbanMeta, MAX_SESSIONS_PREFIX + project_key)
    if row is None:
        return DEFAULT_MAX_SESSIONS
    try:
        n = int(row.value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_SESSIONS
    return n if n >= 1 else DEFAULT_MAX_SESSIONS


async def set_max_sessions(session, project_key: str, n: int) -> None:
    if n < 1:
        raise ValueError("max_sessions must be >= 1")
    key = MAX_SESSIONS_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=str(n)))
    else:
        row.value = str(n)
    await session.flush()


async def get_default_transport(session, project_key: str) -> str:
    row = await session.get(KanbanMeta, TRANSPORT_PREFIX + project_key)
    if row and row.value in TRANSPORTS:
        return row.value
    return DEFAULT_TRANSPORT


async def set_default_transport(session, project_key: str, value: str) -> None:
    if value not in TRANSPORTS:
        raise ValueError(f"unknown transport: {value}")
    key = TRANSPORT_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=value))
    else:
        row.value = value
    await session.flush()
    await _sync_sandcastle_enabled(project_key, value == "sandcastle")


async def _sync_sandcastle_enabled(project_key: str, enabled: bool) -> None:
    """Keep SandcastleConfig.enabled aligned with the project's default transport so
    the two never drift. Resolves the project path from the registry; no-op if the
    project isn't locally registered."""
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.database import Project
    from app.models.sandcastle import SandcastleConfig

    try:
        async with AsyncSessionLocal() as db:
            paths = (await db.execute(select(Project.path))).scalars().all()
            target = next(
                (p for p in paths if _safe_resolve_key(p) == project_key), None
            )
            if target is None:
                return
            cfg = (await db.execute(
                select(SandcastleConfig).where(SandcastleConfig.project_path == target)
            )).scalar_one_or_none()
            if cfg is None:
                if enabled:
                    db.add(SandcastleConfig(project_path=target, enabled=True))
                    await db.commit()
                return
            if cfg.enabled != enabled:
                cfg.enabled = enabled
                await db.commit()
    except Exception:
        logger.exception("failed to sync sandcastle enabled for %s", project_key)


def _safe_resolve_key(path: str) -> str | None:
    try:
        return resolve_project_key(path)
    except Exception:
        return None


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


def _persona_filename(column: str) -> str | None:
    """Get the persona filename for a column. For agent columns, the column name IS the agent name."""
    # Fixed columns don't have personas
    if column in ("Backlog", "Impediment", "Done", "To Resume"):
        return None
    # Agent columns: column name matches agent filename
    return f"{column}.md"


def _resolve_agent_from_persona(persona: str | None) -> str | None:
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


def _read_persona(project_path: str, column: str) -> str | None:
    filename = _persona_filename(column)
    if not filename:
        return None
    return _read_persona_file(project_path, filename)


def _read_persona_file(project_path: str, filename: str) -> str | None:
    path = Path(project_path) / ".claude" / "agents" / filename
    try:
        return _strip_frontmatter(path.read_text()).strip()
    except OSError:
        return None


def _persona_for_card(project_path: str, card, column: str) -> str | None:
    """Resolve persona for a card. `column` is passed explicitly because the card
    may already have been moved to a non-persona column."""
    agent = getattr(card, "agent", None)
    if agent:
        # card.agent may be a persona name (e.g. "analyst") — try it first
        persona = _read_persona_file(project_path, f"{agent}.md")
        if persona:
            return persona
    return _read_persona(project_path, column)


# ---- prompt ----------------------------------------------------------------

def build_card_prompt(card, *, persona: str | None, ship_mode: str,
                      impediment_question: str | None = None) -> str:
    preamble = (persona.strip() + "\n\n") if persona else ""
    impediment_section = ""
    if impediment_question:
        impediment_section = (
            "\n\n## IMPEDIMENT\n"
            "A previous agent was blocked on this card. Their question:\n"
            f"> {impediment_question}\n\n"
            "Please address this question or clarify what's needed before proceeding.\n"
        )

    # Standardised session-end workflow — provider-agnostic, works with any
    # coding agent (Claude Code, OpenCode, Codex CLI, …).  The agent runs
    # tests → ships (merge/PR) → attaches the deliverable → moves the card
    # to Done.  The backend then kills the tmux session and removes the
    # worktree automatically.
    ship_instructions = _build_ship_instructions(ship_mode)

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
        "blocked, use `report_impediment` with a clear question explaining what you need."
        f"\n\n## Session-end workflow\n"
        "When your work on this card is complete, follow these steps in order:\n\n"
        f"{ship_instructions}"
    )


def _build_ship_instructions(ship_mode: str) -> str:
    """Build the standardised session-end workflow instructions.

    These instructions are provider-agnostic: they work the same for Claude Code,
    OpenCode, Codex CLI, or any other coding agent that spawns in a git worktree.
    A skill at ``.claude/skills/git-ship/SKILL.md`` mirrors this logic when the
    agent has filesystem access.
    """
    sync = (
        "1. **Sync** — `git fetch origin` so you are up to date with the remote.\n"
    )
    tests = (
        "2. **Run tests yourself before shipping** — there is no pre-push gate; "
        "nothing blocks a red push.  Run them in this worktree: "
        "``cd backend && source venv/bin/activate && pytest -q`` and "
        "``cd frontend && npm run lint && npm run build``.  Only proceed once both "
        "are green.  GitHub Actions (``quality.yml``) re-runs the same checks after "
        "you push as a backstop, but by then the work may already be merged — it is "
        "not a substitute for checking yourself first.  If a test fails, fix it, "
        "re-run, and only ship once green.  Never ship red tests.\n"
    )
    commit = (
        "3. **Commit your work** — make sure every change is committed to the "
        "current branch.\n"
    )

    if ship_mode == "direct":
        shipping = (
            "4. **Ship (direct mode)** — merge your branch into master and push:\n"
            "   ```bash\n"
            "   BRANCH=$(git rev-parse --abbrev-ref HEAD)\n"
            "   git checkout master\n"
            "   git merge --no-ff \"$BRANCH\"\n"
            "   git push origin HEAD:master\n"
            "   git checkout \"$BRANCH\"   # back so the worktree stays valid\n"
            "   ```\n"
            "5. **Attach the deliverable** — ``attach_deliverable`` with "
            "``kind=\"branch\"`` and ``ref=<your-branch-name>``.\n"
            "6. **Move the card to Done** — ``move_card`` to ``\"Done\"``.  "
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
            "7. **Move the card** — if the PR merged, ``move_card`` to "
            "``\"Done\"``.  If the poll loop exited because a check failed, the "
            "PR was closed, or the wait timed out, call ``report_impediment`` "
            "instead so a human can look at it — do not move to Done.\n"
        )

    return sync + tests + commit + shipping


# ---- transport -------------------------------------------------------------

class SpawnTransport(Protocol):
    def __call__(self, *, directory: str, prompt: str, session_name: str,
                 provider_id: str = "claude-code") -> dict: ...


def _known_provider_ids() -> set[str]:
    """Ids of the agent providers the registry knows about (claude-code, codex-cli, …).

    Used to tell a provider selection apart from a persona/column name, since
    `card.agent` overloads both."""
    from app.services.providers import get_providers
    return {p.id for p in get_providers()}


def make_worktree_transport(skip_permissions: bool = True) -> SpawnTransport:
    """Factory that returns a worktree transport with configurable permission bypass."""
    def _transport(*, directory: str, prompt: str, session_name: str,
                   provider_id: str = "claude-code") -> dict:
        """Create a worktree off origin/master, then spawn a `provider_id` session in it.

        Raises MemoryLimitExceeded if hardware memory limits are reached.
        """
        from app.services.agent_bridge.spawn import spawn_session
        from app.services.providers.base import SpawnCommandOptions
        from app.services.scheduling.session_registry import session_registry

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
            skip_permissions=skip_permissions, worktree_path=worktree_path, repo_path=repo,
        )
        try:
            return spawn_session(provider_id, options, session_name=session_name)
        except Exception:
            subprocess.run(["git", "-C", repo, "worktree", "remove", worktree_path, "--force"],
                           capture_output=True, text=True, timeout=30)
            raise

    return _transport


# Default transport keeps existing behaviour (permissions bypassed)
worktree_transport = make_worktree_transport(skip_permissions=True)


# Strong references to in-flight sandcastle start tasks. asyncio only keeps weak
# references to tasks, so without this set a fire-and-forget task can be garbage
# collected mid-flight and the run silently never starts.
_sandcastle_start_tasks: set = set()


def sandcastle_transport(*, directory: str, prompt: str, session_name: str,
                         provider_id: str = "claude-code") -> dict:
    """Sandcastle transport: run the agent in an isolated sandbox via sandcastle.

    `provider_id` is accepted for transport-signature parity but ignored: sandcastle
    runs use the per-project sandcastle config's `agent_provider`, not the card's.

    Runs the agent in a Docker/Podman container. The actual run is kicked off
    asynchronously; this function returns immediately after scheduling it.

    The dispatcher always calls transports from inside a running event loop, so we
    schedule the start as a tracked task (kept alive via `_sandcastle_start_tasks`)
    rather than blocking. `session_name` is stored as the run's branch so the reaper
    can recognise the live sandcastle session and not release its claim.

    Raises MemoryLimitExceeded if hardware memory limits are reached.
    """
    import asyncio

    from app.services.scheduling.session_registry import session_registry

    # Check memory limits before spawning
    if not session_registry.can_add_session():
        status = get_memory_status_cached()
        raise MemoryLimitExceeded(
            f"Session limit reached ({session_registry.session_count}/{session_registry.effective_max_sessions}). "
            f"Memory: {status.usage_percent:.0%} used, {status.available_bytes / (1024*1024):.0f}MB available."
        )

    from app.services.sandcastle_service import sandcastle_service

    # Reserve a slot synchronously so this run counts against the shared session
    # budget for the rest of the dispatch tick (the run record is created later, in a
    # background task). The reservation is released by the run lifecycle: on success
    # when the run finishes (_execute_run), or immediately if start_run fails.
    session_registry.reserve_external(session_name)

    async def _start():
        try:
            return await sandcastle_service.start_run(
                project_path=directory,
                prompt=prompt,
                branch_name=session_name,
            )
        except Exception:
            session_registry.release_external(session_name)
            raise

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # Async context (the normal dispatch path): schedule without blocking and
        # keep a strong reference so the task can't be GC'd before it runs.
        task = loop.create_task(_start())
        _sandcastle_start_tasks.add(task)
        task.add_done_callback(_sandcastle_start_tasks.discard)
        return {
            "session_name": session_name,
            "transport": "sandcastle",
            "status": "started",
        }

    # No running loop (e.g. a sync caller): run to completion so we can return run_id.
    run = asyncio.run(_start())
    return {
        "session_name": session_name,
        "transport": "sandcastle",
        "run_id": run.id,
        "status": "started",
    }


async def _live_sandcastle_sessions() -> set[str]:
    """Session names of pending/running sandcastle runs on this device.

    Returned as the `sandcastle_live` liveness source for the reaper. Defensive: any
    failure yields an empty set, which only makes the reaper *more* eager — never
    less — so a transient DB hiccup can't keep a truly-dead claim alive forever."""
    try:
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models.sandcastle import SandcastleRun

        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(SandcastleRun.branch).where(
                        SandcastleRun.status.in_(("pending", "running"))
                    )
                )
            ).scalars().all()
        return {b for b in rows if b}
    except Exception:
        logger.exception("could not query live sandcastle sessions")
        return set()


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


def _active_session_count(cards: Iterable[KanbanCard]) -> int:
    """Number of occupied dispatch slots: cards in agent columns (not Backlog,
    Impediment, or Done) held by an `agent:` claim."""
    from app.kanban.schemas import COLUMNS
    return sum(
        1 for c in cards
        if c.column not in COLUMNS and (c.claimed_by or "").startswith(CLAIMANT_PREFIX)
    )


def _claimant_session(card: KanbanCard) -> str | None:
    """The tmux session name behind an `agent:` claim, or None for unclaimed cards
    and human (`me@ui`) claims — those are never reaped."""
    claimant = card.claimed_by or ""
    if claimant.startswith(CLAIMANT_PREFIX):
        return claimant[len(CLAIMANT_PREFIX):]
    return None


def _live_sessions() -> set[str] | None:
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


_DISPATCH_COLUMNS = ("Backlog", "To Resume")  # new cards from Backlog, resumed cards from To Resume
_PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


def _is_due(card: KanbanCard) -> bool:
    """True unless `card.scheduled_at` names a not-yet-reached future time.

    A missing or unparseable value is treated as due (fail open) rather than
    silently hiding a card from auto-dispatch forever over a bad timestamp.
    """
    scheduled_at = getattr(card, "scheduled_at", None)
    if not scheduled_at:
        return True
    try:
        fire_at = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return True
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=UTC)
    return fire_at <= datetime.now(UTC)


def _next_card(cards: Iterable[KanbanCard]) -> KanbanCard | None:
    cards = list(cards)
    for col in _DISPATCH_COLUMNS:
        col_cards = [c for c in cards if c.column == col and not c.claimed_by and _is_due(c)]
        if col_cards:
            # list_cards is ordered by rank; stable-sort by priority on top of that
            # so higher-priority cards jump the queue within the same column.
            col_cards.sort(key=lambda c: _PRIORITY_RANK.get(c.priority, 0), reverse=True)
            return col_cards[0]

    # Fall back to orphans: cards left unclaimed in an agent column, most commonly
    # by reap_stale_claims releasing a dead session's claim without a resumable
    # transcript to fall back on. Without this, an orphan is invisible to every
    # later tick -- it sits in its agent column forever, cap slot unused, until a
    # human notices and hits "redispatch" by hand (see kanban card "auto dispatch
    # nakijken": auto-dispatch looked stuck even though it was enabled).
    from app.kanban.schemas import COLUMNS
    orphans = [c for c in cards if c.column not in COLUMNS and not c.claimed_by and _is_due(c)]
    if orphans:
        orphans.sort(key=lambda c: _PRIORITY_RANK.get(c.priority, 0), reverse=True)
        return orphans[0]
    return None


# ---- core ------------------------------------------------------------------

async def _run_card(
    session, *, card, project_key: str, project_path: str, transport: SpawnTransport,
    impediment_question: str | None = None,
    agent_override: str | None = None,
) -> dict | None:
    """Claim+move-to-agent-column+spawn one specific card. Returns a result dict, or None if
    the claim was lost. The persona honours an explicit per-card agent over the column.
    
    The transport parameter is the project default. If the card has an explicit transport
    setting, that takes precedence."""
    source_column = card.column
    name = _mint_session_name(project_path, card.title)
    claimant = CLAIMANT_PREFIX + name

    # Get the actual transport for this card (card transport > project default)
    card_transport = get_transport_for_card(card, transport)

    try:
        await apply_operation(
            session, op_type="claim", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"claimed_by": claimant},
        )
    except ClaimRejected:
        return None  # lost the race; another tick/device took it

    # `agent_override` / `card.agent` overload two unrelated concepts:
    #   - a provider id (claude-code, mimo-code, …) → which CLI spawns the session
    #   - a persona name (engineer, developer, …)  → which column + role prompt
    # Resolve them separately so a provider id is never mistaken for a column.
    known_providers = _known_provider_ids()
    card_agent = getattr(card, "agent", None)

    # Provider selection (which CLI to spawn). Override wins over the card's own value.
    provider_id = next(
        (v for v in (agent_override, card_agent) if v in known_providers),
        "claude-code",
    )

    # Persona/column resolution: agent_override (if a persona) > card.agent (if a known
    # persona) > persona from source column > "engineer". Provider ids are skipped here.
    agents_dir = Path(project_path) / ".claude" / "agents"
    known_agents = {p.stem for p in agents_dir.glob("*.md")} if agents_dir.is_dir() else set()

    if agent_override and agent_override not in known_providers:
        target_agent = agent_override
    elif card_agent and card_agent in known_agents:
        target_agent = card_agent
    else:
        persona = _persona_for_card(project_path, card, source_column)
        target_agent = _resolve_agent_from_persona(persona) or "engineer"

    await apply_operation(
        session, op_type="move", entity_type="card", project_key=project_key,
        entity_id=card.id, payload={"column": target_agent},
    )

    # Load persona for the target agent
    persona = _read_persona_file(project_path, f"{target_agent}.md")
    ship_mode = await get_ship_mode(session, project_key)
    prompt = build_card_prompt(card, persona=persona, ship_mode=ship_mode,
        impediment_question=impediment_question)
    try:
        spawned = card_transport(directory=project_path, prompt=prompt, session_name=name,
                                 provider_id=provider_id)
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

    logger.info("dispatched card %s (%s) -> session %s (transport: %s, provider: %s)",
                card.id, source_column, name,
                "sandcastle" if card_transport == sandcastle_transport else "worktree",
                provider_id)
    return {"card_id": card.id, "session_name": name, "claimant": claimant,
            "source_column": source_column, "spawned": spawned}


async def _move_to_resume(
    session, *, card, project_key: str, project_path: str,
) -> bool:
    """When a dead agent session has a resumable worktree, move its card to "To Resume".

    Resolves the resume target via ``_resolve_resume_target``, persists the resume
    session id/folder on the card, moves it to the "To Resume" fixed column, kills
    the dead tmux session, and releases the agent claim. Returns True when a resume
    target was found and the card was moved; False when the worktree has no resumable
    transcript — the caller should fall back to a plain claim release (reaper default).
    """
    from app.kanban.schemas import COLUMNS
    from app.kanban.session_recovery import _resolve_resume_target

    if card.column in COLUMNS:
        return False
    session_name = _claimant_session(card)
    if session_name is None:
        return False

    target = _resolve_resume_target(project_path, session_name)
    if target is None:
        return False

    session_id, project_folder = target
    await apply_operation(
        session, op_type="update", entity_type="card", project_key=project_key,
        entity_id=card.id,
        payload={"resume_session_id": session_id,
                 "resume_project_folder": project_folder},
    )
    await apply_operation(
        session, op_type="move", entity_type="card", project_key=project_key,
        entity_id=card.id, payload={"column": "To Resume"},
    )
    _kill_agent_session(session_name)
    await apply_operation(
        session, op_type="release", entity_type="card",
        project_key=project_key, entity_id=card.id, payload={},
    )
    logger.info(
        "moved card %s to To Resume (session %s -> %s, project_folder=%s)",
        card.id, session_name, session_id, project_folder,
    )
    return True


def _resume_target_from_cwd(cwd: str) -> tuple[str, str] | None:
    """Derive (project_path, session_name) from a dispatched session's cwd.

    Kanban-dispatched sessions run in `<project_path>/.claude/worktrees/<session_name>`
    (see `session_recovery._resolve_resume_target`). Any other cwd shape (project
    root, sandcastle, a manually started `claude` session) isn't ours to touch.
    """
    worktree = Path(cwd)
    if worktree.parent.name != "worktrees" or worktree.parent.parent.name != ".claude":
        return None
    return str(worktree.parent.parent.parent), worktree.name


async def move_limited_session_to_resume(cwd: str) -> bool:
    """When a live kanban-dispatched session hits its Claude usage/session limit,
    move its card to "To Resume" and kill the tmux session right away.

    The dead-session reaper (`reap_stale_claims`) only notices a session once its
    tmux pane is gone, but a session that has hit its limit stays open showing the
    limit notice forever (the CLI never exits) -- so without this, the card would
    sit claimed in its agent column indefinitely. Reuses `_move_to_resume`, which
    doesn't check liveness itself, so calling it for a still-alive session is safe.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.schemas import COLUMNS

    target = _resume_target_from_cwd(cwd)
    if target is None:
        return False
    project_path, session_name = target
    project_key = _safe_resolve_key(project_path)
    if project_key is None:
        return False

    claimant = CLAIMANT_PREFIX + session_name
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards if c.column not in COLUMNS and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return False
        moved = await _move_to_resume(
            ks, card=card, project_key=project_key, project_path=project_path,
        )
        if moved:
            await ks.commit()
        return moved


async def reap_stale_claims(
    session, *, project_key: str, cards: Iterable[KanbanCard], live_sessions: set[str],
    sandcastle_live: set[str] | None = None,
    project_path: str | None = None,
) -> int:
    """Release `agent:` claims on cards in agent columns whose session is gone.

    A dispatched session that dies (crash, manual close, reboot) without moving its
    card out of an agent column would otherwise keep `_project_is_busy` True forever, silently
    wedging auto-dispatch for the whole project — the "auto-pick stopped working"
    symptom. Releasing the dead claim frees the per-project cap so the next card is
    picked up; the orphaned card is left in its agent column (now unclaimed), where
    `_next_card`'s orphan fallback will pick it back up itself once there's a free
    cap slot and nothing waiting in Backlog/To Resume — no human redispatch needed.
    Human (`me@ui`) claims are never touched. Returns the number reaped.

    Liveness has two sources: `live_sessions` (tmux session names, for worktree-transport
    cards) and `sandcastle_live` (session names of pending/running sandcastle runs).
    Sandcastle cards have no tmux session, so without the second source every sandcastle
    card would be reaped on the very next tick and re-dispatched in a loop.

    When ``project_path`` is provided, dead sessions with a resumable transcript in
    their worktree are moved to the "To Resume" column (via ``_move_to_resume``)
    instead of being just released. Cards without a resumable worktree fall back to
    the plain release as before."""
    from app.kanban.schemas import COLUMNS
    sandcastle_live = sandcastle_live or set()
    reaped = 0
    for card in cards:
        if card.column in COLUMNS:  # Skip fixed columns (Backlog, Impediment, Done, To Resume)
            continue
        name = _claimant_session(card)
        if name is None or name in live_sessions or name in sandcastle_live:
            continue

        # If we know the project path, try resume recovery first
        if project_path is not None:
            if await _move_to_resume(
                session, card=card, project_key=project_key,
                project_path=project_path,
            ):
                reaped += 1
                continue

        # Fallback: plain release for non-resumable dead sessions
        await apply_operation(
            session, op_type="release", entity_type="card",
            project_key=project_key, entity_id=card.id, payload={},
        )
        logger.info("reaped stale claim on card %s (dead session %s)", card.id, name)
        reaped += 1
    return reaped


async def dispatch_project(
    session, *, project_key: str, project_path: str, transport: SpawnTransport | None = None,
    live_sessions: set[str] | None = None,
    sandcastle_live: set[str] | None = None,
) -> dict | None:
    """Claim+move+spawn the next card for one project. Returns a result dict or
    None when there is nothing to do (no candidate card, or project is busy).

    When `live_sessions` is provided, stale `agent:` claims on Doing cards whose
    session is no longer alive are reaped first, so a dead session can never wedge
    the busy cap. Passing None skips reaping (used by unit tests that exercise the
    cap directly).
    
    If transport is None, the appropriate transport is automatically selected based
    on the project's sandcastle configuration."""
    cards = await list_cards(session, project_key)
    if live_sessions is not None:
        if await reap_stale_claims(
            session, project_key=project_key, cards=cards, live_sessions=live_sessions,
            sandcastle_live=sandcastle_live, project_path=project_path,
        ):
            cards = await list_cards(session, project_key)

    cap = await get_max_sessions(session, project_key)
    last_result: dict | None = None

    # Fill every free slot in this tick, re-listing after each dispatch so the
    # claim just made counts toward the cap.
    while _active_session_count(cards) < cap:
        card = _next_card(cards)
        if card is None:
            break

        if transport is None:
            transport = await get_transport_for_project(project_path)

        last_result = await _run_card(
            session, card=card, project_key=project_key,
            project_path=project_path, transport=transport,
        )
        if last_result is None:
            break  # dispatch failed (e.g. memory) — let the tick queue/retry
        cards = await list_cards(session, project_key)

    return last_result


async def dispatch_card(
    session, *, card_id: str, project_path: str,
    transport: SpawnTransport | None = None,
    agent_override: str | None = None,
) -> dict | None:
    """Manually dispatch one specific card now, regardless of the auto-pick toggle or
    the busy cap. Returns the result dict or None if the card is missing or its claim
    was lost. If agent_override is provided, use that agent instead of the card's agent."""
    card = await get_card(session, card_id)
    if card is None:
        return None
    # Auto-select transport if not provided
    if transport is None:
        transport = await get_transport_for_project(project_path)
    transport = get_transport_for_card(card, transport)
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
        agent_override=agent_override,
    )


async def dispatch_impediment_card(
    session, *, card_id: str, project_path: str, target_agent: str,
    impediment_question: str,
    transport: SpawnTransport | None = None,
) -> dict | None:
    """Dispatch an impediment card to a specific agent for resolution.
    
    Args:
        card_id: The ID of the impediment card
        project_path: Path to the project
        target_agent: The agent to dispatch to (analyst, developer, testing, code-review)
        impediment_question: The question that needs to be answered
        transport: The spawn transport to use (auto-selects based on card if None)
    
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
    
    # Auto-select transport if not provided
    if transport is None:
        transport = await get_transport_for_project(project_path)
    transport = get_transport_for_card(card, transport)
    
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
        impediment_question=impediment_question,
    )


async def redispatch_card(
    session, *, card_id: str, project_path: str,
    transport: SpawnTransport | None = None,
    agent_override: str | None = None,
) -> dict | None:
    """Release a stuck card, optionally kill its session, and re-dispatch.

    This is the human override for cards that are stuck on agent columns —
    most commonly a session that hit its usage limit and never exited, so the
    dead-session reaper never noticed it. Before killing the existing tmux
    session, we check whether its worktree holds a resumable Claude transcript
    (same lookup the auto-recovery reaper uses). If one is found, the card is
    tagged with `resume_session_id`/`resume_project_folder` so the re-dispatch
    below picks the resume transport (`claude --resume`, same worktree) instead
    of discarding the conversation and starting a brand new session.

    When transport is None, the appropriate transport is automatically selected
    based on the card's transport field (sandcastle/worktree) and the project's
    sandcastle configuration.

    Args:
        card_id: The ID of the card to redispatch
        project_path: Path to the project
        transport: The spawn transport to use (auto-selects based on card if None)
        agent_override: Optional agent to use instead of card's current agent

    Returns:
        Result dict or None if the card was not found
    """
    card = await get_card(session, card_id)
    if card is None:
        return None

    # Kill existing tmux session if claimed by an agent
    session_name = _claimant_session(card)
    if session_name:
        if not getattr(card, "resume_session_id", None) and card.transport != "sandcastle":
            from app.kanban.session_recovery import _resolve_resume_target

            target = _resolve_resume_target(project_path, session_name)
            if target is not None:
                resume_session_id, resume_project_folder = target
                await apply_operation(
                    session, op_type="update", entity_type="card",
                    project_key=card.project_key, entity_id=card.id,
                    payload={"resume_session_id": resume_session_id,
                             "resume_project_folder": resume_project_folder},
                )
                logger.info(
                    "redispatch: resuming card %s (session %s -> %s) instead of a fresh session",
                    card_id, session_name, resume_session_id,
                )
        _kill_agent_session(session_name)
        logger.info("killed old session %s for card %s", session_name, card_id)

    # Release the claim
    if card.claimed_by:
        await apply_operation(
            session, op_type="release", entity_type="card",
            project_key=card.project_key, entity_id=card.id, payload={},
        )

    # Auto-select transport if not provided
    if transport is None:
        transport = await get_transport_for_project(project_path)
    transport = get_transport_for_card(card, transport)

    # Re-dispatch (bypasses busy cap since we just freed the project)
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
        agent_override=agent_override,
    )


async def dispatch_all_pending(
    session, *, project_key: str, project_path: str,
    transport: SpawnTransport | None = None,
) -> list[dict]:
    """Dispatch all unclaimed Backlog cards for a project at once.

    Bypasses the busy cap so multiple cards can be dispatched concurrently.
    When transport is None, each card's transport is auto-selected based on its
    card.transport field and the project's sandcastle configuration.
    Returns a list of result dicts for each successfully dispatched card.
    """
    if transport is None:
        transport = await get_transport_for_project(project_path)
    from app.kanban.service import list_pending_cards
    pending = [c for c in await list_pending_cards(session, project_key) if _is_due(c)]
    results = []
    for card in pending:
        try:
            card_transport = get_transport_for_card(card, transport)
            res = await _run_card(
                session, card=card, project_key=project_key,
                project_path=project_path, transport=card_transport,
            )
            if res is not None:
                results.append(res)
        except Exception:
            logger.exception("failed to dispatch card %s", card.id)
    return results


async def redispatch_all_orphans(
    session, *, project_key: str, project_path: str,
    transport: SpawnTransport | None = None,
) -> list[dict]:
    """Re-dispatch all orphaned cards (unclaimed on agent columns) for a project.

    When transport is None, each card's transport is auto-selected.
    Returns a list of result dicts for each successfully dispatched card.
    """
    from app.kanban.service import list_orphaned_cards
    orphans = await list_orphaned_cards(session, project_key)
    results = []
    for card in orphans:
        try:
            # Pass None so redispatch_card auto-selects per-card transport
            res = await redispatch_card(
                session, card_id=card.id, project_path=project_path,
                transport=transport,
            )
            if res is not None:
                results.append(res)
        except Exception:
            logger.exception("failed to redispatch orphan card %s", card.id)
    return results


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


async def run_dispatch_tick(*, transport: SpawnTransport | None = None) -> None:
    """One poll cycle: dispatch the next Analysis/Todo card for every enabled project
    that maps to a local path on this device.
    
    Also retries queued cards that were rejected due to memory limits.
    
    If transport is None, each project will automatically select the appropriate
    transport based on its sandcastle configuration.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import is_dispatch_paused

    async with KanbanSessionLocal() as ks:
        if await is_dispatch_paused(ks):
            logger.info("dispatch tick skipped: paused after a Claude usage-limit hit")
            return

    # First, try to retry queued cards if memory is available
    await _retry_queued_cards(transport or worktree_transport)

    async with KanbanSessionLocal() as ks:
        enabled = set(await list_autodispatch_projects(ks))
    if not enabled:
        return

    paths = await _registered_project_paths()
    mapping = match_project_paths(enabled, paths)
    if not mapping:
        return

    live_sessions = _live_sessions()  # one tmux query per tick, shared across projects
    sandcastle_live = await _live_sandcastle_sessions()  # sandcastle liveness, shared

    for project_key, project_path in mapping.items():
        async with KanbanSessionLocal() as ks:
            try:
                result = await dispatch_project(
                    ks, project_key=project_key, project_path=project_path,
                    transport=transport, live_sessions=live_sessions,
                    sandcastle_live=sandcastle_live,
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


def _kill_agent_session(session_name: str) -> None:
    """Kill a tmux session belonging to an agent."""
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


class MemoryLimitExceeded(Exception):
    """Raised when a spawn is rejected due to memory limits."""
    pass


async def get_transport_for_project(project_path: str) -> SpawnTransport:
    """Get the appropriate transport for a project.

    The authoritative source is the `transport:<project_key>` meta (worktree |
    sandcastle), set via the project's Default-transport control. Worktree honors
    the per-project skip_permissions flag.
    """
    from app.kanban.db import KanbanSessionLocal

    project_key = _safe_resolve_key(project_path)
    if project_key is None:
        return make_worktree_transport(skip_permissions=True)

    async with KanbanSessionLocal() as ks:
        transport_name = await get_default_transport(ks, project_key)
        if transport_name == "sandcastle":
            return sandcastle_transport
        skip = await get_skip_permissions(ks, project_key)

    return make_worktree_transport(skip_permissions=skip)


def make_resume_transport(session_id: str, project_folder: str | None = None,
                          skip_permissions: bool = True) -> SpawnTransport:
    """Factory that returns a transport that resumes an existing session.

    Unlike the worktree transport, this does NOT create a new git worktree.
    The ClaudeCodeProvider resolves the working directory from the session's
    recorded cwd (via project_folder), and spawns with ``--resume session_id``.
    """
    def _transport(*, directory: str, prompt: str, session_name: str,
                   provider_id: str = "claude-code") -> dict:
        from app.services.agent_bridge.spawn import spawn_session
        from app.services.providers.base import SpawnCommandOptions
        from app.services.scheduling.session_registry import session_registry

        if not session_registry.can_add_session():
            status = get_memory_status_cached()
            raise MemoryLimitExceeded(
                f"Session limit reached ({session_registry.session_count}/{session_registry.effective_max_sessions}). "
                f"Memory: {status.usage_percent:.0%} used, {status.available_bytes / (1024*1024):.0f}MB available."
            )

        options = SpawnCommandOptions(
            directory=directory,
            mode="resume",
            session_id=session_id,
            project_folder=project_folder,
            prompt=prompt,
            skip_permissions=skip_permissions,
        )
        return spawn_session(provider_id, options, session_name=session_name)

    return _transport


def get_transport_for_card(card: KanbanCard, default_transport: SpawnTransport) -> SpawnTransport:
    """Get the appropriate transport for a card based on its transport field.

    Transport priority:
    1. Card's resume_session_id (resume mode — no worktree created)
    2. Card's explicit transport setting (worktree | sandcastle)
    3. Project's default transport (from sandcastle config)

    Returns the transport to use for this specific card.
    """
    # Resume takes priority: spawn with --resume rather than creating a worktree
    resume_id = getattr(card, "resume_session_id", None)
    if resume_id:
        project_folder = getattr(card, "resume_project_folder", None)
        return make_resume_transport(resume_id, project_folder)

    # If card has explicit transport setting, use it
    if card.transport == "sandcastle":
        return sandcastle_transport
    elif card.transport == "worktree":
        return worktree_transport

    # Otherwise use the project default
    return default_transport


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

                # Check if card is still in a dispatchable state: unclaimed, and
                # either a fresh Backlog/To-Resume card or an orphan left behind in
                # an agent column (mirrors _next_card's orphan fallback above --
                # otherwise a queued orphan gets silently dropped here instead of
                # retried).
                from app.kanban.schemas import COLUMNS
                dispatchable_column = (
                    card_data.column in _DISPATCH_COLUMNS
                    or card_data.column not in COLUMNS
                )
                if card_data.claimed_by or not dispatchable_column:
                    logger.info(f"Card {card.card_id} is no longer dispatchable, removing from queue")
                    pending_queue.dequeue(card.card_id)
                    continue

                # Honour the per-project session cap. Memory may be free again, but
                # the project can still be at its user-set cap; retrying past it is
                # exactly how a cap of 3 ends up running 6 sessions. A cap hold is not
                # a failed dispatch, so leave the card untouched in the queue (don't
                # mark_retry, which would count toward max_retries and eventually drop
                # a card that is merely waiting for a slot).
                cap = await get_max_sessions(ks, card.project_key)
                if _active_session_count(await list_cards(ks, card.project_key)) >= cap:
                    logger.info(
                        f"Card {card.card_id} held back: project {card.project_key} "
                        f"at session cap ({cap})"
                    )
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
