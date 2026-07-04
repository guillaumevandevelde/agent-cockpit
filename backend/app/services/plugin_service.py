"""
Plugin Service for Claude Cockpit

Facade combining plugin registry (discovery/listing), installer
(install/uninstall/toggle), and marketplace (marketplace API, sync, updates)
responsibilities behind the original PluginService public API.
"""

import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.schemas import (
    Plugin,
    PluginListResponse,
    MarketplacePlugin,
    MarketplacePluginListResponse,
    MarketplaceCreate,
    MarketplaceResponse,
    MarketplaceListResponse,
    PluginInstallRequest,
    PluginInstallResponse,
    PluginToggleResponse,
    PluginUpdatesResponse,
    PluginValidationResult,
    PluginUpdateResponse,
    PluginUpdateAllResponse,
)
from .cli_executor import CLIExecutor
from .plugin_registry_service import PluginRegistry
from .plugin_installer_service import PluginInstaller
from .marketplace_service import MarketplaceService


logger = logging.getLogger(__name__)


class PluginService:
    """Service for managing Claude Code plugins."""

    def __init__(self, db: Optional[AsyncSession] = None):
        """Initialize plugin service."""
        self.db = db
        self.cli_executor = CLIExecutor()
        self.registry = PluginRegistry()
        self.installer = PluginInstaller(self.cli_executor)
        self.marketplace = MarketplaceService(db, self.cli_executor, self.registry)

    @property
    def _marketplace_cache(self) -> Dict[str, List[MarketplacePlugin]]:
        return self.marketplace._marketplace_cache

    # -- Registry (discovery/listing) ---------------------------------------

    def list_installed_plugins(
        self, project_path: Optional[str] = None
    ) -> PluginListResponse:
        return self.registry.list_installed_plugins(project_path)

    def get_plugin_details(
        self, name: str, project_path: Optional[str] = None
    ) -> Optional[Plugin]:
        return self.registry.get_plugin_details(name, project_path)

    def validate_plugin(self, path: str) -> PluginValidationResult:
        return self.registry.validate_plugin(path)

    # -- Installer (install/uninstall/toggle) --------------------------------

    def install_plugin(
        self, request: PluginInstallRequest
    ) -> PluginInstallResponse:
        return self.installer.install_plugin(request)

    async def uninstall_plugin(
        self, name: str, project_path: Optional[str] = None
    ) -> bool:
        return await self.installer.uninstall_plugin(name, project_path)

    async def toggle_plugin(
        self, name: str, enabled: bool, source: Optional[str] = None
    ) -> PluginToggleResponse:
        return await self.installer.toggle_plugin(name, enabled, source)

    # -- Marketplace (marketplace API, sync, updates) ------------------------

    async def list_marketplaces(self) -> MarketplaceListResponse:
        return await self.marketplace.list_marketplaces()

    def _resolve_marketplace_input(self, input_str: str) -> tuple:
        return self.marketplace._resolve_marketplace_input(input_str)

    async def add_marketplace(
        self, marketplace: MarketplaceCreate
    ) -> MarketplaceResponse:
        return await self.marketplace.add_marketplace(marketplace)

    async def remove_marketplace(self, name: str) -> bool:
        return await self.marketplace.remove_marketplace(name)

    async def sync_marketplace(self, name: str) -> bool:
        return await self.marketplace.sync_marketplace(name)

    def browse_marketplace(self, name: str) -> MarketplacePluginListResponse:
        return self.marketplace.browse_marketplace(name)

    def add_marketplace_via_cli(self, marketplace_input: str) -> dict:
        return self.marketplace.add_marketplace_via_cli(marketplace_input)

    def remove_marketplace_via_cli(self, name: str) -> dict:
        return self.marketplace.remove_marketplace_via_cli(name)

    def update_marketplace_via_cli(self, name: str) -> dict:
        return self.marketplace.update_marketplace_via_cli(name)

    def list_marketplaces_from_files(self) -> List[dict]:
        return self.marketplace.list_marketplaces_from_files()

    async def set_marketplace_auto_update(self, name: str, enabled: bool) -> bool:
        return await self.marketplace.set_marketplace_auto_update(name, enabled)

    def browse_marketplace_from_files(self, name: str) -> List[dict]:
        return self.marketplace.browse_marketplace_from_files(name)

    def get_marketplace_plugin_details(self, marketplace_name: str, plugin_name: str) -> Optional[dict]:
        return self.marketplace.get_marketplace_plugin_details(marketplace_name, plugin_name)

    def check_for_updates(self) -> PluginUpdatesResponse:
        return self.marketplace.check_for_updates()

    def update_plugin(self, name: str) -> PluginUpdateResponse:
        return self.marketplace.update_plugin(name)

    def update_all_plugins(self) -> PluginUpdateAllResponse:
        return self.marketplace.update_all_plugins()

    def get_all_available_plugins(self) -> List[MarketplacePlugin]:
        return self.marketplace.get_all_available_plugins()
