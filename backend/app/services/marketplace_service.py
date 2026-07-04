"""Marketplace API, sync, and plugin-update operations."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx

from ..models.database import Marketplace
from ..models.schemas import (
    MarketplacePlugin,
    MarketplacePluginListResponse,
    MarketplaceCreate,
    MarketplaceResponse,
    MarketplaceListResponse,
    PluginUpdateInfo,
    PluginUpdatesResponse,
    PluginUpdateResponse,
    PluginUpdateAllResponse,
)
from ..utils.path_utils import (
    get_claude_user_plugins_dir,
    get_known_marketplaces_file,
    get_marketplaces_dir,
    ensure_directory_exists,
)
from ..utils.file_utils import read_json_file, write_json_file
from .cli_executor import CLIExecutor
from .plugin_registry_service import PluginRegistry


logger = logging.getLogger(__name__)


class MarketplaceService:
    """Manages plugin marketplaces: registration, sync, browsing, and updates."""

    def __init__(
        self,
        db: Optional[AsyncSession] = None,
        cli_executor: Optional[CLIExecutor] = None,
        registry: Optional[PluginRegistry] = None,
    ):
        self.db = db
        self.cli_executor = cli_executor or CLIExecutor()
        self.registry = registry or PluginRegistry()
        self._marketplace_cache: Dict[str, List[MarketplacePlugin]] = {}

    async def list_marketplaces(self) -> MarketplaceListResponse:
        """
        List all configured marketplaces from database.

        Returns:
            MarketplaceListResponse with list of marketplaces
        """
        if not self.db:
            return MarketplaceListResponse(marketplaces=[])

        result = await self.db.execute(select(Marketplace))
        marketplaces = result.scalars().all()

        marketplace_responses = [
            MarketplaceResponse(
                id=m.id,
                name=m.name,
                url=m.url,
                last_synced=m.last_synced.isoformat() if m.last_synced else None,
                created_at=m.created_at.isoformat(),
            )
            for m in marketplaces
        ]

        return MarketplaceListResponse(marketplaces=marketplace_responses)

    def _resolve_marketplace_input(self, input_str: str) -> tuple:
        """
        Resolve marketplace input to (name, url).

        Supports two formats:
        1. Full URL: https://example.com/plugins.json
        2. GitHub shorthand: owner/repo

        Args:
            input_str: Either "owner/repo" or full URL

        Returns:
            Tuple of (name, url)
        """
        input_str = input_str.strip()

        # Check if it's a URL
        if input_str.startswith(("http://", "https://")):
            # Extract name from URL path
            name = input_str.rstrip("/").split("/")[-1].replace(".json", "")
            return (name, input_str)

        # Assume owner/repo format
        if "/" in input_str:
            parts = input_str.split("/", 1)
            owner = parts[0]
            repo = parts[1]
            name = repo
            # Use raw GitHub URL for plugins.json in main branch
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/plugins.json"
            return (name, url)

        raise ValueError(
            f"Invalid marketplace input: '{input_str}'. "
            "Expected 'owner/repo' or full URL."
        )

    async def add_marketplace(
        self, marketplace: MarketplaceCreate
    ) -> MarketplaceResponse:
        """
        Add a new marketplace to the database.

        Supports smart input resolution for owner/repo format.

        Args:
            marketplace: Marketplace configuration

        Returns:
            MarketplaceResponse with created marketplace
        """
        if not self.db:
            raise ValueError("Database session required")

        # Smart resolution from input field
        if marketplace.input:
            resolved_name, resolved_url = self._resolve_marketplace_input(
                marketplace.input
            )
            # Use resolved values if not explicitly provided
            if not marketplace.name:
                marketplace.name = resolved_name
            if not marketplace.url:
                marketplace.url = resolved_url

        # Validate we have required fields
        if not marketplace.name or not marketplace.url:
            raise ValueError(
                "Marketplace name and URL are required. "
                "Provide them directly or use the 'input' field with 'owner/repo' format."
            )

        # Check if marketplace already exists
        result = await self.db.execute(
            select(Marketplace).where(Marketplace.name == marketplace.name)
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise ValueError(f"Marketplace '{marketplace.name}' already exists")

        # Create new marketplace
        new_marketplace = Marketplace(
            name=marketplace.name,
            url=marketplace.url,
            last_synced=None,
            created_at=datetime.now(timezone.utc),
        )

        self.db.add(new_marketplace)
        await self.db.commit()
        await self.db.refresh(new_marketplace)

        return MarketplaceResponse(
            id=new_marketplace.id,
            name=new_marketplace.name,
            url=new_marketplace.url,
            last_synced=None,
            created_at=new_marketplace.created_at.isoformat(),
        )

    async def remove_marketplace(self, name: str) -> bool:
        """
        Remove a marketplace from the database.

        Args:
            name: Marketplace name

        Returns:
            True if removed successfully, False otherwise
        """
        if not self.db:
            return False

        result = await self.db.execute(
            select(Marketplace).where(Marketplace.name == name)
        )
        marketplace = result.scalar_one_or_none()

        if not marketplace:
            return False

        await self.db.delete(marketplace)
        await self.db.commit()

        # Clear cache for this marketplace
        if name in self._marketplace_cache:
            del self._marketplace_cache[name]

        return True

    async def sync_marketplace(self, name: str) -> bool:
        """
        Sync marketplace catalog from remote URL.

        Fetches the marketplace catalog and caches it locally.

        Args:
            name: Marketplace name

        Returns:
            True if synced successfully, False otherwise
        """
        if not self.db:
            return False

        # Get marketplace from database
        result = await self.db.execute(
            select(Marketplace).where(Marketplace.name == name)
        )
        marketplace = result.scalar_one_or_none()

        if not marketplace:
            return False

        try:
            # Fetch marketplace catalog
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(marketplace.url)
                response.raise_for_status()
                catalog_data = response.json()

            # Parse catalog
            plugins = []
            if isinstance(catalog_data, dict) and "plugins" in catalog_data:
                for plugin_data in catalog_data["plugins"]:
                    plugins.append(
                        MarketplacePlugin(
                            name=plugin_data.get("name", ""),
                            description=plugin_data.get("description"),
                            version=plugin_data.get("version"),
                            install_command=plugin_data.get(
                                "install_command", f"plugin install {plugin_data.get('name', '')}"
                            ),
                        )
                    )

            # Cache the catalog
            self._marketplace_cache[name] = plugins

            # Update last_synced timestamp
            marketplace.last_synced = datetime.now(timezone.utc)
            await self.db.commit()

            return True
        except Exception as e:
            print(f"Error syncing marketplace: {e}")
            return False

    def browse_marketplace(self, name: str) -> MarketplacePluginListResponse:
        """
        Browse cached marketplace catalog.

        Args:
            name: Marketplace name

        Returns:
            MarketplacePluginListResponse with list of available plugins
        """
        plugins = self._marketplace_cache.get(name, [])
        return MarketplacePluginListResponse(plugins=plugins)

    # =========================================================================
    # CLI Passthrough Methods - Use Claude CLI for marketplace management
    # =========================================================================

    def add_marketplace_via_cli(self, marketplace_input: str) -> dict:
        """
        Add a marketplace using Claude CLI.

        This delegates to `claude plugin marketplace add` which handles:
        - Cloning the repository
        - Discovering plugins
        - Updating known_marketplaces.json

        Args:
            marketplace_input: GitHub repo (owner/repo) or full URL

        Returns:
            Dict with success status and message
        """
        result = self.cli_executor.execute(
            "plugin", ["marketplace", "add", marketplace_input], timeout=120
        )

        success = result.exit_code == 0
        return {
            "success": success,
            "message": result.stdout if success else result.stderr,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def remove_marketplace_via_cli(self, name: str) -> dict:
        """
        Remove a marketplace using Claude CLI.

        Args:
            name: Marketplace name

        Returns:
            Dict with success status and message
        """
        result = self.cli_executor.execute(
            "plugin", ["marketplace", "remove", name], timeout=60
        )
        return {
            "success": result.exit_code == 0,
            "message": result.stdout if result.exit_code == 0 else result.stderr,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def update_marketplace_via_cli(self, name: str) -> dict:
        """
        Update a marketplace using Claude CLI.

        Args:
            name: Marketplace name

        Returns:
            Dict with success status and message
        """
        result = self.cli_executor.execute(
            "plugin", ["marketplace", "update", name], timeout=120
        )
        return {
            "success": result.exit_code == 0,
            "message": result.stdout if result.exit_code == 0 else result.stderr,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    # =========================================================================
    # File-based Methods - Read marketplace data from Claude's config files
    # =========================================================================

    def list_marketplaces_from_files(self) -> List[dict]:
        """
        List marketplaces from Claude's known_marketplaces.json.

        Returns:
            List of marketplace info dicts
        """
        known_file = get_known_marketplaces_file()
        known_data = read_json_file(known_file) or {}

        # Load auto-update settings
        auto_update_settings = self._load_marketplace_auto_update_settings()

        marketplaces = []
        for name, info in known_data.items():
            # Get plugin count from marketplace.json
            marketplace_json = (
                get_marketplaces_dir() / name / ".claude-plugin" / "marketplace.json"
            )
            marketplace_data = read_json_file(marketplace_json) or {}
            plugin_count = len(marketplace_data.get("plugins", []))

            marketplaces.append({
                "name": name,
                "repo": info.get("source", {}).get("repo", ""),
                "install_location": info.get("installLocation", ""),
                "last_updated": info.get("lastUpdated"),
                "plugin_count": plugin_count,
                "auto_update": auto_update_settings.get(name, False),
            })

        return marketplaces

    def _load_marketplace_auto_update_settings(self) -> Dict[str, bool]:
        """Load per-marketplace auto-update settings."""
        settings_file = get_claude_user_plugins_dir() / "marketplace_settings.json"
        if not settings_file.exists():
            return {}
        data = read_json_file(settings_file) or {}
        return data.get("auto_update", {})

    async def set_marketplace_auto_update(self, name: str, enabled: bool) -> bool:
        """
        Set auto-update preference for a marketplace.

        Args:
            name: Marketplace name
            enabled: Whether auto-update is enabled

        Returns:
            True if saved successfully
        """
        settings_file = get_claude_user_plugins_dir() / "marketplace_settings.json"
        ensure_directory_exists(settings_file.parent)

        data = read_json_file(settings_file) or {}
        if "auto_update" not in data:
            data["auto_update"] = {}
        data["auto_update"][name] = enabled

        return await write_json_file(settings_file, data)

    def browse_marketplace_from_files(self, name: str) -> List[dict]:
        """
        Browse plugins from a marketplace's local clone.

        Args:
            name: Marketplace name

        Returns:
            List of plugin info dicts
        """
        marketplace_json = (
            get_marketplaces_dir() / name / ".claude-plugin" / "marketplace.json"
        )
        marketplace_data = read_json_file(marketplace_json) or {}
        return marketplace_data.get("plugins", [])

    def get_marketplace_plugin_details(self, marketplace_name: str, plugin_name: str) -> Optional[dict]:
        """
        Get detailed information about a plugin from a marketplace.

        Reads the plugin's README, metadata, and constructs GitHub links.

        Args:
            marketplace_name: Name of the marketplace
            plugin_name: Name of the plugin

        Returns:
            Dict with plugin details or None if not found
        """
        marketplace_dir = get_marketplaces_dir() / marketplace_name
        marketplace_json = marketplace_dir / ".claude-plugin" / "marketplace.json"

        marketplace_data = read_json_file(marketplace_json) or {}
        plugins = marketplace_data.get("plugins", [])

        # Find the plugin in marketplace
        plugin_info = None
        for p in plugins:
            if p.get("name") == plugin_name:
                plugin_info = p
                break

        if not plugin_info:
            return None

        # Get the plugin source path
        source_path = plugin_info.get("source", "")
        if source_path.startswith("./"):
            source_path = source_path[2:]

        plugin_dir = marketplace_dir / source_path

        # Read README if available
        readme_content = None
        for readme_name in ["README.md", "readme.md", "README.MD"]:
            readme_path = plugin_dir / readme_name
            if readme_path.exists():
                try:
                    with open(readme_path, "r", encoding="utf-8") as f:
                        readme_content = f.read()
                    break
                except Exception:
                    pass

        # Try to read plugin.json for more details
        plugin_json_path = plugin_dir / ".claude-plugin" / "plugin.json"
        plugin_json_data = read_json_file(plugin_json_path) or {}

        # Get known marketplace info for GitHub link
        known_file = get_known_marketplaces_file()
        known_data = read_json_file(known_file) or {}
        marketplace_info = known_data.get(marketplace_name, {})
        repo = marketplace_info.get("source", {}).get("repo", "")

        # Construct GitHub URL
        github_url = None
        if repo:
            github_url = f"https://github.com/{repo}"
            if source_path:
                github_url = f"https://github.com/{repo}/tree/main/{source_path}"

        # Use homepage from plugin_info if available
        homepage = plugin_info.get("homepage") or github_url

        # Scan for components
        components = []
        commands_dir = plugin_dir / "commands"
        if commands_dir.exists():
            for f in commands_dir.iterdir():
                if f.suffix == ".md":
                    components.append({"type": "command", "name": f.stem})

        agents_dir = plugin_dir / "agents"
        if agents_dir.exists():
            for f in agents_dir.iterdir():
                if f.suffix == ".md":
                    components.append({"type": "agent", "name": f.stem})

        skills_dir = plugin_dir / "skills"
        if skills_dir.exists():
            for f in skills_dir.iterdir():
                if f.is_dir() or f.suffix == ".md":
                    components.append({"type": "skill", "name": f.stem if f.is_file() else f.name})

        return {
            "name": plugin_info.get("name"),
            "description": plugin_info.get("description"),
            "version": plugin_info.get("version") or plugin_json_data.get("version"),
            "author": plugin_info.get("author") or plugin_json_data.get("author"),
            "category": plugin_info.get("category"),
            "homepage": homepage,
            "github_url": github_url,
            "readme": readme_content,
            "components": components,
            "has_mcp": bool(plugin_info.get("mcpServers") or plugin_json_data.get("mcpServers")),
            "has_lsp": bool(plugin_info.get("lspServers") or plugin_json_data.get("lspServers")),
        }

    # =========================================================================
    # Plugin Update Methods
    # =========================================================================

    def check_for_updates(self) -> PluginUpdatesResponse:
        """
        Compare installed plugins with marketplace versions.

        Uses `claude plugin list --available --json` to get latest versions
        and compares with installed plugins.

        Returns:
            PluginUpdatesResponse with list of plugins that have updates
        """
        update_info_list = []

        # Get installed plugins
        installed_response = self.registry.list_installed_plugins()
        installed_plugins = {p.name: p for p in installed_response.plugins}

        # Get all available plugins from marketplaces
        available_plugins = self.get_all_available_plugins()
        available_by_name = {p.name: p for p in available_plugins}

        # Compare versions
        for name, installed in installed_plugins.items():
            available = available_by_name.get(name)
            if available and available.version and installed.version:
                has_update = self._version_compare(installed.version, available.version) < 0
                if has_update:
                    update_info_list.append(
                        PluginUpdateInfo(
                            name=name,
                            installed_version=installed.version,
                            latest_version=available.version,
                            has_update=True,
                            source=installed.source,
                        )
                    )

        return PluginUpdatesResponse(
            plugins=update_info_list,
            outdated_count=len(update_info_list),
        )

    def _version_compare(self, v1: str, v2: str) -> int:
        """
        Compare two version strings.

        Returns:
            -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
        """
        def normalize(v):
            # Remove 'v' prefix if present
            v = v.lstrip('v')
            # Split by dots and convert to integers where possible
            parts = []
            for part in v.split('.'):
                try:
                    parts.append(int(part))
                except ValueError:
                    parts.append(part)
            return parts

        parts1 = normalize(v1)
        parts2 = normalize(v2)

        # Compare part by part
        for i in range(max(len(parts1), len(parts2))):
            p1 = parts1[i] if i < len(parts1) else 0
            p2 = parts2[i] if i < len(parts2) else 0

            if isinstance(p1, int) and isinstance(p2, int):
                if p1 < p2:
                    return -1
                elif p1 > p2:
                    return 1
            else:
                # String comparison for non-numeric parts
                if str(p1) < str(p2):
                    return -1
                elif str(p1) > str(p2):
                    return 1

        return 0

    def update_plugin(self, name: str) -> PluginUpdateResponse:
        """
        Update a plugin via CLI: claude plugin update <name>

        Args:
            name: Plugin name to update

        Returns:
            PluginUpdateResponse with update result
        """
        try:
            result = self.cli_executor.execute(
                "plugin", ["update", name], timeout=120
            )

            success = result.exit_code == 0
            return PluginUpdateResponse(
                success=success,
                message=f"Plugin '{name}' {'updated successfully' if success else 'update failed'}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except Exception as e:
            return PluginUpdateResponse(
                success=False,
                message=f"Error updating plugin: {str(e)}",
                stdout="",
                stderr=str(e),
            )

    def update_all_plugins(self) -> PluginUpdateAllResponse:
        """
        Update all outdated plugins.

        Returns:
            PluginUpdateAllResponse with results
        """
        updates = self.check_for_updates()
        results = []
        updated_count = 0
        failed_count = 0

        for plugin_info in updates.plugins:
            result = self.update_plugin(plugin_info.name)
            results.append(result)
            if result.success:
                updated_count += 1
            else:
                failed_count += 1

        return PluginUpdateAllResponse(
            success=failed_count == 0,
            message=f"Updated {updated_count} plugins, {failed_count} failed",
            updated_count=updated_count,
            failed_count=failed_count,
            results=results,
        )

    def get_all_available_plugins(self) -> List[MarketplacePlugin]:
        """
        Get all plugins from all marketplaces.

        Returns:
            List of MarketplacePlugin from all configured marketplaces
        """
        all_plugins = []
        seen_names = set()

        # Get all marketplaces
        marketplaces = self.list_marketplaces_from_files()

        for marketplace in marketplaces:
            plugins = self.browse_marketplace_from_files(marketplace["name"])
            for plugin_data in plugins:
                name = plugin_data.get("name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_plugins.append(
                        MarketplacePlugin(
                            name=name,
                            description=plugin_data.get("description"),
                            version=plugin_data.get("version"),
                            install_command=plugin_data.get(
                                "install_command",
                                f"claude plugin install {name}"
                            ),
                        )
                    )

        return all_plugins
