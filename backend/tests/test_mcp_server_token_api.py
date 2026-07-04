"""API tests for the MCP access-token CRUD endpoints (create/list/revoke)."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import AsyncSessionLocal, Base, engine
from app.main import app
from app.models.mcp_token import MCPAccessToken


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _delete_token(token_id: int) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(MCPAccessToken, token_id)
        if row:
            await db.delete(row)
            await db.commit()


@pytest.mark.asyncio
async def test_create_token_matches_response_model():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.api.v1.mcp_server import TokenResponse

    token_id = None
    try:
        async with _client() as ac:
            r = await ac.post(
                "/api/v1/mcp-server/tokens",
                json={"name": "test-token", "scope": "read", "agent_name": "tester"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        token_id = body["id"]
        TokenResponse.model_validate(body)
        assert body["token"].startswith("ccp_")
    finally:
        if token_id is not None:
            await _delete_token(token_id)


@pytest.mark.asyncio
async def test_list_tokens_matches_response_model():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.api.v1.mcp_server import TokenListResponse

    token_id = None
    try:
        async with _client() as ac:
            create_r = await ac.post(
                "/api/v1/mcp-server/tokens", json={"name": "list-test-token"},
            )
            token_id = create_r.json()["id"]

            r = await ac.get("/api/v1/mcp-server/tokens")
        assert r.status_code == 200, r.text
        body = r.json()
        TokenListResponse.model_validate(body)
        assert any(t["id"] == token_id for t in body["tokens"])
        assert all("token" not in t for t in body["tokens"])
    finally:
        if token_id is not None:
            await _delete_token(token_id)


@pytest.mark.asyncio
async def test_revoke_token_matches_response_model():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.api.v1.mcp_server import TokenRevokeResponse

    token_id = None
    try:
        async with _client() as ac:
            create_r = await ac.post(
                "/api/v1/mcp-server/tokens", json={"name": "revoke-test-token"},
            )
            token_id = create_r.json()["id"]

            r = await ac.delete(f"/api/v1/mcp-server/tokens/{token_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        TokenRevokeResponse.model_validate(body)
        assert body == {"revoked": True, "id": token_id}
    finally:
        if token_id is not None:
            await _delete_token(token_id)


@pytest.mark.asyncio
async def test_revoke_token_404_for_unknown_id():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _client() as ac:
        r = await ac.delete("/api/v1/mcp-server/tokens/999999999")
    assert r.status_code == 404
