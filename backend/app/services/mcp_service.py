"""
Service for managing MCP server configurations.

MCPService merges three responsibilities via inheritance (not composition):
MCPCacheService (status caching), MCPConfigService (config read/write, adds
caching), and MCPServerTestService (connectivity tests, adds config+cache).
Inheritance is required rather than composition here because callers and
tests rely on `self.<method>` calls resolving against a single shared
instance (e.g. tests monkeypatch `svc.get_server` and expect
`svc.test_connection` to see the patch).
"""
import httpx  # noqa: F401 - re-exported so tests can patch mcp_service.httpx.AsyncClient

from app.services.credentials_service import CredentialsService  # noqa: F401 - re-exported for test patching
from app.services.mcp_cache_service import MCPCacheService  # noqa: F401
from app.services.mcp_config_service import MCPConfigService  # noqa: F401
from app.services.mcp_server_test_service import MCPServerTestService


class MCPService(MCPServerTestService):
    """Facade combining MCP config, cache, and connectivity-test services."""
    pass
