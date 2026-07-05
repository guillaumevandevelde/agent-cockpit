"""Plugin install / uninstall / toggle operations."""
import json
import logging
import shutil
from pathlib import Path

from ..models.schemas import (
    Plugin,
    PluginInstallRequest,
    PluginInstallResponse,
    PluginToggleResponse,
)
from ..utils.file_utils import read_json_file, write_json_file
from ..utils.path_utils import get_claude_user_plugins_dir, get_claude_user_settings_file
from .cli_executor import CLIExecutor
from .plugin_descriptions import get_plugin_info

logger = logging.getLogger(__name__)


class PluginInstaller:
    """Installs, uninstalls, and toggles Claude Code plugins."""

    def __init__(self, cli_executor: CLIExecutor | None = None):
        self.cli_executor = cli_executor or CLIExecutor()

    def _enhance_git_error_message(self, stderr: str, stdout: str) -> str:
        """
        Detect common git/SSH errors and provide helpful suggestions.

        Args:
            stderr: Standard error output from CLI
            stdout: Standard output from CLI

        Returns:
            Enhanced error message with suggestions
        """
        combined_output = f"{stderr}\n{stdout}".lower()

        # Detect SSH authentication failure
        if "permission denied" in combined_output and "publickey" in combined_output:
            return (
                "Failed to clone repository: SSH authentication failed.\n\n"
                "This usually means the plugin repository is private or requires authentication.\n\n"
                "For private repositories:\n"
                "• Set up SSH keys: Add your SSH public key to GitHub\n"
                "• Or use GitHub CLI: Run 'gh auth login' to authenticate\n\n"
                "For public repositories:\n"
                "• This should not happen - please report this issue\n\n"
                f"Original error:\n{stderr}"
            )

        # Detect other common git errors
        if "could not read from remote repository" in combined_output:
            return (
                "Failed to access remote repository. Please verify:\n"
                "• The repository exists and is accessible\n"
                "• You have the correct access permissions\n"
                "• Your network connection is working\n\n"
                f"Original error:\n{stderr}"
            )

        # Return original error if no enhancement needed
        return stderr

    def install_plugin(
        self, request: PluginInstallRequest
    ) -> PluginInstallResponse:
        """
        Install a plugin using the Claude CLI.

        Args:
            request: Plugin installation request

        Returns:
            PluginInstallResponse with installation result
        """
        # For now, we'll use a simple install command
        # In the future, this could use marketplace-specific install commands
        logger.info("Installing plugin", extra={"plugin": request.name})
        try:
            # Configure git to use HTTPS instead of SSH for GitHub
            # This allows cloning public repos without SSH keys
            import os
            env = os.environ.copy()
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "url.https://github.com/.insteadOf"
            env["GIT_CONFIG_VALUE_0"] = "git@github.com:"

            result = self.cli_executor.execute(
                "plugin", ["install", request.name], timeout=120, env=env
            )

            success = result.exit_code == 0

            if success:
                message = f"Successfully installed plugin '{request.name}'"
                enhanced_stderr = result.stderr
                logger.info("Plugin installed", extra={"plugin": request.name})
            else:
                # Enhance error message if it's a known issue
                enhanced_stderr = self._enhance_git_error_message(
                    result.stderr, result.stdout
                )
                message = f"Failed to install plugin '{request.name}'"
                logger.warning("Plugin install failed", extra={"plugin": request.name, "exit_code": result.exit_code})

            return PluginInstallResponse(
                success=success,
                message=message,
                stdout=result.stdout,
                stderr=enhanced_stderr,
            )
        except Exception as e:
            logger.exception("Exception installing plugin", extra={"plugin": request.name})
            return PluginInstallResponse(
                success=False,
                message=f"Error installing plugin: {str(e)}",
                stdout="",
                stderr=str(e),
            )

    async def uninstall_plugin(
        self, name: str, project_path: str | None = None
    ) -> bool:
        """
        Uninstall a plugin by removing its directory and updating config files.

        Args:
            name: Plugin name (can be 'plugin-name' or 'plugin-name@marketplace')
            project_path: Optional project directory path

        Returns:
            True if uninstalled successfully, False otherwise
        """
        logger.info("Uninstalling plugin", extra={"plugin": name})
        removed_any = False
        matching_key = None

        # Try to remove from installed_plugins.json
        installed_plugins_file = get_claude_user_plugins_dir() / "installed_plugins.json"
        if installed_plugins_file.exists():
            try:
                with open(installed_plugins_file) as f:
                    data = json.load(f)

                plugins = data.get("plugins", {})

                # Find matching plugin key
                for key in plugins:
                    if key == name or key.startswith(f"{name}@"):
                        matching_key = key
                        break

                if matching_key:
                    # Remove installation directories
                    plugin_entries = plugins.get(matching_key, [])
                    for entry in plugin_entries:
                        install_path = entry.get("installPath")
                        if install_path:
                            plugin_dir = Path(install_path)
                            if plugin_dir.exists():
                                try:
                                    shutil.rmtree(plugin_dir)
                                    removed_any = True
                                except Exception as e:
                                    logger.error("Error removing plugin directory", extra={"path": str(plugin_dir), "error": str(e)})

                    # Remove from installed_plugins.json
                    del plugins[matching_key]
                    with open(installed_plugins_file, "w") as f:
                        json.dump(data, f, indent=2)
                    removed_any = True
            except Exception:
                logger.exception("Error processing installed_plugins.json", extra={"plugin": name})

        # ALWAYS try to remove from settings.json (enabledPlugins)
        settings_file = get_claude_user_settings_file()
        if settings_file.exists():
            try:
                settings_data = read_json_file(settings_file) or {}
                enabled_plugins = settings_data.get("enabledPlugins", {})

                # Remove matching entries from enabledPlugins
                keys_to_remove = [
                    k for k in enabled_plugins
                    if k in (name, matching_key) or k.startswith(f"{name}@")
                ]
                for key in keys_to_remove:
                    del enabled_plugins[key]

                if keys_to_remove:
                    await write_json_file(settings_file, settings_data)
                    removed_any = True
            except Exception:
                logger.exception("Error updating settings.json", extra={"plugin": name})

        logger.info("Plugin uninstall complete", extra={"plugin": name, "removed": removed_any})
        return removed_any

    async def toggle_plugin(
        self, name: str, enabled: bool, source: str | None = None
    ) -> PluginToggleResponse:
        """
        Toggle a plugin's enabled state in settings.json.

        Args:
            name: Plugin name
            enabled: Whether to enable or disable the plugin
            source: Plugin source (e.g., 'anthropic-agent-skills')

        Returns:
            PluginToggleResponse with success status and updated plugin
        """
        logger.info("Toggling plugin", extra={"plugin": name, "enabled": enabled})
        settings_file = get_claude_user_settings_file()

        # Read current settings
        settings_data = read_json_file(settings_file) or {}

        # Ensure enabledPlugins exists
        if "enabledPlugins" not in settings_data:
            settings_data["enabledPlugins"] = {}

        # Build plugin key
        if source:
            plugin_key = f"{name}@{source}"
        else:
            # Try to find existing key with this name
            existing_key = None
            for key in settings_data["enabledPlugins"]:
                if key == name or key.startswith(f"{name}@"):
                    existing_key = key
                    break
            plugin_key = existing_key or name

        # Update enabled state
        settings_data["enabledPlugins"][plugin_key] = enabled

        # Write back to settings file
        success = await write_json_file(settings_file, settings_data)

        if not success:
            logger.warning("Failed to write settings file for plugin toggle", extra={"plugin": name})
            return PluginToggleResponse(
                success=False,
                message="Failed to write settings file",
                plugin=None,
            )

        # Get updated plugin info
        plugin_info = get_plugin_info(name)
        plugin = Plugin(
            name=name,
            source=source or "unknown",
            enabled=enabled,
            description=plugin_info.get("description") if plugin_info else None,
            usage=plugin_info.get("usage") if plugin_info else None,
            examples=plugin_info.get("examples") if plugin_info else None,
        )

        return PluginToggleResponse(
            success=True,
            message=f"Plugin '{name}' {'enabled' if enabled else 'disabled'} successfully",
            plugin=plugin,
        )
