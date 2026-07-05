"""Service for proxying MCP Registry API and generating install configs."""
import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.models.schemas import MCPServerCreate
from app.services.mcp_service import MCPService

logger = logging.getLogger(__name__)
REGISTRY_BASE_URL = "https://registry.modelcontextprotocol.io/v0.1"
REQUEST_TIMEOUT = 15.0


class MCPRegistryService:
    """Proxy for the official MCP Registry API with install config generation."""

    @staticmethod
    async def search_servers(
        query: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search the MCP registry for servers."""
        params: dict[str, Any] = {"limit": limit, "version": "latest"}
        if query:
            params["search"] = query
        if cursor:
            params["cursor"] = cursor

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(f"{REGISTRY_BASE_URL}/servers", params=params)
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def get_server_detail(
        server_name: str, version: str = "latest"
    ) -> dict[str, Any]:
        """Get detail for a specific server version."""
        encoded_name = quote(server_name, safe="")
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                f"{REGISTRY_BASE_URL}/servers/{encoded_name}/versions/{version}"
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def get_server_versions(server_name: str) -> dict[str, Any]:
        """Get all versions for a server."""
        encoded_name = quote(server_name, safe="")
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                f"{REGISTRY_BASE_URL}/servers/{encoded_name}/versions"
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def generate_install_config(
        *,
        package_registry_type: str | None = None,
        package_identifier: str | None = None,
        package_version: str | None = None,
        package_runtime_hint: str | None = None,
        package_arguments: dict[str, str] | None = None,
        remote_type: str | None = None,
        remote_url: str | None = None,
        remote_headers: dict[str, str] | None = None,
        env_values: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Generate an mcpServers config entry from registry package/remote data.

        Returns a dict suitable for writing to ~/.claude.json or .mcp.json.
        """
        config: dict[str, Any] = {}

        if package_registry_type and package_identifier:
            config = MCPRegistryService._generate_package_config(
                registry_type=package_registry_type,
                identifier=package_identifier,
                version=package_version,
                runtime_hint=package_runtime_hint,
                arguments=package_arguments,
            )
        elif remote_type and remote_url:
            config = MCPRegistryService._generate_remote_config(
                remote_type=remote_type,
                url=remote_url,
                headers=remote_headers,
            )

        if env_values:
            config["env"] = env_values

        return config

    @staticmethod
    def _generate_package_config(
        *,
        registry_type: str,
        identifier: str,
        version: str | None,
        runtime_hint: str | None,
        arguments: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Generate stdio config for a package-based server."""
        config: dict[str, Any] = {"type": "stdio"}
        args: list[str] = []

        if registry_type == "npm":
            command = runtime_hint or "npx"
            config["command"] = command
            if command == "npx":
                args.append("-y")
            pkg = f"{identifier}@{version}" if version else identifier
            args.append(pkg)

        elif registry_type == "pypi":
            config["command"] = runtime_hint or "uvx"
            args.append(identifier)

        elif registry_type == "oci":
            config["command"] = "docker"
            args.extend(["run", "-i", "--rm", identifier])

        else:
            # Fallback for unknown types (nuget, mcpb, etc.)
            config["command"] = runtime_hint or identifier
            args.append(identifier)

        # Append user-provided package arguments
        if arguments:
            for name, value in arguments.items():
                if value:
                    args.extend([f"--{name}", value])

        config["args"] = args
        return config

    @staticmethod
    def _generate_remote_config(
        *,
        remote_type: str,
        url: str,
        headers: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Generate http/sse config for a remote server."""
        # Map registry transport types to Claude Code types
        config_type = "http" if remote_type == "streamable-http" else remote_type
        config: dict[str, Any] = {"type": config_type, "url": url}

        if headers:
            config["headers"] = headers

        return config

    @staticmethod
    async def install_server(
        *,
        server_name: str,
        scope: str,
        config: dict[str, Any],
        project_path: str | None = None,
    ) -> dict[str, Any]:
        """Install a registry server by writing config via MCPService.

        Returns the generated config entry.
        """
        server_type = config.get("type", "stdio")

        server_create = MCPServerCreate(
            name=server_name,
            type=server_type,
            scope=scope,
            command=config.get("command"),
            args=config.get("args"),
            url=config.get("url"),
            headers=config.get("headers"),
            env=config.get("env"),
        )

        mcp_service = MCPService()
        await mcp_service.add_server(server_create, project_path)

        return config
