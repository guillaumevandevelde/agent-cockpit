"""FastAPI endpoint for the Claude Cockpit MCP server (Streamable HTTP)."""
import logging
from datetime import UTC

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.mcp_server.auth import TokenContext, generate_token, hash_secret, verify_bearer_token
from app.mcp_server.server import mcp
from app.models.mcp_token import MCPAccessToken

logger = logging.getLogger(__name__)

router = APIRouter()

MCP_HEADERS = {"MCP-Protocol-Version": "2025-03-26"}


async def _get_ctx(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenContext:
    """Extract and verify the Bearer token."""
    auth = request.headers.get("Authorization")
    ctx = await verify_bearer_token(auth, db)
    if not ctx:
        raise PermissionError("Unauthorized")
    return ctx


@router.post("/mcp-server")
async def handle_mcp_post(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle MCP JSON-RPC requests via Streamable HTTP."""
    auth = request.headers.get("Authorization")
    ctx = await verify_bearer_token(auth, db)
    if not ctx:
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized"},
            headers={**MCP_HEADERS, "WWW-Authenticate": 'Bearer realm="claude-cockpit"'},
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"}, headers=MCP_HEADERS)

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    logger.info("[mcp] method=%s id=%s scope=%s", method, req_id, ctx.scope)

    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "claude-cockpit", "version": "1.0.0"},
            }
            return JSONResponse(
                content={"jsonrpc": "2.0", "id": req_id, "result": result},
                headers=MCP_HEADERS,
            )

        if method == "notifications/initialized":
            return Response(status_code=202, headers=MCP_HEADERS)

        if method == "tools/list":
            tools = await mcp.list_tools()
            tool_list = []
            for t in tools:
                tool_list.append({
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else {},
                })
            return JSONResponse(
                content={"jsonrpc": "2.0", "id": req_id, "result": {"tools": tool_list}},
                headers=MCP_HEADERS,
            )

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            result = await mcp.call_tool(tool_name, arguments)
            # FastMCP's call_tool(..., convert_result=True) returns a
            # (content_blocks, structured_result) tuple when the tool has an
            # output schema (every @mcp.tool() here does, since they return a
            # plain str). Iterating the tuple directly (as if it were the
            # content list) yields the two tuple elements themselves, not
            # content blocks, and neither has `.text` -- str(item) then dumps
            # a mangled repr instead of the tool's actual output.
            items = result[0] if isinstance(result, tuple) else result
            content = []
            for item in items:
                if hasattr(item, "text"):
                    content.append({"type": "text", "text": item.text})
                else:
                    content.append({"type": "text", "text": str(item)})
            return JSONResponse(
                content={"jsonrpc": "2.0", "id": req_id, "result": {"content": content}},
                headers=MCP_HEADERS,
            )

        if method == "ping":
            return JSONResponse(
                content={"jsonrpc": "2.0", "id": req_id, "result": {}},
                headers=MCP_HEADERS,
            )

        return JSONResponse(
            content={"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}},
            headers=MCP_HEADERS,
        )

    except Exception as e:
        logger.exception("[mcp] error calling %s", method)
        return JSONResponse(
            content={"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}},
            headers=MCP_HEADERS,
        )


@router.get("/mcp-server")
async def handle_mcp_get(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle MCP GET requests (health check / SSE init)."""
    auth = request.headers.get("Authorization")
    ctx = await verify_bearer_token(auth, db)
    if not ctx:
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized"},
            headers={**MCP_HEADERS, "WWW-Authenticate": 'Bearer realm="claude-cockpit"'},
        )

    return JSONResponse(
        content={
            "name": "claude-cockpit",
            "version": "1.0.0",
            "transport": "streamable-http",
            "scope": ctx.scope,
        },
        headers=MCP_HEADERS,
    )


@router.delete("/mcp-server")
async def handle_mcp_delete():
    """Handle MCP session termination."""
    return Response(status_code=204, headers=MCP_HEADERS)


# --- Token management endpoints ---


class TokenCreateRequest(BaseModel):
    name: str
    scope: str = "read"
    agent_name: str | None = None


class TokenResponse(BaseModel):
    id: int
    token: str
    name: str
    scope: str
    agent_name: str | None = None
    created_at: str | None = None


class TokenInfo(BaseModel):
    id: int
    name: str
    scope: str
    agent_name: str | None = None
    enabled: bool
    token_prefix: str
    last_used_at: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    revoked_at: str | None = None


class TokenListResponse(BaseModel):
    tokens: list[TokenInfo]


class TokenRevokeResponse(BaseModel):
    revoked: bool
    id: int


@router.post("/mcp-server/tokens", response_model=TokenResponse)
async def create_token(payload: TokenCreateRequest, db: AsyncSession = Depends(get_db)):
    """Create a new MCP access token. Returns the full token (shown once)."""
    full_token, prefix, secret = generate_token()
    hashed = hash_secret(secret)

    token_row = MCPAccessToken(
        token_prefix=prefix,
        token_hash=hashed,
        name=payload.name,
        scope=payload.scope,
        agent_name=payload.agent_name,
    )
    db.add(token_row)
    await db.commit()
    await db.refresh(token_row)

    return {
        "id": token_row.id,
        "token": full_token,
        "name": token_row.name,
        "scope": token_row.scope,
        "agent_name": token_row.agent_name,
        "created_at": token_row.created_at.isoformat() if token_row.created_at else None,
    }


@router.get("/mcp-server/tokens", response_model=TokenListResponse)
async def list_tokens(db: AsyncSession = Depends(get_db)):
    """List all MCP access tokens (without secrets)."""
    result = await db.execute(
        select(MCPAccessToken).order_by(MCPAccessToken.created_at.desc())
    )
    rows = result.scalars().all()

    return {
        "tokens": [
            {
                "id": t.id,
                "name": t.name,
                "scope": t.scope,
                "agent_name": t.agent_name,
                "enabled": t.enabled,
                "token_prefix": t.token_prefix,
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
                "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
            }
            for t in rows
        ]
    }


@router.delete("/mcp-server/tokens/{token_id}", response_model=TokenRevokeResponse)
async def revoke_token(token_id: int, db: AsyncSession = Depends(get_db)):
    """Revoke an MCP access token."""
    from datetime import datetime

    result = await db.execute(
        select(MCPAccessToken).where(MCPAccessToken.id == token_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        return JSONResponse(status_code=404, content={"error": "Token not found"})

    row.revoked_at = datetime.now(UTC)
    row.enabled = False
    await db.commit()

    return {"revoked": True, "id": token_id}
