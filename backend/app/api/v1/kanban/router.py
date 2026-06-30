"""REST API for the kanban board. All mutations go through apply_operation."""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from app.config import settings
from app.utils.url_utils import resolve_base_url

from app.kanban.db import KanbanSessionLocal
from app.kanban import service
from app.kanban.operations import apply_operation, ClaimRejected
from app.kanban.project_key import resolve_project_key
from app.kanban.schemas import (
    CardResponse, CardCreate, CardUpdate, MoveRequest, ClaimRequest,
    CommentRequest, AttachRequest, ActivityEntry, EnableRequest,
    AutodispatchRequest, ShipModeRequest, SkipPermissionsRequest,
    MaxSessionsRequest, DefaultTransportRequest,
    DispatchRequest, RedispatchRequest,
    ColumnResponse, ColumnCreate, ColumnUpdate, ColumnClearRequest,
    ImpedimentResolveRequest,
    AgentStatsResponse,
)

logger = logging.getLogger(__name__)

# Fallback routing for an impediment when no target_agent is given. Mirrors the
# former card-flow.json `impediment_agents`; the first entry is chosen.
_IMPEDIMENT_AGENTS = {
    "developer": ["analyst", "testing", "code-review"],
    "tester": ["developer", "analyst"],
    "analyst": ["developer"],
    "code-review": ["developer"],
}


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


@router.get("/stats", response_model=AgentStatsResponse)
async def agent_stats(project_key: str = Query(...)):
    """Per-agent performance: time per task, success rate, token use, failures."""
    from app.kanban import stats as kstats
    from app.kanban.schemas import COLUMNS
    async with KanbanSessionLocal() as s:
        cards, ops = await service.list_project_ops(s, project_key)
        cols = await service.list_columns(s, project_key)
    agent_columns = {c.name for c in cols if c.name not in COLUMNS}
    core = kstats.compute_core_stats(cards, ops, agent_columns)
    usage_by_agent = await kstats.gather_token_usage(core["session_to_agent"])
    tokens_available = kstats.apply_token_usage(core["agents"], usage_by_agent)
    return AgentStatsResponse(
        project_key=project_key,
        totals=core["totals"],
        agents=core["agents"],
        common_failures=core["common_failures"],
        tokens_available=tokens_available,
    )


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
        data = payload.model_dump(exclude_unset=True)
        column_change = data.pop("column", None)

        if column_change:
            card = await service.get_card(s, cid)
            if card is None:
                raise HTTPException(404, "card not found")

            await apply_operation(s, op_type="move", entity_type="card",
                project_key="", entity_id=cid,
                payload={"column": column_change})

        if data:
            await apply_operation(s, op_type="update", entity_type="card",
                project_key="", entity_id=cid, payload=data)

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
async def enable(payload: EnableRequest, request: Request):
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
    entry: dict = {"type": "sse", "url": f"{resolve_base_url(request)}/kanban-mcp/sse"}
    if settings.api_token:
        entry["headers"] = {"Authorization": f"Bearer {settings.api_token}"}
    data.setdefault("mcpServers", {})["cockpit-kanban"] = entry
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


@router.get("/mcp-health")
async def mcp_health():
    """End-to-end self-check of the kanban MCP wiring (advertised endpoint routes
    to the mount, tools registered, store reachable). Surfaced in the UI so a
    silent mis-mount is visible instead of only manifesting as agents that never
    touch their cards."""
    from app.kanban.mcp_health import check_mcp_health
    return await check_mcp_health()


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


@router.get("/skip-permissions")
async def get_skip_permissions(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "enabled": await dispatch.get_skip_permissions(s, project_key)}


@router.post("/skip-permissions")
async def set_skip_permissions(payload: SkipPermissionsRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        await dispatch.set_skip_permissions(s, payload.project_key, payload.enabled)
        await s.commit()
    return {"project_key": payload.project_key, "enabled": payload.enabled}


@router.get("/max-sessions")
async def get_max_sessions(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "max_sessions": await dispatch.get_max_sessions(s, project_key)}


@router.post("/max-sessions")
async def set_max_sessions(payload: MaxSessionsRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_max_sessions(s, payload.project_key, payload.max_sessions)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "max_sessions": payload.max_sessions}


@router.get("/transport")
async def get_transport(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "transport": await dispatch.get_default_transport(s, project_key)}


@router.post("/transport")
async def set_transport(payload: DefaultTransportRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_default_transport(s, payload.project_key, payload.transport)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "transport": payload.transport}


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


@router.post("/dispatch-all")
async def dispatch_all(payload: EnableRequest):
    """Dispatch all pending Backlog/Dispatch cards for a project at once."""
    from app.kanban import dispatch
    from app.kanban.project_key import resolve_project_key
    key = resolve_project_key(payload.project_path)
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=key, project_path=payload.project_path,
        )
        await s.commit()
    return {"dispatched": len(results), "results": results}


@router.post("/clear-column")
async def clear_column(payload: ColumnClearRequest):
    """Delete all cards in a given column for a project."""
    async with KanbanSessionLocal() as s:
        cards = await service.list_cards(s, payload.project_key, column=payload.column)
        count = 0
        for card in cards:
            await apply_operation(s, op_type="delete", entity_type="card",
                project_key="", entity_id=card.id, payload={})
            count += 1
        await s.commit()
    return {"cleared": count}


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
            current_agent = card.agent or "developer"
            possible_agents = _IMPEDIMENT_AGENTS.get(current_agent, ["developer"])
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
