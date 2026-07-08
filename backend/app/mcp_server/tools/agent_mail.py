"""MCP tools for Agent Mail — cross-session messaging.

Identity is an explicit (cwd, session_key) argument pair, same trade-off as
this fork's other local MCP tools (e.g. kanban's claim_card(claimed_by)):
the shared MCP server doesn't thread the caller's Bearer-token identity down
into individual tool calls yet, so this is spoofable but acceptable in the
local single-user trust model. See docs/cockpit/agent-mail-spec.md.
"""
import json

from mcp.server.fastmcp import FastMCP

from app.database import AsyncSessionLocal
from app.models.agent_mail_schemas import MailAgentRegisterRequest, MailMessageCreate
from app.services.agent_mail_service import agent_mail_service


def register_agent_mail_tools(mcp: FastMCP) -> None:
    """Register Agent Mail MCP tools."""

    async def _whoami(cwd: str, session_key: str) -> tuple[int, dict]:
        async with AsyncSessionLocal() as db:
            member, _ = await agent_mail_service.register_session(
                db, MailAgentRegisterRequest(source="mcp", cwd=cwd, session_key=session_key),
            )
            unread, pending = await agent_mail_service.counts_for_member(db, member.id)
            return member.id, {
                "member": {
                    "id": member.id, "display_name": member.display_name,
                    "repo_name": member.repo_name, "role": member.role,
                },
                "unread_count": unread, "pending_count": pending,
            }

    @mcp.tool()
    async def agent_mail_whoami(cwd: str, session_key: str) -> str:
        """Register/refresh your Agent Mail session and return your identity + inbox counts.

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

    @mcp.tool()
    async def agent_mail_check_inbox(cwd: str, session_key: str, unread_only: bool = True, limit: int = 20) -> str:
        """Check your Agent Mail inbox. Marks fetched messages as read.

        Args:
            unread_only: If true, only return unread messages.
            limit: Maximum number of messages to return.
        """
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            inbox = await agent_mail_service.get_inbox(
                db, member_id, unread_only=unread_only, mark_read=True, limit=limit,
            )
            return inbox.model_dump_json(indent=2)

    @mcp.tool()
    async def agent_mail_send_message(cwd: str, session_key: str, to_member_id: int, body: str, subject: str = "") -> str:
        """Send a direct message to another Agent Mail team member.

        Args:
            to_member_id: The recipient's member id (from agent_mail_list_team).
            body: Markdown message body.
            subject: Optional short subject line.
        """
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            try:
                msg = await agent_mail_service.send_message(
                    db, MailMessageCreate(
                        sender_member_id=member_id, recipient_member_id=to_member_id,
                        subject=subject or None, body_markdown=body,
                    ),
                )
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "message_id": msg.id})

    @mcp.tool()
    async def agent_mail_reply(cwd: str, session_key: str, thread_root_id: int, body: str) -> str:
        """Reply in a thread. Automatically sent as an 'answer' if the thread root
        is a pending context_request addressed to you, otherwise as a plain message.

        Args:
            thread_root_id: The id of the root message of the thread.
            body: Markdown reply body.
        """
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            root = await agent_mail_service.get_thread(db, thread_root_id)
            is_pending_request_to_me = (
                root.root.kind == "context_request"
                and root.root.request_status == "pending"
                and root.root.recipient_member_id == member_id
            )
            kind = "answer" if is_pending_request_to_me else "message"
            try:
                msg = await agent_mail_service.send_message(
                    db, MailMessageCreate(
                        sender_member_id=member_id, thread_root_id=thread_root_id,
                        kind=kind, body_markdown=body,
                    ),
                )
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "message_id": msg.id, "kind": kind})

    @mcp.tool()
    async def agent_mail_ack_message(cwd: str, session_key: str, message_id: int) -> str:
        """Acknowledge a message you've received (closes handoff/answer lifecycle)."""
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            await agent_mail_service.ack_message(db, message_id, member_id)
            return json.dumps({"ok": True})

    @mcp.tool()
    async def agent_mail_request_context(
        cwd: str, session_key: str, to_member_id: int, topic: str,
        why_needed: str = "", files_or_symbols: list[str] | None = None,
    ) -> str:
        """Ask another team member for specific context. They reply via agent_mail_reply.

        Args:
            to_member_id: The member id to ask.
            topic: Short topic (used as the message subject).
            why_needed: Why you need this context.
            files_or_symbols: Relevant files or symbols, if known.
        """
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            try:
                msg = await agent_mail_service.send_message(
                    db, MailMessageCreate(
                        sender_member_id=member_id, recipient_member_id=to_member_id,
                        kind="context_request", subject=topic[:120],
                        body_markdown=why_needed or topic,
                        payload={"why_needed": why_needed, "files_or_symbols": files_or_symbols or []},
                    ),
                )
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "message_id": msg.id})

    @mcp.tool()
    async def agent_mail_create_handoff(
        cwd: str, session_key: str, to_member_id: int, summary: str,
        files: list[str] | None = None, next_steps: list[str] | None = None,
    ) -> str:
        """Hand off work to another team member.

        Args:
            to_member_id: The member id to hand off to.
            summary: What you did / what's next.
            files: Files touched, if relevant.
            next_steps: Concrete next steps for the recipient.
        """
        member_id, _ = await _whoami(cwd, session_key)
        body_lines = [f"## Handoff\n{summary}"]
        if files:
            body_lines.append("\n### Files\n" + "\n".join(f"- {f}" for f in files))
        if next_steps:
            body_lines.append("\n### Next steps\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(next_steps)))
        async with AsyncSessionLocal() as db:
            try:
                msg = await agent_mail_service.send_message(
                    db, MailMessageCreate(
                        sender_member_id=member_id, recipient_member_id=to_member_id,
                        kind="handoff", subject=f"Handoff: {summary[:100]}",
                        body_markdown="\n".join(body_lines),
                        payload={"files": files or [], "next_steps": next_steps or []},
                    ),
                )
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "message_id": msg.id})
