"""MCP tools for Agent Mail — roster + session discovery.

Identity is an explicit (cwd, session_key) argument pair, same trade-off as
this fork's other local MCP tools (e.g. kanban's claim_card(claimed_by)):
the shared MCP server doesn't thread the caller's Bearer-token identity down
into individual tool calls yet, so this is spoofable but acceptable in the
local single-user trust model. See docs/cockpit/agent-mail-spec.md.

Mail-message tools were removed 2026-08-15 (kaart ``46930d26…``); the
``whoami`` + ``list_team`` pair is everything the roster layer needs from
MCP.
"""
import json

from mcp.server.fastmcp import FastMCP

from app.database import AsyncSessionLocal
from app.models.agent_mail_schemas import MailAgentRegisterRequest
from app.services.agent_mail_service import agent_mail_service


def register_agent_mail_tools(mcp: FastMCP) -> None:
    """Register Agent Mail MCP tools."""

    async def _whoami(cwd: str, session_key: str) -> tuple[int, dict]:
        async with AsyncSessionLocal() as db:
            member, _ = await agent_mail_service.register_session(
                db, MailAgentRegisterRequest(source="mcp", cwd=cwd, session_key=session_key),
            )
            return member.id, {
                "member": {
                    "id": member.id, "display_name": member.display_name,
                    "repo_name": member.repo_name, "role": member.role,
                },
            }

    @mcp.tool()
    async def agent_mail_whoami(cwd: str, session_key: str) -> str:
        """Register/refresh your Agent Mail session and return your identity.

        Args:
            cwd: Your current working directory (repo root or subdirectory).
            session_key: A stable key for this session (e.g. your Claude Code session_id).
        """
        _, body = await _whoami(cwd, session_key)
        return json.dumps(body, indent=2)

    @mcp.tool()
    async def agent_mail_list_team(cwd: str, session_key: str) -> str:
        """List all Agent Mail team members visible on this machine."""
        await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            await agent_mail_service.sync_observed_sessions(db)
            team = await agent_mail_service.list_team(db)
            return json.dumps([{
                "id": m.id, "display_name": m.display_name, "role": m.role,
                "repo_name": m.repo_name, "status": m.status,
            } for m in team], indent=2)
