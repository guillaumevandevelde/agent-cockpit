"""Kanban MCP server. The agent talks to this over localhost SSE; only the
backend reaches the store, so the agent never sees DB/sync credentials.

Each tool is a thin wrapper over apply_operation/service, returning plain
dicts (JSON-serializable) for the MCP layer.
"""
import logging

from mcp.server.fastmcp import FastMCP
from sqlalchemy import text

from app.kanban.db import KanbanSessionLocal
from app.kanban import service
from app.kanban.operations import apply_operation, ClaimRejected
from app.kanban.schemas import CardResponse

logger = logging.getLogger(__name__)

mcp = FastMCP("cockpit-kanban")

_NOT_FOUND = "not_found"


def _card_dict(card) -> dict:
    if card is None:
        return {"error": _NOT_FOUND}
    return CardResponse.model_validate(card).model_dump(mode="json")


async def _require_card(s, card_id: str):
    """Return the card ORM instance or None. Uses a bare identity lookup so the
    deliverables relationship stays unloaded; the final service.get_card call then
    fetches it fresh via selectinload (avoids stale-cache issues in the same session)."""
    from app.kanban.models import KanbanCard
    return await s.get(KanbanCard, card_id)


@mcp.tool()
async def ping() -> dict:
    """Verify the kanban MCP server is reachable and the database is responsive."""
    async with KanbanSessionLocal() as s:
        await s.execute(text("SELECT 1"))
    return {"ok": True, "server": "cockpit-kanban"}


@mcp.tool()
async def list_cards(project: str, column: str | None = None) -> list[dict]:
    """List cards for a project, optionally filtered by column."""
    async with KanbanSessionLocal() as s:
        rows = await service.list_cards(s, project, column)
        return [_card_dict(c) for c in rows]


@mcp.tool()
async def get_card(card_id: str) -> dict:
    """Get a single card with its deliverables."""
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, card_id)
        if card is None:
            logger.debug("get_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        return _card_dict(card)


@mcp.tool()
async def create_card(project: str, title: str, description: str = "",
                      column: str = "Backlog") -> dict:
    """Create a new card (agents may decompose work into subtask cards)."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=project, entity_id=None,
            payload={"title": title, "description": description, "column": column})
        await s.commit()
        card = await service.get_card(s, cid)
        logger.info("create_card: %s in %s (%s)", cid, project, column)
        return _card_dict(card)


@mcp.tool()
async def claim_card(card_id: str, claimed_by: str) -> dict:
    """Claim a card. Returns the card, or {error: already_claimed, owner} or {error: not_found}."""
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("claim_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        try:
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="", entity_id=card_id, payload={"claimed_by": claimed_by})
        except ClaimRejected as e:
            return {"error": "already_claimed", "owner": e.current_owner}
        await s.commit()
        logger.info("claim_card: %s claimed by %s", card_id, claimed_by)
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def move_card(card_id: str, column: str) -> dict:
    """Move a card to a different column."""
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("move_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": column})
        await s.commit()
        logger.info("move_card: %s → %s", card_id, column)
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def update_card(card_id: str, title: str | None = None,
                      description: str | None = None) -> dict:
    """Update a card's title and/or description."""
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("update_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        payload = {k: v for k, v in {"title": title, "description": description}.items()
                   if v is not None}
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=card_id, payload=payload)
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def comment(card_id: str, text: str) -> dict:
    """Add a comment to a card's activity feed."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id, payload={"text": text})
        await s.commit()
        logger.info("comment: on %s", card_id)
        return {"ok": True}


@mcp.tool()
async def attach_deliverable(card_id: str, kind: str, ref: str) -> dict:
    """Bind a deliverable (pr|branch|commit|link|note) as a portable reference."""
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("attach_deliverable: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=card_id, payload={"kind": kind, "ref": ref})
        await s.commit()
        logger.info("attach_deliverable: %s kind=%s ref=%s", card_id, kind, ref)
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def release_card(card_id: str) -> dict:
    """Release a claim on a card."""
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("release_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=card_id, payload={})
        await s.commit()
        logger.info("release_card: %s", card_id)
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def report_impediment(card_id: str, question: str) -> dict:
    """Report an impediment on a card. Moves it to Impediment column with a clear question.

    Use this when you're stuck and need help from another agent (e.g., analyst for clarification,
    tester for test failures, developer for code issues). The question should be specific and
    actionable so the other agent can help you resolve the blocker.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("report_impediment: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}

        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": "Impediment"})

        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id,
            payload={"text": f"**Impediment:** {question}"})

        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=card_id, payload={})

        await s.commit()
        logger.info("report_impediment: %s — %s", card_id, question[:80])
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def set_resume(card_id: str, session_id: str,
                     project_folder: str | None = None) -> dict:
    """Mark this card to resume an existing Claude session on next dispatch.

    When session_id is set, the next dispatch uses ``claude --resume session_id``
    in the session's original working directory instead of creating a new worktree.
    This lets a new engineer session continue exactly where a context-limited
    session left off.

    Args:
        card_id: The card to tag.
        session_id: The Claude session UUID to resume (from ~/.claude/projects/…/*.jsonl).
        project_folder: Encoded folder name (e.g. "-home-user-repo") that maps to
            ~/.claude/projects/<folder>/.  Inferred from the session file when omitted.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("set_resume: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        payload: dict = {"resume_session_id": session_id}
        if project_folder is not None:
            payload["resume_project_folder"] = project_folder
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=card_id, payload=payload)
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def redispatch_card(card_id: str, project_path: str, agent: str | None = None) -> dict:
    """Release a stuck card and re-dispatch it with a fresh session.

    Use this when a card is stuck on an agent column (e.g., the agent crashed,
    got stuck, or you want to restart work with a fresh session). This will:
    1. Kill the existing tmux session (if any)
    2. Release the claim
    3. Spawn a new session with the same (or different) agent

    Args:
        card_id: The ID of the card to redispatch
        project_path: The project path for spawning the session
        agent: Optional agent override (uses card's current agent if not specified)
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("redispatch_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}

    from app.kanban import dispatch as dispatch_mod

    async with KanbanSessionLocal() as s:
        result = await dispatch_mod.redispatch_card(
            s, card_id=card_id, project_path=project_path,
            agent_override=agent,
        )
        await s.commit()

        if result is None:
            return {"error": _NOT_FOUND, "card_id": card_id}

        logger.info("redispatch_card: %s → session %s", card_id, result.get("session_name"))
        return {
            "ok": True,
            "card_id": card_id,
            "session_name": result.get("session_name"),
        }
