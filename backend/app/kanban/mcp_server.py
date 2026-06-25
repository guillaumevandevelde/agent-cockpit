"""Kanban MCP server. The agent talks to this over localhost SSE; only the
backend reaches the store, so the agent never sees DB/sync credentials.

Each tool is a thin wrapper over apply_operation/service, returning plain
dicts (JSON-serializable) for the MCP layer.
"""
from mcp.server.fastmcp import FastMCP

from app.kanban.db import KanbanSessionLocal
from app.kanban import service
from app.kanban.operations import apply_operation, ClaimRejected
from app.kanban.schemas import CardResponse

mcp = FastMCP("cockpit-kanban")


def _card_dict(card) -> dict:
    return CardResponse.model_validate(card).model_dump(mode="json")


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
        return _card_dict(card) if card else {"error": "not_found"}


@mcp.tool()
async def create_card(project: str, title: str, description: str = "",
                      column: str = "Backlog") -> dict:
    """Create a new card (agents may decompose work into subtask cards)."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=project, entity_id=None,
            payload={"title": title, "description": description, "column": column})
        await s.commit()
        return _card_dict(await service.get_card(s, cid))


@mcp.tool()
async def claim_card(card_id: str, claimed_by: str) -> dict:
    """Claim a card. Returns the card, or {error: already_claimed, owner}."""
    async with KanbanSessionLocal() as s:
        try:
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="", entity_id=card_id, payload={"claimed_by": claimed_by})
        except ClaimRejected as e:
            return {"error": "already_claimed", "owner": e.current_owner}
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def move_card(card_id: str, column: str) -> dict:
    """Move a card to a different column."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": column})
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def update_card(card_id: str, title: str | None = None,
                      description: str | None = None) -> dict:
    """Update a card's title and/or description."""
    payload = {k: v for k, v in {"title": title, "description": description}.items()
               if v is not None}
    async with KanbanSessionLocal() as s:
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
        return {"ok": True}


@mcp.tool()
async def attach_deliverable(card_id: str, kind: str, ref: str) -> dict:
    """Bind a deliverable (pr|branch|commit|link|note) as a portable reference."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=card_id, payload={"kind": kind, "ref": ref})
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def release_card(card_id: str) -> dict:
    """Release a claim on a card."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=card_id, payload={})
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def report_impediment(card_id: str, question: str) -> dict:
    """Report an impediment on a card. Moves it to Impediment column with a clear question.
    
    Use this when you're stuck and need help from another agent (e.g., analyst for clarification,
    tester for test failures, developer for code issues). The question should be specific and
    actionable so the other agent can help you resolve the blocker.
    """
    async with KanbanSessionLocal() as s:
        # Move card to Impediment column
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": "Impediment"})
        
        # Add the impediment question as a comment
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id,
            payload={"text": f"**Impediment:** {question}"})
        
        # Release the claim so another agent can pick it up
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=card_id, payload={})

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
    from app.kanban import dispatch as dispatch_mod
    
    async with KanbanSessionLocal() as s:
        result = await dispatch_mod.redispatch_card(
            s, card_id=card_id, project_path=project_path,
            agent_override=agent,
        )
        await s.commit()
        
        if result is None:
            return {"error": "not_found", "card_id": card_id}
        
        return {
            "ok": True,
            "card_id": card_id,
            "session_name": result.get("session_name"),
        }
