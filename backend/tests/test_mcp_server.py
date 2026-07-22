"""Tests for the Agent Cockpit MCP server."""
import json
from datetime import UTC

import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.mcp_server.auth import (
    TOKEN_PREFIX,
    generate_token,
    hash_secret,
    verify_bearer_token,
    verify_secret,
)
from app.models.mcp_token import MCPAccessToken


@pytest.mark.asyncio
async def test_generate_token_format():
    full_token, prefix, secret = generate_token()
    assert full_token.startswith(f"{TOKEN_PREFIX}_")
    assert len(prefix) == 16
    assert len(secret) > 20
    # Secret may contain underscores (token_urlsafe), so check prefix+secret reconstructs
    assert full_token == f"{TOKEN_PREFIX}_{prefix}_{secret}"


@pytest.mark.asyncio
async def test_hash_and_verify_secret():
    full_token, prefix, secret = generate_token()
    hashed = hash_secret(secret)
    assert verify_secret(secret, hashed) is True
    assert verify_secret("wrong_secret", hashed) is False


@pytest.mark.asyncio
async def test_verify_bearer_token_valid():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    full_token, prefix, secret = generate_token()
    hashed = hash_secret(secret)

    async with AsyncSessionLocal() as db:
        token_row = MCPAccessToken(
            token_prefix=prefix,
            token_hash=hashed,
            name="test-token",
            scope="read",
        )
        db.add(token_row)
        await db.commit()

    async with AsyncSessionLocal() as db:
        ctx = await verify_bearer_token(f"Bearer {full_token}", db)
        assert ctx is not None
        assert ctx.scope == "read"
        assert ctx.name == "test-token"


@pytest.mark.asyncio
async def test_verify_bearer_token_invalid():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    ctx = await verify_bearer_token("Bearer ccp_bad_bad_secret", None)
    assert ctx is None


@pytest.mark.asyncio
async def test_verify_bearer_token_no_auth():
    ctx = await verify_bearer_token(None, None)
    assert ctx is None


@pytest.mark.asyncio
async def test_verify_bearer_token_revoked():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from datetime import datetime

    full_token, prefix, secret = generate_token()
    hashed = hash_secret(secret)

    async with AsyncSessionLocal() as db:
        token_row = MCPAccessToken(
            token_prefix=prefix,
            token_hash=hashed,
            name="revoked-token",
            scope="read",
            revoked_at=datetime.now(UTC),
        )
        db.add(token_row)
        await db.commit()

    async with AsyncSessionLocal() as db:
        ctx = await verify_bearer_token(f"Bearer {full_token}", db)
        assert ctx is None


@pytest.mark.asyncio
async def test_verify_bearer_token_disabled():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    full_token, prefix, secret = generate_token()
    hashed = hash_secret(secret)

    async with AsyncSessionLocal() as db:
        token_row = MCPAccessToken(
            token_prefix=prefix,
            token_hash=hashed,
            name="disabled-token",
            scope="read",
            enabled=False,
        )
        db.add(token_row)
        await db.commit()

    async with AsyncSessionLocal() as db:
        ctx = await verify_bearer_token(f"Bearer {full_token}", db)
        assert ctx is None


@pytest.mark.asyncio
async def test_mcp_tools_registered():
    from app.mcp_server.server import mcp
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "list_sessions" in names
    assert "get_session" in names
    assert "list_scheduled_messages" in names
    assert "get_scheduled_message" in names
    assert "list_mcp_servers" in names
    assert "get_mcp_server" in names
    assert "get_config" in names
    assert "list_config_files" in names
    assert "list_projects" in names
    assert "agent_mail_whoami" in names
    assert "agent_mail_list_team" in names
    assert "agent_mail_check_inbox" in names
    assert "agent_mail_send_message" in names
    assert "agent_mail_reply" in names
    assert "agent_mail_ack_message" in names
    assert "agent_mail_request_context" in names
    assert "agent_mail_create_handoff" in names
    assert len(tools) == 17


@pytest.mark.asyncio
async def test_mcp_tool_list_projects():
    import uuid

    from app.mcp_server.server import mcp

    # Per-test ``_reset_app_database_tables`` (see conftest.py) gives us a
    # fresh ``Base.metadata`` schema, and the ``_patch_app_database``
    # identity-swap ensures ``app.mcp_server.tools.projects.AsyncSessionLocal``
    # opens sessions on the same test engine we wrote into above. Kanban
    # card 02e80e79 removed the legacy ``try/finally`` cleanup + the
    # session-scoped safety net (``_cleanup_test_projects``) because the
    # temp-file engine is unlinked at process exit and rows never reach
    # ``claude_registry.db`` in the first place.

    uid = uuid.uuid4().hex[:8]
    test_path = f"/tmp/test-{uid}"
    from app.models.database import Project

    async with AsyncSessionLocal() as db:
        p = Project(name=f"mcp-test-{uid}", path=test_path, is_active=True)
        db.add(p)
        await db.commit()

    result = await mcp.call_tool("list_projects", {})
    content_list, _ = result
    text = content_list[0].text
    data = json.loads(text)
    assert "projects" in data
    matching = [p for p in data["projects"] if p["name"] == f"mcp-test-{uid}"]
    assert len(matching) == 1
