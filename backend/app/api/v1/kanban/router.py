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
    CommentRequest, AttachRequest, ActivityEntry, COLUMNS, EnableRequest,
    AutodispatchRequest,
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
async def columns():
    return {"columns": COLUMNS}


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
    if payload.column not in COLUMNS:
        raise HTTPException(422, f"unknown column: {payload.column}")
    async with KanbanSessionLocal() as s:
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
