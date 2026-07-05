"""Caching of MCP server connectivity-test results."""
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import MCPServerCache

logger = logging.getLogger(__name__)


class MCPCacheService:
    """Reads and writes cached MCP server connectivity/status data."""

    # Max items to cache per list (tools, resources, prompts)
    MAX_CACHED_ITEMS = 200

    async def get_cached_server_info(
        self, name: str, scope: str, db: AsyncSession
    ) -> MCPServerCache | None:
        """Retrieve cached data for a server."""
        result = await db.execute(
            select(MCPServerCache).where(
                MCPServerCache.server_name == name,
                MCPServerCache.server_scope == scope,
            )
        )
        return result.scalar_one_or_none()

    async def update_server_cache(
        self,
        name: str,
        scope: str,
        test_result: dict[str, Any],
        config_hash: str,
        db: AsyncSession,
    ) -> None:
        """Update or create cache entry after testing."""
        cache_entry = await self.get_cached_server_info(name, scope, db)

        tools_list = test_result.get("tools") or []
        resources_list = test_result.get("resources") or []
        prompts_list = test_result.get("prompts") or []
        is_success = test_result.get("success", False)
        now = datetime.now(UTC)

        # Prepare common cache data
        cache_data = {
            "is_connected": is_success,
            "last_tested_at": now,
            "last_error": None if is_success else test_result.get("message"),
            "mcp_server_name": test_result.get("server_name"),
            "mcp_server_version": test_result.get("server_version"),
            "tools": tools_list[:self.MAX_CACHED_ITEMS],
            "tool_count": test_result.get("tool_count", len(tools_list)),
            "resources": resources_list[:self.MAX_CACHED_ITEMS],
            "prompts": prompts_list[:self.MAX_CACHED_ITEMS],
            "resource_count": test_result.get("resource_count", len(resources_list)),
            "prompt_count": test_result.get("prompt_count", len(prompts_list)),
            "capabilities": test_result.get("capabilities"),
            "cached_at": now,
            "config_hash": config_hash,
        }

        if cache_entry:
            for key, value in cache_data.items():
                setattr(cache_entry, key, value)
        else:
            cache_entry = MCPServerCache(
                server_name=name,
                server_scope=scope,
                **cache_data,
            )
            db.add(cache_entry)

        await db.commit()

    async def invalidate_cache(
        self, name: str, scope: str, db: AsyncSession
    ) -> None:
        """Clear cache for a specific server."""
        cache_entry = await self.get_cached_server_info(name, scope, db)
        if cache_entry:
            await db.delete(cache_entry)
            await db.commit()
