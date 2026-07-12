"""Reading and writing MCP server configuration files."""
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Project
from app.models.schemas import (
    MCPPrompt,
    MCPResource,
    MCPServer,
    MCPServerApprovalMode,
    MCPServerApprovalSettings,
    MCPServerCreate,
    MCPServerUpdate,
    MCPTool,
)
from app.services.mcp_cache_service import MCPCacheService
from app.utils.file_utils import read_json_file, write_json_file
from app.utils.path_utils import (
    get_claude_user_config_file,
    get_claude_user_settings_file,
    get_installed_plugins_file,
    get_managed_mcp_config_file,
    get_project_mcp_config_file,
)

logger = logging.getLogger(__name__)


class UnregisteredProjectPathError(ValueError):
    """Raised when a client-supplied project_path is not a registered project.

    Writing a project-scoped ``.mcp.json`` is gated on the target path existing
    in the ``projects`` table so an unauthenticated API caller can't have the
    server write config into an arbitrary filesystem location.
    """


class MCPConfigService(MCPCacheService):
    """Reads, writes, and lists MCP server configuration across scopes."""

    SENSITIVE_PATTERNS = ["KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"]

    @staticmethod
    def _mask_sensitive_env(env: dict[str, str] | None) -> dict[str, str] | None:
        """Mask sensitive environment variables containing KEY, TOKEN, or SECRET."""
        if not env:
            return env

        masked = {}
        for key, value in env.items():
            if any(pattern in key.upper() for pattern in MCPConfigService.SENSITIVE_PATTERNS):
                masked[key] = "***MASKED***"
            else:
                masked[key] = value

        return masked

    def _create_mcp_server(
        self, name: str, config: dict[str, Any], scope: str
    ) -> MCPServer:
        """Create an MCPServer instance from configuration."""
        return MCPServer(
            name=name,
            type=config.get("type", "stdio"),
            scope=scope,
            command=config.get("command"),
            args=config.get("args"),
            url=config.get("url"),
            headers=config.get("headers"),
            env=self._mask_sensitive_env(config.get("env")),
        )

    @staticmethod
    def _read_user_mcp_config(project_path: str | None = None) -> dict[str, Any]:
        """
        Read MCP configuration from user-level ~/.claude.json.

        MCP servers can be defined in two places:
        1. Top-level mcpServers (global to all projects)
        2. Per-project in projects[path].mcpServers

        Args:
            project_path: Optional project path to read project-specific servers.
                         If None, only reads global servers.

        Returns:
            Dict of MCP server configurations
        """
        user_config_path = get_claude_user_config_file()
        config = read_json_file(user_config_path)

        if not config:
            return {}

        servers = {}

        # Read top-level mcpServers (global)
        if "mcpServers" in config:
            servers.update(config.get("mcpServers", {}))

        # Read project-specific mcpServers only if a project is active
        if project_path:
            projects = config.get("projects", {})
            if project_path in projects:
                project_config = projects[project_path]
                project_servers = project_config.get("mcpServers", {})
                servers.update(project_servers)

        return servers

    @staticmethod
    def _read_project_mcp_config(project_path: str | None = None) -> dict[str, Any]:
        """Read MCP configuration from project-level .mcp.json."""
        project_config_path = get_project_mcp_config_file(project_path)
        config = read_json_file(project_config_path)

        if not config or "mcpServers" not in config:
            return {}

        return config.get("mcpServers", {})

    @staticmethod
    def _read_plugin_mcp_servers() -> list[dict[str, Any]]:
        """
        Read MCP servers from installed plugins.

        Plugins can define MCP servers in:
        1. .mcp.json file in the plugin root
        2. .claude-plugin/plugin.json under the "mcpServers" key

        The server names are prefixed with 'plugin:{plugin_name}:{server_name}'.

        Returns:
            List of MCP server configurations with metadata
        """
        installed_plugins_path = get_installed_plugins_file()
        installed_plugins = read_json_file(installed_plugins_path)

        if not installed_plugins or "plugins" not in installed_plugins:
            return []

        plugin_servers = []
        plugins_data = installed_plugins.get("plugins", {})

        for plugin_key, installations in plugins_data.items():
            # plugin_key format: "{plugin_name}@{marketplace}"
            if "@" not in plugin_key:
                continue

            plugin_name, marketplace = plugin_key.rsplit("@", 1)

            # Get the first (usually only) installation
            if not installations or not isinstance(installations, list):
                continue

            installation = installations[0]
            install_path = installation.get("installPath")

            if not install_path:
                continue

            install_path = Path(install_path)
            mcp_servers = {}

            # Try .mcp.json first (legacy format)
            plugin_mcp_path = install_path / ".mcp.json"
            plugin_mcp_config = read_json_file(plugin_mcp_path)
            if plugin_mcp_config and isinstance(plugin_mcp_config, dict):
                # .mcp.json may have {"mcpServers": {...}} or be a flat dict of servers
                if "mcpServers" in plugin_mcp_config and isinstance(plugin_mcp_config["mcpServers"], dict):
                    mcp_servers.update(plugin_mcp_config["mcpServers"])
                else:
                    mcp_servers.update(plugin_mcp_config)

            # Also check .claude-plugin/plugin.json for mcpServers
            plugin_json_path = install_path / ".claude-plugin" / "plugin.json"
            plugin_json = read_json_file(plugin_json_path)
            if plugin_json and "mcpServers" in plugin_json:
                mcp_servers_value = plugin_json["mcpServers"]
                if isinstance(mcp_servers_value, dict):
                    mcp_servers.update(mcp_servers_value)
                elif isinstance(mcp_servers_value, str):
                    # String is a relative path to another JSON file (e.g. "./.mcp.json")
                    ref_path = (plugin_json_path.parent / mcp_servers_value).resolve()
                    # Ensure the resolved path stays within the plugin install directory
                    if not str(ref_path).startswith(str(install_path.resolve()) + "/"):
                        continue
                    ref_data = read_json_file(ref_path)
                    if ref_data and isinstance(ref_data, dict):
                        if "mcpServers" in ref_data and isinstance(ref_data["mcpServers"], dict):
                            mcp_servers.update(ref_data["mcpServers"])

            if not mcp_servers:
                continue

            # Each key in mcp_servers is a server definition
            for server_name, server_config in mcp_servers.items():
                # Prefix with plugin identifier to match Claude Code's format
                # Format: plugin:{plugin_name}:{server_name}
                prefixed_name = f"plugin:{plugin_name}:{server_name}"
                plugin_servers.append({
                    "name": prefixed_name,
                    "config": server_config,
                    "plugin_name": plugin_name,
                    "marketplace": marketplace,
                })

        return plugin_servers

    @staticmethod
    def _read_managed_mcp_config() -> dict[str, Any]:
        """
        Read MCP configuration from managed config file (read-only).

        This file is typically managed by enterprise/system admins and
        cannot be modified by users. Servers from this file are always
        enabled and marked with scope="managed".

        Returns:
            Dict of MCP server configurations
        """
        managed_config_path = get_managed_mcp_config_file()
        config = read_json_file(managed_config_path)

        if not config or "mcpServers" not in config:
            return {}

        return config.get("mcpServers", {})

    @staticmethod
    async def _write_user_mcp_config(servers: dict[str, Any]) -> bool:
        """Write MCP configuration to user-level ~/.claude.json."""
        user_config_path = get_claude_user_config_file()
        config = read_json_file(user_config_path) or {}

        config["mcpServers"] = servers
        return await write_json_file(user_config_path, config)

    @staticmethod
    async def _assert_registered_project_path(
        project_path: str | None, db: AsyncSession | None
    ) -> None:
        """Reject a client-supplied project_path that isn't a registered project.

        ``project_path is None`` falls back to the server's cwd — not a
        client-controlled location — so it's allowed. Any explicit path must
        exist in the ``projects`` table; validating it requires a db session,
        so a missing session with an explicit path is refused rather than
        silently trusted.
        """
        if project_path is None:
            return
        if db is None:
            raise UnregisteredProjectPathError(
                f"Project path '{project_path}' cannot be validated without a "
                "database session"
            )
        result = await db.execute(select(Project).where(Project.path == project_path))
        if result.scalar_one_or_none() is None:
            raise UnregisteredProjectPathError(
                f"Project path '{project_path}' is not a registered project"
            )

    @staticmethod
    async def _write_project_mcp_config(
        servers: dict[str, Any],
        project_path: str | None = None,
        db: AsyncSession | None = None,
    ) -> bool:
        """Write MCP configuration to project-level .mcp.json.

        The target path is validated against the ``projects`` table first (see
        ``_assert_registered_project_path``); an unregistered path raises
        ``UnregisteredProjectPathError`` before anything is written.
        """
        await MCPConfigService._assert_registered_project_path(project_path, db)
        project_config_path = get_project_mcp_config_file(project_path)
        config = read_json_file(project_config_path) or {}

        config["mcpServers"] = servers
        return await write_json_file(project_config_path, config)

    async def list_servers(
        self, project_path: str | None = None, db: AsyncSession | None = None
    ) -> list[MCPServer]:
        """
        List all MCP servers from user, project, plugin, and managed scopes.

        Args:
            project_path: Optional path to project directory
            db: Optional database session for cache lookup

        Returns:
            List of MCPServer objects with cached data merged
        """
        servers = []
        disabled_servers = self.get_disabled_servers()

        # Read managed servers (admin-enforced, read-only)
        managed_servers = self._read_managed_mcp_config()
        for name, config in managed_servers.items():
            server = self._create_mcp_server(name, config, "managed")
            server.source = "enterprise"  # Mark source for UI
            servers.append(server)

        # Read user-level servers (including project-specific from ~/.claude.json)
        user_servers = self._read_user_mcp_config(project_path)
        for name, config in user_servers.items():
            servers.append(self._create_mcp_server(name, config, "user"))

        # Read project-level servers
        project_servers = self._read_project_mcp_config(project_path)
        for name, config in project_servers.items():
            servers.append(self._create_mcp_server(name, config, "project"))

        # Read plugin-provided servers
        plugin_servers = self._read_plugin_mcp_servers()
        for plugin_server in plugin_servers:
            server = self._create_mcp_server(
                plugin_server["name"], plugin_server["config"], "plugin"
            )
            server.source = plugin_server.get("plugin_name")
            servers.append(server)

        # Merge disabled state from settings
        for server in servers:
            server.disabled = server.name in disabled_servers

        # Merge cached data if database session is provided
        if db:
            for server in servers:
                cache_entry = await self.get_cached_server_info(server.name, server.scope, db)
                if cache_entry:
                    server.is_connected = cache_entry.is_connected
                    server.last_tested_at = cache_entry.last_tested_at.isoformat() if cache_entry.last_tested_at else None
                    server.last_error = cache_entry.last_error
                    server.mcp_server_name = cache_entry.mcp_server_name
                    server.mcp_server_version = cache_entry.mcp_server_version
                    server.tool_count = cache_entry.tool_count
                    server.resource_count = cache_entry.resource_count
                    server.prompt_count = cache_entry.prompt_count
                    server.capabilities = cache_entry.capabilities
                    # Convert tools from JSON to MCPTool objects
                    if cache_entry.tools:
                        server.tools = [MCPTool(**tool) for tool in cache_entry.tools]
                    if cache_entry.resources:
                        server.resources = [MCPResource(**r) for r in cache_entry.resources]
                    if cache_entry.prompts:
                        server.prompts = [MCPPrompt(**p) for p in cache_entry.prompts]

        return servers

    async def get_server(self, name: str, scope: str) -> MCPServer | None:
        """
        Get a specific MCP server configuration.

        Args:
            name: Server name
            scope: Server scope ("user", "project", "plugin", or "managed")

        Returns:
            MCPServer object or None if not found
        """
        if scope == "managed":
            servers = self._read_managed_mcp_config()
            if name not in servers:
                return None
            server = self._create_mcp_server(name, servers[name], scope)
            server.source = "enterprise"
            return server
        elif scope == "user":
            servers = self._read_user_mcp_config()
            if name not in servers:
                return None
            return self._create_mcp_server(name, servers[name], scope)
        elif scope == "project":
            servers = self._read_project_mcp_config()
            if name not in servers:
                return None
            return self._create_mcp_server(name, servers[name], scope)
        elif scope == "plugin":
            plugin_servers = self._read_plugin_mcp_servers()
            for plugin_server in plugin_servers:
                if plugin_server["name"] == name:
                    server = self._create_mcp_server(name, plugin_server["config"], scope)
                    server.source = plugin_server.get("plugin_name")
                    return server
            return None
        else:
            return None

    async def add_server(
        self,
        server: MCPServerCreate,
        project_path: str | None = None,
        db: AsyncSession | None = None,
    ) -> MCPServer:
        """
        Add a new MCP server to the appropriate config file.

        Args:
            server: MCP server configuration to add
            project_path: Optional path to project directory
            db: Database session used to validate a project-scoped path

        Returns:
            Created MCPServer object
        """
        logger.info("Adding MCP server", extra={"server": server.name, "scope": server.scope, "type": server.type})
        # Build config from server fields, excluding None values
        config = {"type": server.type}
        for field in ("command", "args", "url", "headers", "env"):
            value = getattr(server, field)
            if value:
                config[field] = value

        # Read, update, and write config
        if server.scope == "user":
            servers = self._read_user_mcp_config()
            servers[server.name] = config
            await self._write_user_mcp_config(servers)
        else:
            servers = self._read_project_mcp_config(project_path)
            servers[server.name] = config
            await self._write_project_mcp_config(servers, project_path, db)

        logger.info("MCP server added", extra={"server": server.name, "scope": server.scope})
        return self._create_mcp_server(server.name, config, server.scope)

    async def update_server(
        self,
        name: str,
        server: MCPServerUpdate,
        scope: str,
        project_path: str | None = None,
        db: AsyncSession | None = None,
    ) -> MCPServer | None:
        """
        Update an existing MCP server configuration.

        Args:
            name: Server name
            server: Updated server configuration
            scope: Server scope ("user" or "project")
            project_path: Optional path to project directory
            db: Database session used to validate a project-scoped path

        Returns:
            Updated MCPServer object or None if not found
        """
        logger.info("Updating MCP server", extra={"server": name, "scope": scope})
        # Read existing servers
        if scope == "user":
            servers = self._read_user_mcp_config()
        else:
            servers = self._read_project_mcp_config(project_path)

        if name not in servers:
            logger.warning("MCP server not found for update", extra={"server": name, "scope": scope})
            return None

        # Update config with non-None values
        config = servers[name]
        for field in ("type", "command", "args", "url", "headers", "env"):
            value = getattr(server, field)
            if value is not None:
                config[field] = value

        servers[name] = config

        # Write updated config
        if scope == "user":
            await self._write_user_mcp_config(servers)
        else:
            await self._write_project_mcp_config(servers, project_path, db)

        logger.info("MCP server updated", extra={"server": name, "scope": scope})
        return self._create_mcp_server(name, config, scope)

    async def remove_server(
        self,
        name: str,
        scope: str,
        project_path: str | None = None,
        db: AsyncSession | None = None,
    ) -> bool:
        """
        Remove an MCP server from configuration.

        Args:
            name: Server name
            scope: Server scope ("user" or "project")
            project_path: Optional path to project directory
            db: Database session used to validate a project-scoped path

        Returns:
            True if removed, False if not found
        """
        logger.info("Removing MCP server", extra={"server": name, "scope": scope})
        # Read existing servers
        if scope == "user":
            servers = self._read_user_mcp_config()
        else:
            servers = self._read_project_mcp_config(project_path)

        if name not in servers:
            logger.warning("MCP server not found for removal", extra={"server": name, "scope": scope})
            return False

        # Remove server
        del servers[name]

        # Write updated config
        if scope == "user":
            await self._write_user_mcp_config(servers)
        else:
            await self._write_project_mcp_config(servers, project_path, db)

        logger.info("MCP server removed", extra={"server": name, "scope": scope})
        return True

    def get_approval_settings(self) -> MCPServerApprovalSettings:
        """
        Get MCP server approval settings from user settings.

        These settings control automatic tool approval for MCP servers.

        Returns:
            MCPServerApprovalSettings object
        """
        settings_path = get_claude_user_settings_file()
        config = read_json_file(settings_path)

        if not config:
            return MCPServerApprovalSettings()

        mcp_settings = config.get("mcpServerApproval", {})
        default_mode = mcp_settings.get("defaultMode", "ask-every-time")
        server_overrides = []

        for server_name, mode in mcp_settings.get("serverOverrides", {}).items():
            server_overrides.append(
                MCPServerApprovalMode(server_name=server_name, mode=mode)
            )

        return MCPServerApprovalSettings(
            default_mode=default_mode,
            server_overrides=server_overrides,
        )

    async def update_approval_settings(
        self, settings: MCPServerApprovalSettings
    ) -> MCPServerApprovalSettings:
        """
        Update MCP server approval settings.

        Args:
            settings: New approval settings

        Returns:
            Updated MCPServerApprovalSettings object
        """
        settings_path = get_claude_user_settings_file()
        config = read_json_file(settings_path) or {}

        # Build the mcpServerApproval structure
        mcp_approval = {
            "defaultMode": settings.default_mode,
            "serverOverrides": {
                override.server_name: override.mode
                for override in settings.server_overrides
            },
        }

        config["mcpServerApproval"] = mcp_approval
        await write_json_file(settings_path, config)

        return settings

    def get_disabled_servers(self) -> set:
        """Get the set of disabled MCP server names from settings."""
        settings_path = get_claude_user_settings_file()
        config = read_json_file(settings_path)
        if not config:
            return set()
        return set(config.get("disabledMcpServers", []))

    async def toggle_server(self, name: str, disabled: bool) -> bool:
        """
        Toggle an MCP server's disabled state in settings.

        Args:
            name: Server name
            disabled: Whether to disable the server

        Returns:
            True if successful
        """
        logger.info("Toggling MCP server", extra={"server": name, "disabled": disabled})
        settings_path = get_claude_user_settings_file()
        config = read_json_file(settings_path) or {}

        disabled_list = set(config.get("disabledMcpServers", []))

        if disabled:
            disabled_list.add(name)
        else:
            disabled_list.discard(name)

        config["disabledMcpServers"] = sorted(disabled_list)
        result = await write_json_file(settings_path, config)
        if result:
            logger.info("MCP server toggled", extra={"server": name, "disabled": disabled})
        else:
            logger.warning("Failed to write settings for MCP server toggle", extra={"server": name})
        return result
