"""REST API for the kanban board. All mutations go through apply_operation."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status

from app.kanban.db import KanbanSessionLocal
from app.kanban import service
from app.kanban.operations import apply_operation, ClaimRejected
from app.kanban.project_key import resolve_project_key
from app.kanban.schemas import (
    CardResponse, CardCreate, CardUpdate, MoveRequest, ClaimRequest,
    CommentRequest, AttachRequest, ActivityEntry, EnableRequest,
    AutodispatchRequest, ShipModeRequest, DispatchRequest, RedispatchRequest,
    ColumnResponse, ColumnCreate, ColumnUpdate,
    WorkflowTriggerRequest, WorkflowTriggerResponse, ImpedimentResolveRequest,
)

MCP_SSE_URL = "http://localhost:8000/kanban-mcp/sse"


def _write_json_atomic(target: Path, data: dict) -> None:
    """Write JSON via a temp file + os.replace so a crash mid-write can't
    corrupt an existing .mcp.json."""
    import os
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, target)

router = APIRouter(prefix="/kanban", tags=["Kanban"])


@router.get("/columns")
async def columns(project_key: str = Query(...)):
    async with KanbanSessionLocal() as s:
        cols = await service.list_columns(s, project_key)
        return {"columns": [ColumnResponse.model_validate(c) for c in cols]}


@router.post("/columns", response_model=ColumnResponse, status_code=status.HTTP_201_CREATED)
async def create_column(payload: ColumnCreate):
    async with KanbanSessionLocal() as s:
        col = await service.create_column(
            s, project_key=payload.project_key, name=payload.name,
            rank=payload.rank, default_agent=payload.default_agent,
        )
        await s.commit()
        return ColumnResponse.model_validate(col)


@router.patch("/columns/{column_id}", response_model=ColumnResponse)
async def update_column(column_id: str, payload: ColumnUpdate):
    async with KanbanSessionLocal() as s:
        col = await service.update_column(
            s, column_id,
            name=payload.name, rank=payload.rank,
            default_agent=payload.default_agent,
        )
        if col is None:
            raise HTTPException(404, "column not found")
        await s.commit()
        return ColumnResponse.model_validate(col)


@router.delete("/columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_column(column_id: str):
    async with KanbanSessionLocal() as s:
        if not await service.delete_column(s, column_id):
            raise HTTPException(404, "column not found")
        await s.commit()


@router.get("/cards")
async def list_cards(project_key: str = Query(...), column: str | None = None):
    async with KanbanSessionLocal() as s:
        rows = await service.list_cards(s, project_key, column)
        return {"items": [CardResponse.model_validate(c) for c in rows]}


async def _reload(s, cid: str) -> CardResponse:
    card = await service.get_card(s, cid)
    if card is None:
        raise HTTPException(404, "card not found")
    return CardResponse.model_validate(card)


@router.post("/cards", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
async def create_card(payload: CardCreate):
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=payload.project_key, entity_id=None,
            payload=payload.model_dump(exclude={"project_key"}))
        await s.commit()
        return await _reload(s, cid)


@router.get("/cards/{cid}", response_model=CardResponse)
async def get_card(cid: str):
    async with KanbanSessionLocal() as s:
        return await _reload(s, cid)


@router.get("/cards/{cid}/activity", response_model=list[ActivityEntry])
async def activity(cid: str):
    async with KanbanSessionLocal() as s:
        return await service.card_activity(s, cid)


@router.patch("/cards/{cid}", response_model=CardResponse)
async def update_card(cid: str, payload: CardUpdate):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid,
            payload=payload.model_dump(exclude_unset=True))
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/move", response_model=CardResponse)
async def move_card(cid: str, payload: MoveRequest):
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")

        # Auto-assign agent from column default if card has no explicit agent
        if card.agent is None:
            default_agent = await service.get_column_default_agent(
                s, card.project_key, payload.column
            )
            if default_agent:
                await apply_operation(s, op_type="update", entity_type="card",
                    project_key=card.project_key, entity_id=cid,
                    payload={"agent": default_agent})

        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/claim", response_model=CardResponse)
async def claim_card(cid: str, payload: ClaimRequest):
    async with KanbanSessionLocal() as s:
        try:
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="", entity_id=cid, payload=payload.model_dump())
        except ClaimRejected as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e))
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/release", response_model=CardResponse)
async def release_card(cid: str):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=cid, payload={})
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/comment", response_model=CardResponse)
async def comment(cid: str, payload: CommentRequest):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/deliverables", response_model=CardResponse)
async def attach(cid: str, payload: AttachRequest):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


@router.post("/enable")
async def enable(payload: EnableRequest):
    path = Path(payload.project_path)
    if not path.is_dir():
        raise HTTPException(422, "project_path is not a directory")
    key = f"slug:{payload.slug}" if payload.slug else resolve_project_key(str(path))
    mcp_file = path / ".mcp.json"
    data = {}
    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text())
        except json.JSONDecodeError:
            data = {}
    data.setdefault("mcpServers", {})["cockpit-kanban"] = {
        "type": "sse", "url": MCP_SSE_URL,
    }
    _write_json_atomic(mcp_file, data)

    # Ensure fixed columns exist for this project (Backlog, Impediment, Done)
    # Agent columns are created dynamically based on project agents
    from app.kanban.schemas import COLUMNS
    async with KanbanSessionLocal() as s:
        existing = await service.list_columns(s, key)
        existing_names = {c.name for c in existing}
        
        # Get agents from .claude/agents directory
        agents_dir = path / ".claude" / "agents"
        agents = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
        valid_names = set(COLUMNS) | set(agents)
        
        # Move cards from orphaned columns to Backlog before removing columns
        orphaned = [c.name for c in existing if c.name not in valid_names]
        if orphaned:
            from app.kanban.models import KanbanCard
            from sqlalchemy import select, update
            for col_name in orphaned:
                await s.execute(
                    update(KanbanCard)
                    .where(KanbanCard.project_key == key)
                    .where(KanbanCard.column == col_name)
                    .values(column="Backlog")
                )
        
        # Remove orphaned columns
        for col in existing:
            if col.name not in valid_names:
                await service.delete_column(s, col.id)
        
        # Create missing fixed columns
        for i, col_name in enumerate(COLUMNS):
            if col_name not in existing_names:
                await service.create_column(s, key, name=col_name, rank=f"{i:04d}")
        
        # Sync agent columns
        await service.sync_agent_columns(s, key, agents)
        
        await s.commit()

    return {"project_key": key, "enabled": True}


@router.post("/disable")
async def disable(payload: EnableRequest):
    path = Path(payload.project_path)
    mcp_file = path / ".mcp.json"
    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text())
            data.get("mcpServers", {}).pop("cockpit-kanban", None)
            _write_json_atomic(mcp_file, data)
        except json.JSONDecodeError:
            pass
    return {"enabled": False}


@router.get("/mcp-status")
async def mcp_status(project_path: str = Query(...)):
    path = Path(project_path)
    mcp_file = path / ".mcp.json"
    if not mcp_file.exists():
        return {"enabled": False}
    try:
        data = json.loads(mcp_file.read_text())
        enabled = "cockpit-kanban" in data.get("mcpServers", {})
    except (json.JSONDecodeError, OSError):
        enabled = False
    return {"enabled": enabled}


@router.get("/project-key")
async def project_key(project_path: str = Query(...)):
    return {"project_key": resolve_project_key(project_path)}


@router.get("/autodispatch")
async def get_autodispatch(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "enabled": await dispatch.is_autodispatch_enabled(s, project_key)}


@router.post("/autodispatch")
async def set_autodispatch(payload: AutodispatchRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        await dispatch.set_autodispatch(s, payload.project_key, payload.enabled)
        await s.commit()
    return {"project_key": payload.project_key, "enabled": payload.enabled}


@router.get("/shipmode")
async def get_shipmode(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "mode": await dispatch.get_ship_mode(s, project_key)}


@router.post("/shipmode")
async def set_shipmode(payload: ShipModeRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_ship_mode(s, payload.project_key, payload.mode)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "mode": payload.mode}


@router.delete("/cards/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(cid: str):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="delete", entity_type="card",
            project_key="", entity_id=cid, payload={})
        await s.commit()


@router.get("/agents")
async def list_agents(project_path: str = Query(...)):
    """Agent personas available to this project (\"<name>\" of each
    .claude/agents/<name>.md), for the per-card agent selector."""
    agents_dir = Path(project_path) / ".claude" / "agents"
    names = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    return {"agents": names}


@router.post("/sync-agent-columns")
async def sync_agent_columns_endpoint(payload: EnableRequest):
    """Sync agent columns with the agents configured for this project."""
    path = Path(payload.project_path)
    if not path.is_dir():
        raise HTTPException(422, "project_path is not a directory")
    
    # Get project key
    key = f"slug:{payload.slug}" if payload.slug else resolve_project_key(str(path))
    
    # Get agents from .claude/agents directory
    agents_dir = path / ".claude" / "agents"
    agents = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    
    async with KanbanSessionLocal() as s:
        await service.sync_agent_columns(s, key, agents)
        await s.commit()
    
    return {"project_key": key, "agents": agents, "synced": True}


@router.post("/cards/{cid}/dispatch")
async def dispatch_now(cid: str, payload: DispatchRequest):
    """Manually run the chosen agent on one card now, regardless of auto-pick."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            res = await dispatch.dispatch_card(s, card_id=cid,
                project_path=payload.project_path, agent_override=payload.agent)
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"dispatch failed: {e}")
        await s.commit()
    if res is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
            "could not dispatch (card missing or already claimed)")
    return res


@router.post("/cards/{cid}/redispatch")
async def redispatch_now(cid: str, payload: RedispatchRequest):
    """Release a stuck card and re-dispatch it with a fresh session."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            res = await dispatch.redispatch_card(
                s, card_id=cid, project_path=payload.project_path,
                agent_override=payload.agent,
            )
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"redispatch failed: {e}")
        await s.commit()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card not found")
    return res


@router.post("/redispatch-all")
async def redispatch_all(payload: EnableRequest):
    """Re-dispatch all orphaned cards (unclaimed on agent columns) for a project."""
    from app.kanban import dispatch
    from app.kanban.project_key import resolve_project_key
    key = resolve_project_key(payload.project_path)
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=key, project_path=payload.project_path,
        )
        await s.commit()
    return {"redispatched": len(results), "results": results}


@router.post("/cards/{cid}/workflow", response_model=WorkflowTriggerResponse)
async def trigger_workflow(cid: str, payload: WorkflowTriggerRequest):
    """Trigger workflow based on agent output. Moves card to next column if status indicates."""
    from app.kanban.workflow import process_agent_output, load_flows
    
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")
        
        # Manual override: skip workflow and move to specified column
        if payload.manual_override:
            # For manual override, the agent_output should contain the target column
            from app.kanban.workflow import parse_agent_output
            parsed = parse_agent_output(payload.agent_output)
            target_column = parsed.get("next_column") if parsed else None
            if target_column:
                await apply_operation(s, op_type="move", entity_type="card",
                    project_key=card.project_key, entity_id=cid,
                    payload={"column": target_column})
                await s.commit()
                return WorkflowTriggerResponse(
                    should_move=True,
                    next_column=target_column,
                    card_moved=True,
                )
            else:
                raise HTTPException(422, "manual override requires next_column in agent output")
        
        # Process agent output through workflow engine
        flows = load_flows()
        result = process_agent_output(
            card_id=cid,
            current_column=card.column,
            agent_output=payload.agent_output,
            flows=flows,
        )
        
        if result["error"]:
            return WorkflowTriggerResponse(
                should_move=False,
                error=result["error"],
            )
        
        if result["should_move"] and result["next_column"]:
            await apply_operation(s, op_type="move", entity_type="card",
                project_key=card.project_key, entity_id=cid,
                payload={"column": result["next_column"]})
            
            # Update agent if next_agent is specified
            if result["next_agent"]:
                await apply_operation(s, op_type="update", entity_type="card",
                    project_key=card.project_key, entity_id=cid,
                    payload={"agent": result["next_agent"]})
            
            # Store impediment question if present
            if result["impediment_question"]:
                await apply_operation(s, op_type="comment", entity_type="comment",
                    project_key=card.project_key, entity_id=cid,
                    payload={"text": f"**Impediment:** {result['impediment_question']}"})
            
            await s.commit()
            return WorkflowTriggerResponse(
                should_move=True,
                next_column=result["next_column"],
                next_agent=result["next_agent"],
                card_moved=True,
            )
        
        return WorkflowTriggerResponse(
            should_move=False,
            next_column=result.get("next_column"),
            next_agent=result.get("next_agent"),
        )


@router.post("/cards/{cid}/resolve-impediment", response_model=CardResponse)
async def resolve_impediment(cid: str, payload: ImpedimentResolveRequest):
    """Resolve an impediment by dispatching to a specific agent."""
    from app.kanban import dispatch
    
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")
        
        if card.column != "Impediment":
            raise HTTPException(422, "card is not in Impediment column")
        
        # Get impediment question from activity
        activity = await service.card_activity(s, cid)
        impediment_question = None
        for entry in reversed(activity):
            if entry.op_type == "comment" and "Impediment:" in entry.payload.get("text", ""):
                impediment_question = entry.payload["text"].replace("**Impediment:** ", "")
                break
        
        if not impediment_question:
            impediment_question = "No impediment question found"
        
        # Determine target agent based on workflow rules or override
        target_agent = payload.target_agent
        if not target_agent:
            from app.kanban.workflow import load_flows
            flows = load_flows()
            impediment_agents = flows.get("impediment_agents", {})
            current_agent = card.agent or "developer"
            possible_agents = impediment_agents.get(current_agent, ["developer"])
            target_agent = possible_agents[0] if possible_agents else "developer"
        
        try:
            res = await dispatch.dispatch_impediment_card(
                s, card_id=cid, project_path=payload.project_path,
                target_agent=target_agent, impediment_question=impediment_question,
            )
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"dispatch failed: {e}")
        await s.commit()
    
    if res is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
            "could not dispatch impediment (card missing or already claimed)")
    return await _reload(s, cid)
