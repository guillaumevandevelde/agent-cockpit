"""Local plugin discovery and listing."""
import json
import logging
from pathlib import Path
from typing import Any

from ..models.schemas import (
    Plugin,
    PluginComponent,
    PluginHook,
    PluginListResponse,
    PluginLSPConfig,
    PluginValidationResult,
)
from ..utils.file_utils import read_json_file
from ..utils.path_utils import (
    get_claude_user_plugins_dir,
    get_claude_user_settings_file,
    get_project_plugins_dir,
)
from .plugin_descriptions import get_plugin_info

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Discovers and lists locally installed Claude Code plugins."""

    def list_installed_plugins(
        self, project_path: str | None = None
    ) -> PluginListResponse:
        """
        List all installed plugins from user and project scopes.

        Includes:
        - Locally installed plugins (directories with .claude-plugin/plugin.json)
        - Enabled plugins from settings.json (enabledPlugins configuration)

        Args:
            project_path: Optional project directory path

        Returns:
            PluginListResponse with list of installed plugins
        """
        plugins = []

        # First, get enabled plugins from settings.json
        plugins.extend(self._get_enabled_plugins_from_settings())

        # User-level local plugins
        user_plugins_dir = get_claude_user_plugins_dir()
        if user_plugins_dir.exists():
            local_plugins = self._scan_plugins_directory(user_plugins_dir, scope="user")
            # Mark local plugins and avoid duplicates
            for plugin in local_plugins:
                plugin.source = "local"
                if not any(p.name == plugin.name for p in plugins):
                    plugins.append(plugin)

        # Project-level local plugins
        if project_path:
            project_plugins_dir = get_project_plugins_dir(project_path)
            if project_plugins_dir.exists():
                local_plugins = self._scan_plugins_directory(project_plugins_dir, scope="project")
                for plugin in local_plugins:
                    plugin.source = "local-project"
                    if not any(p.name == plugin.name for p in plugins):
                        plugins.append(plugin)

        return PluginListResponse(plugins=plugins)

    def _get_installed_plugins_map(self) -> dict[str, Any]:
        """
        Read installed_plugins.json to get install paths.

        Returns:
            Dict mapping plugin key to install info
        """
        installed_file = get_claude_user_plugins_dir() / "installed_plugins.json"
        if not installed_file.exists():
            return {}

        data = read_json_file(installed_file)
        if not data or "plugins" not in data:
            return {}

        return data.get("plugins", {})

    def _get_enabled_plugins_from_settings(self) -> list[Plugin]:
        """
        Read enabled plugins from ~/.claude/settings.json.

        Also scans actual install directories from installed_plugins.json
        to get component information (agents, commands, skills, etc).

        Returns:
            List of Plugin objects for enabled plugins
        """
        plugins = []

        settings_file = get_claude_user_settings_file()
        if not settings_file.exists():
            return plugins

        settings_data = read_json_file(settings_file)
        if not settings_data:
            return plugins

        enabled_plugins = settings_data.get("enabledPlugins", {})
        if not isinstance(enabled_plugins, dict):
            return plugins

        # Get install paths from installed_plugins.json
        installed_map = self._get_installed_plugins_map()

        for plugin_key, is_enabled in enabled_plugins.items():
            # Parse plugin key format: "name@source" or just "name"
            if "@" in plugin_key:
                name, source = plugin_key.rsplit("@", 1)
            else:
                name = plugin_key
                source = "unknown"

            # Look up detailed info from hardcoded descriptions
            plugin_info = get_plugin_info(name)

            # Get install path and scan for components
            install_info = installed_map.get(plugin_key, [])
            install_path = None
            version = None
            if install_info and len(install_info) > 0:
                install_path = install_info[0].get("installPath")
                version = install_info[0].get("version")

            # Scan plugin directory for components
            components = []
            skill_count = 0
            agent_count = 0
            hook_count = 0
            mcp_count = 0
            lsp_count = 0
            hooks = None
            lsp_configs = None
            readme = None

            if install_path:
                plugin_dir = Path(install_path)
                if plugin_dir.exists():
                    # Count commands as skills
                    commands_dir = plugin_dir / "commands"
                    if commands_dir.exists():
                        for cmd_file in commands_dir.iterdir():
                            if cmd_file.suffix == ".md":
                                skill_count += 1
                                components.append(
                                    PluginComponent(
                                        type="command",
                                        name=cmd_file.stem,
                                        description=f"Command: {cmd_file.stem}",
                                    )
                                )

                    # Count skills
                    skills_dir = plugin_dir / "skills"
                    if skills_dir.exists():
                        for skill_item in skills_dir.iterdir():
                            if skill_item.is_dir() or skill_item.suffix == ".md":
                                skill_count += 1
                                components.append(
                                    PluginComponent(
                                        type="skill",
                                        name=skill_item.stem if skill_item.is_file() else skill_item.name,
                                        description=f"Skill: {skill_item.stem if skill_item.is_file() else skill_item.name}",
                                    )
                                )

                    # Count agents
                    agents_dir = plugin_dir / "agents"
                    if agents_dir.exists():
                        for agent_file in agents_dir.iterdir():
                            if agent_file.suffix == ".md":
                                agent_count += 1
                                components.append(
                                    PluginComponent(
                                        type="agent",
                                        name=agent_file.stem,
                                        description=f"Agent: {agent_file.stem}",
                                    )
                                )

                    # Count MCP servers
                    mcp_dir = plugin_dir / "mcp-servers"
                    if mcp_dir.exists():
                        mcp_count = self._count_directory_items(mcp_dir)

                    # Parse hooks
                    hooks = self._parse_plugin_hooks(plugin_dir)
                    if hooks:
                        hook_count = len(hooks)

                    # Parse LSP configs
                    lsp_configs = self._parse_lsp_config(plugin_dir)
                    if lsp_configs:
                        lsp_count = len(lsp_configs)

                    # Read README
                    readme = self._read_plugin_readme(plugin_dir)

            plugin = Plugin(
                name=name,
                version=version,
                source=source,
                enabled=bool(is_enabled),
                description=plugin_info.get("description", f"Plugin from {source}") if plugin_info else f"Plugin from {source}",
                usage=plugin_info.get("usage") if plugin_info else None,
                examples=plugin_info.get("examples") if plugin_info else None,
                components=components,
                skill_count=skill_count,
                agent_count=agent_count,
                hook_count=hook_count,
                mcp_count=mcp_count,
                lsp_count=lsp_count,
                hooks=hooks,
                lsp_configs=lsp_configs,
                readme=readme,
            )
            plugins.append(plugin)

        return plugins

    def _scan_plugins_directory(self, plugins_dir: Path, scope: str = "user") -> list[Plugin]:
        """
        Scan a plugins directory for installed plugins.

        Looks for directories containing .claude-plugin/plugin.json.

        Args:
            plugins_dir: Path to plugins directory
            scope: Installation scope ("user", "project", "local")

        Returns:
            List of Plugin objects
        """
        plugins = []

        if not plugins_dir.exists():
            return plugins

        # Iterate through subdirectories
        for plugin_dir in plugins_dir.iterdir():
            if not plugin_dir.is_dir():
                continue

            # Check for .claude-plugin/plugin.json
            plugin_json_path = plugin_dir / ".claude-plugin" / "plugin.json"
            if plugin_json_path.exists():
                try:
                    with open(plugin_json_path, encoding="utf-8") as f:
                        plugin_data = json.load(f)

                    # Parse components with better aggregation
                    components = []
                    skill_count = 0
                    agent_count = 0
                    hook_count = 0
                    mcp_count = 0
                    lsp_count = 0

                    if "components" in plugin_data:
                        for comp in plugin_data["components"]:
                            comp_type = comp.get("type", "")
                            components.append(
                                PluginComponent(
                                    type=comp_type,
                                    name=comp.get("name", ""),
                                    description=comp.get("description"),
                                )
                            )
                            # Count by type
                            if comp_type == "skill" or comp_type == "command":
                                skill_count += 1
                            elif comp_type == "agent":
                                agent_count += 1
                            elif comp_type == "hook":
                                hook_count += 1
                            elif comp_type == "mcp":
                                mcp_count += 1
                            elif comp_type == "lsp":
                                lsp_count += 1

                    # Scan for additional components in directories
                    skill_count += self._count_directory_items(plugin_dir / "skills")
                    agent_count += self._count_directory_items(plugin_dir / "agents")
                    mcp_count += self._count_directory_items(plugin_dir / "mcp-servers")

                    # Parse hooks from hooks/hooks.json
                    hooks = self._parse_plugin_hooks(plugin_dir)
                    if hooks:
                        hook_count = len(hooks)

                    # Parse LSP configs from .lsp.json
                    lsp_configs = self._parse_lsp_config(plugin_dir)
                    if lsp_configs:
                        lsp_count = len(lsp_configs)

                    # Read README.md if it exists
                    readme_content = self._read_plugin_readme(plugin_dir)

                    plugin = Plugin(
                        name=plugin_data.get("name", plugin_dir.name),
                        version=plugin_data.get("version"),
                        description=plugin_data.get("description"),
                        author=plugin_data.get("author"),
                        category=plugin_data.get("category"),
                        scope=scope,
                        components=components,
                        skill_count=skill_count,
                        agent_count=agent_count,
                        hook_count=hook_count,
                        mcp_count=mcp_count,
                        lsp_count=lsp_count,
                        usage=plugin_data.get("usage"),
                        examples=plugin_data.get("examples"),
                        readme=readme_content,
                        hooks=hooks,
                        lsp_configs=lsp_configs,
                    )
                    plugins.append(plugin)
                except Exception as e:
                    # Skip plugins with invalid plugin.json
                    print(f"Warning: Failed to parse {plugin_json_path}: {e}")
                    continue

        return plugins

    def _count_directory_items(self, directory: Path) -> int:
        """Count items in a directory (for component counting)."""
        if not directory.exists():
            return 0
        return len([d for d in directory.iterdir() if d.is_dir() or d.suffix == ".md"])

    def _parse_plugin_hooks(self, plugin_dir: Path) -> list[PluginHook] | None:
        """
        Parse hooks from a plugin's hooks/hooks.json file.

        Args:
            plugin_dir: Path to plugin directory

        Returns:
            List of PluginHook objects or None
        """
        hooks_json_path = plugin_dir / "hooks" / "hooks.json"
        if not hooks_json_path.exists():
            return None

        try:
            with open(hooks_json_path, encoding="utf-8") as f:
                hooks_data = json.load(f)

            hooks = []
            # hooks.json can be a dict with event names as keys or a list
            if isinstance(hooks_data, dict):
                for event, hook_list in hooks_data.items():
                    if isinstance(hook_list, list):
                        for hook in hook_list:
                            hooks.append(
                                PluginHook(
                                    event=event,
                                    type=hook.get("type", "command"),
                                    matcher=hook.get("matcher"),
                                    command=hook.get("command"),
                                    prompt=hook.get("prompt"),
                                )
                            )
            elif isinstance(hooks_data, list):
                for hook in hooks_data:
                    hooks.append(
                        PluginHook(
                            event=hook.get("event", ""),
                            type=hook.get("type", "command"),
                            matcher=hook.get("matcher"),
                            command=hook.get("command"),
                            prompt=hook.get("prompt"),
                        )
                    )
            return hooks if hooks else None
        except Exception as e:
            print(f"Warning: Failed to parse hooks.json: {e}")
            return None

    def _parse_lsp_config(self, plugin_dir: Path) -> list[PluginLSPConfig] | None:
        """
        Parse LSP configuration from plugin's .lsp.json file.

        Args:
            plugin_dir: Path to plugin directory

        Returns:
            List of PluginLSPConfig objects or None
        """
        lsp_json_path = plugin_dir / ".lsp.json"
        if not lsp_json_path.exists():
            # Also check in .claude-plugin directory
            lsp_json_path = plugin_dir / ".claude-plugin" / ".lsp.json"
            if not lsp_json_path.exists():
                return None

        try:
            with open(lsp_json_path, encoding="utf-8") as f:
                lsp_data = json.load(f)

            configs = []
            # Can be a single config or list of configs
            if isinstance(lsp_data, dict):
                if "servers" in lsp_data:
                    # Multiple servers format
                    for server in lsp_data["servers"]:
                        configs.append(
                            PluginLSPConfig(
                                name=server.get("name", ""),
                                language=server.get("language", ""),
                                command=server.get("command", ""),
                                args=server.get("args"),
                                env=server.get("env"),
                            )
                        )
                else:
                    # Single server format
                    configs.append(
                        PluginLSPConfig(
                            name=lsp_data.get("name", ""),
                            language=lsp_data.get("language", ""),
                            command=lsp_data.get("command", ""),
                            args=lsp_data.get("args"),
                            env=lsp_data.get("env"),
                        )
                    )
            elif isinstance(lsp_data, list):
                for server in lsp_data:
                    configs.append(
                        PluginLSPConfig(
                            name=server.get("name", ""),
                            language=server.get("language", ""),
                            command=server.get("command", ""),
                            args=server.get("args"),
                            env=server.get("env"),
                        )
                    )
            return configs if configs else None
        except Exception as e:
            print(f"Warning: Failed to parse .lsp.json: {e}")
            return None

    def _read_plugin_readme(self, plugin_dir: Path) -> str | None:
        """
        Read README.md from a plugin directory.

        Looks for README.md in the plugin directory or .claude-plugin subdirectory.

        Args:
            plugin_dir: Path to plugin directory

        Returns:
            README content as string, or None if not found
        """
        # Check common README locations
        readme_paths = [
            plugin_dir / "README.md",
            plugin_dir / "readme.md",
            plugin_dir / ".claude-plugin" / "README.md",
            plugin_dir / ".claude-plugin" / "readme.md",
        ]

        for readme_path in readme_paths:
            if readme_path.exists():
                try:
                    with open(readme_path, encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    continue

        return None

    def get_plugin_details(
        self, name: str, project_path: str | None = None
    ) -> Plugin | None:
        """
        Get detailed information about a specific plugin.

        Args:
            name: Plugin name
            project_path: Optional project directory path

        Returns:
            Plugin object or None if not found
        """
        # Check user plugins
        user_plugins_dir = get_claude_user_plugins_dir()
        plugin_path = user_plugins_dir / name / ".claude-plugin" / "plugin.json"

        if not plugin_path.exists() and project_path:
            # Check project plugins
            project_plugins_dir = get_project_plugins_dir(project_path)
            plugin_path = project_plugins_dir / name / ".claude-plugin" / "plugin.json"

        if not plugin_path.exists():
            return None

        try:
            with open(plugin_path, encoding="utf-8") as f:
                plugin_data = json.load(f)

            # Parse components
            components = []
            if "components" in plugin_data:
                for comp in plugin_data["components"]:
                    components.append(
                        PluginComponent(
                            type=comp.get("type", ""),
                            name=comp.get("name", ""),
                        )
                    )

            return Plugin(
                name=plugin_data.get("name", name),
                version=plugin_data.get("version"),
                description=plugin_data.get("description"),
                author=plugin_data.get("author"),
                category=plugin_data.get("category"),
                components=components,
            )
        except Exception as e:
            print(f"Error reading plugin details: {e}")
            return None

    def validate_plugin(self, path: str) -> PluginValidationResult:
        """
        Validate a plugin via CLI: claude plugin validate <path>

        Args:
            path: Path to plugin directory

        Returns:
            PluginValidationResult with validation status
        """
        errors = []
        warnings = []

        # Check if path exists
        plugin_path = Path(path)
        if not plugin_path.exists():
            return PluginValidationResult(
                valid=False,
                errors=[f"Path does not exist: {path}"],
                warnings=[],
            )

        # Check for plugin.json
        plugin_json_path = plugin_path / ".claude-plugin" / "plugin.json"
        if not plugin_json_path.exists():
            errors.append("Missing .claude-plugin/plugin.json")
        else:
            # Validate plugin.json structure
            try:
                with open(plugin_json_path, encoding="utf-8") as f:
                    plugin_data = json.load(f)

                # Check required fields
                if not plugin_data.get("name"):
                    errors.append("Missing 'name' field in plugin.json")

                # Check optional but recommended fields
                if not plugin_data.get("description"):
                    warnings.append("Missing 'description' field in plugin.json")
                if not plugin_data.get("version"):
                    warnings.append("Missing 'version' field in plugin.json")

            except json.JSONDecodeError as e:
                errors.append(f"Invalid JSON in plugin.json: {str(e)}")

        # Check for README
        readme_paths = [
            plugin_path / "README.md",
            plugin_path / "readme.md",
        ]
        if not any(p.exists() for p in readme_paths):
            warnings.append("Missing README.md")

        return PluginValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
