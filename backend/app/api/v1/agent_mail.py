"""Agent Mail endpoints: team roster, agent registration, hooks, install."""
import logging
import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent_mail import MailTeamMember
from app.models.agent_mail_schemas import (
    AgentMailInstallStatus,
    AgentMailSnippets,
    MailAgentRegisterRequest,
    MailAgentRegisterResponse,
    MailMemberResponse,
    MailMemberUpdate,
    TeamListResponse,
)
from app.services.agent_mail import install_status
from app.services.agent_mail_service import agent_mail_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/team", response_model=TeamListResponse)
async def get_team(sync: bool = True, db: AsyncSession = Depends(get_db)):
    if sync:
        await agent_mail_service.sync_observed_sessions(db)
    return TeamListResponse(members=await agent_mail_service.list_team(db))


@router.patch("/members/{member_id}", response_model=MailMemberResponse)
async def update_member(member_id: int, update: MailMemberUpdate, db: AsyncSession = Depends(get_db)):
    member = await db.get(MailTeamMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if update.display_name is not None:
        member.display_name = update.display_name.strip() or member.display_name
    if update.role is not None:
        member.role = update.role.strip() or None
    if update.charter is not None:
        member.charter = update.charter.strip() or None
    member.updated_at = datetime.utcnow()
    await db.commit()
    members = await agent_mail_service.list_team(db)
    found = next((c for c in members if c.id == member_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return found


@router.post("/agent/register", response_model=MailAgentRegisterResponse)
async def register_agent(request: MailAgentRegisterRequest, db: AsyncSession = Depends(get_db)):
    member, session = await agent_mail_service.register_session(db, request)
    members = await agent_mail_service.list_team(db)
    member_resp = next(c for c in members if c.id == member.id)
    session_resp = next(c for c in member_resp.sessions if c.session_key == session.session_key)
    return MailAgentRegisterResponse(member=member_resp, session=session_resp)


def _hook_cli(payload: dict) -> str:
    raw = str(payload.get("provider") or payload.get("cli") or "claude-code")
    return raw if raw in {"claude-code", "codex-cli"} else "unknown"


def _hook_session_key(payload: dict) -> str | None:
    session_id = payload.get("session_id")
    if not session_id:
        return None
    prefix = "cc" if _hook_cli(payload) == "claude-code" else "codex"
    return f"{prefix}:{session_id}"


async def _register_from_hook(db: AsyncSession, payload: dict):
    session_key = _hook_session_key(payload)
    cwd = payload.get("cwd")
    if not session_key or not cwd:
        return None, None
    return await agent_mail_service.register_session(
        db,
        MailAgentRegisterRequest(
            source="hook", cli=_hook_cli(payload), cwd=cwd,
            session_key=session_key, pid=payload.get("pid"),
        ),
    )


@router.post("/hooks/session-start")
async def hook_session_start(payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    try:
        member, session = await _register_from_hook(db, payload)
        if member is None:
            return {}
        context = await agent_mail_service.build_session_start_context(
            db, member.id, session.session_key if session is not None else None,
        )
        if not context:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    except Exception as exc:
        logger.warning("session-start hook failed: %s", exc)
        return {}


@router.post("/hooks/session-end")
async def hook_session_end(payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    try:
        session_key = _hook_session_key(payload)
        if session_key is not None:
            await agent_mail_service.mark_session_offline(db, session_key)
    except Exception as exc:
        logger.warning("session-end hook failed: %s", exc)
    return {}


@router.post("/hooks/post-tool-use")
async def hook_post_tool_use(payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    try:
        session_key = _hook_session_key(payload)
        if session_key is None:
            return {}
        activity = None
        file_path = (payload.get("tool_input") or {}).get("file_path")
        if file_path:
            activity = f"edited {os.path.basename(str(file_path))}"
        session = await agent_mail_service.heartbeat_session(db, session_key, activity=activity)
        if session is None:
            await _register_from_hook(db, payload)
            if activity:
                await agent_mail_service.heartbeat_session(db, session_key, activity=activity)
    except Exception as exc:
        logger.warning("post-tool-use hook failed: %s", exc)
    return {}


def _require_confirmed(body: dict[str, Any] | None) -> None:
    if not body or not body.get("confirmed"):
        raise HTTPException(status_code=400, detail='Pass {"confirmed": true} to mutate config')


@router.get("/install/status", response_model=AgentMailInstallStatus)
async def install_status_route():
    return await install_status.get_install_status()


@router.post("/install/claude-code/apply", response_model=AgentMailInstallStatus)
async def install_claude_code(body: dict[str, Any] | None = Body(default=None)):
    _require_confirmed(body)
    return await install_status.apply_claude_code_install()


@router.post("/install/claude-code/uninstall", response_model=AgentMailInstallStatus)
async def uninstall_claude_code_route(body: dict[str, Any] | None = Body(default=None)):
    _require_confirmed(body)
    return await install_status.uninstall_claude_code()


@router.post("/install/codex/apply", response_model=AgentMailInstallStatus)
async def install_codex(body: dict[str, Any] | None = Body(default=None)):
    _require_confirmed(body)
    try:
        return await install_status.apply_codex_install()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/install/codex/uninstall", response_model=AgentMailInstallStatus)
async def uninstall_codex_route(body: dict[str, Any] | None = Body(default=None)):
    _require_confirmed(body)
    return await install_status.uninstall_codex()


@router.get("/install/snippets", response_model=AgentMailSnippets)
async def install_snippets():
    return install_status.get_snippets()
