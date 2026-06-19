"""REST API for Agent Mail. Direct CRUD via mail.py (outside the op-log)."""
from fastapi import APIRouter, HTTPException, Query, status

from app.kanban.db import KanbanSessionLocal
from app.kanban import mail
from app.kanban.schemas import (
    EnsureIdentityRequest, IdentityResponse, MarkReadRequest,
    MessageResponse, SendMessageRequest,
)

router = APIRouter(prefix="/kanban/mail", tags=["Kanban Mail"])


@router.get("/identities")
async def list_identities(project_key: str = Query(...)):
    async with KanbanSessionLocal() as s:
        rows = await mail.list_identities(s, project_key)
        return {"identities": [IdentityResponse.model_validate(r) for r in rows]}


@router.post("/identities", response_model=IdentityResponse)
async def ensure_identity(payload: EnsureIdentityRequest):
    async with KanbanSessionLocal() as s:
        identity = await mail.ensure_identity(
            s, payload.project_key, payload.handle,
            display_name=payload.display_name, agent_session=payload.session,
        )
        await s.commit()
        return IdentityResponse.model_validate(identity)


@router.get("/inbox")
async def inbox(project_key: str = Query(...), handle: str = Query(...),
                unread_only: bool = False):
    async with KanbanSessionLocal() as s:
        rows = await mail.list_inbox(s, project_key, handle, unread_only=unread_only)
        return {"messages": [MessageResponse.model_validate(r) for r in rows]}


@router.get("/messages")
async def messages(project_key: str = Query(...), card_id: str = Query(...)):
    async with KanbanSessionLocal() as s:
        rows = await mail.list_for_card(s, project_key, card_id)
        return {"messages": [MessageResponse.model_validate(r) for r in rows]}


@router.post("/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send(payload: SendMessageRequest):
    async with KanbanSessionLocal() as s:
        try:
            msg = await mail.send_message(
                s, payload.project_key, payload.from_handle, payload.to_handle,
                payload.kind, payload.subject, payload.body,
                card_id=payload.card_id, in_reply_to=payload.in_reply_to,
            )
        except ValueError as e:
            raise HTTPException(422, str(e))
        await mail.ensure_identity(s, payload.project_key, payload.from_handle)
        await s.commit()
        return MessageResponse.model_validate(msg)


@router.get("/messages/{message_id}/thread")
async def thread(message_id: str):
    async with KanbanSessionLocal() as s:
        rows = await mail.list_thread(s, message_id)
        if not rows:
            raise HTTPException(404, "message not found")
        return {"messages": [MessageResponse.model_validate(r) for r in rows]}


@router.post("/messages/{message_id}/read", response_model=MessageResponse)
async def mark_read(message_id: str, payload: MarkReadRequest):
    async with KanbanSessionLocal() as s:
        msg = await mail.mark_read(s, message_id, payload.reader_handle)
        if msg is None:
            raise HTTPException(404, "message not found")
        await s.commit()
        return MessageResponse.model_validate(msg)
