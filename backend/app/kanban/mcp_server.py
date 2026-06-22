"""Kanban MCP server. The agent talks to this over localhost SSE; only the
backend reaches the store, so the agent never sees DB/sync credentials.

Each tool is a thin wrapper over apply_operation/service, returning plain
dicts (JSON-serializable) for the MCP layer.
"""
from mcp.server.fastmcp import FastMCP

from app.kanban.db import KanbanSessionLocal
from app.kanban import mail, service
from app.kanban.operations import apply_operation, ClaimRejected
from app.kanban.schemas import CardResponse, MessageResponse

mcp = FastMCP("cockpit-kanban")


def _card_dict(card) -> dict:
    return CardResponse.model_validate(card).model_dump(mode="json")


def _msg_dict(msg) -> dict:
    return MessageResponse.model_validate(msg).model_dump(mode="json")


def _msg_summary(msg) -> dict:
    """Compact inbox row — full body fetched via read_mail."""
    return {
        "id": msg.id, "from": msg.from_handle, "to": msg.to_handle,
        "kind": msg.kind, "subject": msg.subject, "card_id": msg.card_id,
        "in_reply_to": msg.in_reply_to, "status": msg.status,
        "created_at": msg.created_at.isoformat(),
    }


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
async def dispatch_to_agent(card_id: str, target_agent: str, project_path: str) -> dict:
    """Route a card to a specific agent and spawn a session for it.
    
    Use this when you are the dispatch agent and need to route a card to the
    appropriate agent. The card will be moved to the target agent's column and
    a new Claude session will be spawned to work on it.
    
    Args:
        card_id: The ID of the card to dispatch
        target_agent: The agent to dispatch to (e.g., developer, testing, code-review)
        project_path: The project path for spawning the session
    """
    from app.kanban import dispatch as dispatch_mod
    
    async with KanbanSessionLocal() as s:
        result = await dispatch_mod.dispatch_card(
            s, card_id=card_id, project_path=project_path,
            agent_override=target_agent,
        )
        await s.commit()
        
        if result is None:
            return {"error": "dispatch_failed", "card_id": card_id}
        
        return {
            "ok": True,
            "card_id": card_id,
            "target_agent": target_agent,
            "session_name": result.get("session_name"),
        }


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


# --- Agent Mail -------------------------------------------------------------
# Identity is passed explicitly (MCP/SSE is stateless — the server can't tell who
# is calling, same as claim_card(claimed_by)). Spoofable, acceptable in the local
# single-user trust model.


@mcp.tool()
async def send_mail(project: str, from_handle: str, to_handle: str | None,
                    kind: str, subject: str, body: str,
                    card_id: str | None = None) -> dict:
    """Send a message to another agent (or broadcast with to_handle=None).

    kind is one of: context_request, context_response, handoff, note. Prefer the
    dedicated request_context/respond_context/handoff tools for those flows."""
    async with KanbanSessionLocal() as s:
        try:
            msg = await mail.send_message(s, project, from_handle, to_handle,
                kind, subject, body, card_id=card_id)
        except ValueError as e:
            return {"error": str(e)}
        await mail.ensure_identity(s, project, from_handle)
        await s.commit()
        return _msg_dict(msg)


@mcp.tool()
async def request_context(project: str, from_handle: str, to_handle: str,
                          subject: str, body: str,
                          card_id: str | None = None) -> dict:
    """Ask another agent for specific context. They reply with respond_context."""
    async with KanbanSessionLocal() as s:
        msg = await mail.send_message(s, project, from_handle, to_handle,
            "context_request", subject, body, card_id=card_id)
        await mail.ensure_identity(s, project, from_handle)
        await s.commit()
        return _msg_dict(msg)


@mcp.tool()
async def respond_context(project: str, from_handle: str, in_reply_to: str,
                          body: str) -> dict:
    """Answer a context_request. Marks the original request as answered."""
    async with KanbanSessionLocal() as s:
        parent = await mail.get_message(s, in_reply_to)
        if parent is None:
            return {"error": "not_found", "in_reply_to": in_reply_to}
        msg = await mail.send_message(s, project, from_handle, parent.from_handle,
            "context_response", f"Re: {parent.subject}", body,
            card_id=parent.card_id, in_reply_to=in_reply_to)
        await mail.ensure_identity(s, project, from_handle)
        await s.commit()
        return _msg_dict(msg)


@mcp.tool()
async def handoff(project: str, from_handle: str, to_handle: str,
                  subject: str, body: str, card_id: str) -> dict:
    """Hand work + context off to another agent. The next agent picking up the
    card sees this inline in their prompt (warm start)."""
    async with KanbanSessionLocal() as s:
        msg = await mail.send_message(s, project, from_handle, to_handle,
            "handoff", subject, body, card_id=card_id)
        await mail.ensure_identity(s, project, from_handle)
        await s.commit()
        return _msg_dict(msg)


@mcp.tool()
async def check_inbox(project: str, handle: str, unread_only: bool = True) -> list[dict]:
    """List your inbox (messages addressed to you + broadcasts). Read full bodies
    with read_mail."""
    async with KanbanSessionLocal() as s:
        rows = await mail.list_inbox(s, project, handle, unread_only=unread_only)
        return [_msg_summary(m) for m in rows]


@mcp.tool()
async def read_mail(message_id: str, reader_handle: str) -> dict:
    """Read a message in full and mark it read (if you are the recipient)."""
    async with KanbanSessionLocal() as s:
        msg = await mail.mark_read(s, message_id, reader_handle)
        if msg is None:
            return {"error": "not_found", "message_id": message_id}
        await s.commit()
        return _msg_dict(msg)
