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
    AutodispatchRequest, ShipModeRequest, DispatchRequest,
    ColumnResponse, ColumnCreate, ColumnUpdate,
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


@router.post("/cards/{cid}/dispatch")
async def dispatch_now(cid: str, payload: DispatchRequest):
    """Manually run the chosen agent on one card now, regardless of auto-pick."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            res = await dispatch.dispatch_card(s, card_id=cid,
                project_path=payload.project_path)
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"dispatch failed: {e}")
        await s.commit()
    if res is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
            "could not dispatch (card missing or already claimed)")
    return res
