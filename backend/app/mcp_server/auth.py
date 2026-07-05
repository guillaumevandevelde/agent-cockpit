"""Bearer token authentication for the MCP server."""
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_token import MCPAccessToken

TOKEN_PREFIX = "ccp"


@dataclass
class TokenContext:
    token_id: int
    scope: str  # "read" | "write"
    agent_name: str | None
    name: str


def generate_token() -> tuple[str, str, str]:
    """Generate a new token. Returns (full_token, prefix, secret)."""
    secret = secrets.token_urlsafe(32)
    prefix = secrets.token_hex(8)
    full_token = f"{TOKEN_PREFIX}_{prefix}_{secret}"
    return full_token, prefix, secret


def hash_secret(secret: str) -> str:
    """Hash a token secret with bcrypt."""
    return bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode()


def verify_secret(secret: str, token_hash: str) -> bool:
    """Verify a secret against a bcrypt hash."""
    return bcrypt.checkpw(secret.encode(), token_hash.encode())


async def verify_bearer_token(authorization: str | None, db: AsyncSession) -> TokenContext | None:
    """Verify a Bearer token and return the context.

    Token format: ccp_<prefix8>_<secret>
    """
    if not authorization:
        return None

    if not authorization.lower().startswith("bearer "):
        return None

    token = authorization[7:].strip()
    parts = token.split("_")
    if len(parts) < 3:
        return None

    scheme, prefix8 = parts[0], parts[1]
    secret = "_".join(parts[2:])

    if scheme != TOKEN_PREFIX or not prefix8 or not secret:
        return None

    if not db:
        return None

    result = await db.execute(
        select(MCPAccessToken).where(
            MCPAccessToken.token_prefix == prefix8,
            MCPAccessToken.revoked_at.is_(None),
            MCPAccessToken.enabled,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    if not verify_secret(secret, row.token_hash):
        return None

    if row.expires_at and row.expires_at < datetime.now(UTC):
        return None

    # Update last_used_at (fire and forget)
    row.last_used_at = datetime.now(UTC)
    await db.commit()

    return TokenContext(
        token_id=row.id,
        scope=row.scope,
        agent_name=row.agent_name,
        name=row.name,
    )
