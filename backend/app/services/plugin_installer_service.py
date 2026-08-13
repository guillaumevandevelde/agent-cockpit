"""Plugin install / uninstall / toggle operations."""
import hashlib
import io
import json
import logging
import re
import shutil
import zipfile
from pathlib import Path

import httpx

from ..models.schemas import (
    Plugin,
    PluginInstallRequest,
    PluginInstallResponse,
    PluginToggleResponse,
)
from ..utils.file_utils import read_json_file, write_json_file
from ..utils.path_utils import (
    get_claude_user_plugins_dir,
    get_claude_user_settings_file,
)
from .cli_executor import CLIExecutor
from .plugin_descriptions import get_plugin_info

logger = logging.getLogger(__name__)

# Parses `name@archive://https://host/path.zip[?sha256=<64-hex>]`.
# URL must be https (archive:// over plaintext breaks the supply-chain
# guarantee pinning is meant to provide). sha256, when present, must be
# exactly 64 lowercase/uppercase hex chars.
_ARCHIVE_SOURCE_RE = re.compile(
    r"^(?P<name>[^@]+)@archive://(?P<url>https://[^\s?]+)(?:\?sha256=(?P<sha256>[0-9a-fA-F]{64}))?$"
)


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

        For `name@archive://https://...zip[?sha256=<hex>]` references
        (CC 2.1.224 archive source), downloads the zip, verifies sha256
        when pinned, extracts into the user plugin dir, and registers
        the verbatim archive URL as the source. All other inputs fall
        through to the existing CLI install path unchanged.

        Args:
            request: Plugin installation request

        Returns:
            PluginInstallResponse with installation result
        """
        logger.info("Installing plugin", extra={"plugin": request.name})

        # Archive-source path (CC 2.1.224). Run before the CLI branch
        # so the verifier owns the supply-chain check; the CLI branch
        # is the legacy marketplace route.
        archive = self._parse_archive_source(request.name)
        if archive is not None:
            return self._install_from_archive(
                plugin_name=archive["plugin_name"],
                url=archive["url"],
                sha256=archive["sha256"],
            )

        # For now, we'll use a simple install command
        # In the future, this could use marketplace-specific install commands
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

    def _parse_archive_source(self, name: str) -> dict | None:
        """Parse ``name@archive://https://...zip[?sha256=<hex>]`` into parts.

        Returns ``None`` when ``name`` doesn't carry the archive scheme
        so callers fall through to the existing CLI install path.
        """
        match = _ARCHIVE_SOURCE_RE.match(name or "")
        if not match:
            return None
        return {
            "plugin_name": match.group("name"),
            "url": match.group("url"),
            "sha256": match.group("sha256"),  # may be None when no ?sha256
        }

    def _verify_sha256(self, data: bytes, expected_hex: str) -> bool:
        """Constant-time-ish compare (hexdigest comparison is short-circuit
        on first byte diff, but the input is short and we are not protecting
        a secret — the goal is rejection of wrong-content, not timing
        side-channels)."""
        if not isinstance(expected_hex, str) or len(expected_hex) != 64:
            return False
        try:
            int(expected_hex, 16)
        except ValueError:
            return False
        return hashlib.sha256(data).hexdigest() == expected_hex.lower()

    def _install_from_archive(
        self,
        plugin_name: str,
        url: str,
        sha256: str | None,
    ) -> PluginInstallResponse:
        """Download zip, verify, extract, register.

        Idempotent on overwrite: an existing plugin dir is replaced.
        """
        try:
            response = httpx.get(url, follow_redirects=True, timeout=120.0)
            response.raise_for_status()
        except Exception as e:
            logger.warning(
                "Archive download failed",
                extra={"plugin": plugin_name, "url": url, "error": str(e)},
            )
            return PluginInstallResponse(
                success=False,
                message=f"Failed to download archive for '{plugin_name}': {e}",
                stderr=str(e),
            )

        payload = response.content

        if sha256 is not None and not self._verify_sha256(payload, sha256):
            digest = hashlib.sha256(payload).hexdigest()
            msg = (
                f"sha256 mismatch for '{plugin_name}': "
                f"expected {sha256.lower()}, got {digest}"
            )
            logger.warning(msg, extra={"plugin": plugin_name, "url": url})
            return PluginInstallResponse(
                success=False,
                message=msg,
                stderr=msg,
            )

        plugins_dir = get_claude_user_plugins_dir()
        plugin_dir = plugins_dir / plugin_name
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        plugin_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                names = [m.filename for m in zf.infolist() if m.filename]
                # Strip a common top-level dir (e.g. `archive-foo/...`)
                # so the manifest lands at plugin_dir/.claude-plugin/plugin.json
                # rather than plugin_dir/archive-foo/.claude-plugin/plugin.json.
                # Only strip when every entry is prefixed and stripping leaves
                # no stray empty top-level name — anything else keeps the
                # zip's own layout intact.
                strip_prefix = ""
                if names and all(n.startswith(plugin_name + "/") for n in names):
                    strip_prefix = plugin_name + "/"

                plugin_root = plugin_dir.resolve()
                for member in zf.infolist():
                    rel = member.filename[len(strip_prefix):] if strip_prefix else member.filename
                    if not rel:
                        continue  # the bare top-level dir entry itself
                    target = (plugin_dir / rel).resolve()
                    if not str(target).startswith(str(plugin_root) + "/") and target != plugin_root:
                        raise ValueError(f"archive entry escapes plugin dir: {member.filename}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if member.is_dir():
                        continue
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        except Exception as e:
            logger.exception(
                "Archive extract failed",
                extra={"plugin": plugin_name, "url": url, "error": str(e)},
            )
            return PluginInstallResponse(
                success=False,
                message=f"Failed to extract archive for '{plugin_name}': {e}",
                stderr=str(e),
            )

        # Source string preserves the original `archive://` URL verbatim —
        # including the `?sha256=` query when present — so the registry
        # can re-parse it back into the same scheme (name@archive://...).
        source = f"archive://{url}"
        if sha256 is not None:
            source = f"{source}?sha256={sha256}"

        plugin_key = f"{plugin_name}@{source}"

        installed_file = plugins_dir / "installed_plugins.json"
        try:
            data: dict = {}
            if installed_file.exists():
                with open(installed_file) as f:
                    data = json.load(f) or {}
            plugins_map = data.setdefault("plugins", {})
            plugins_map[plugin_key] = [
                {
                    "installPath": str(plugin_dir),
                    "version": "archive",
                    "isLocal": True,
                }
            ]
            installed_file.parent.mkdir(parents=True, exist_ok=True)
            with open(installed_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.exception(
                "Failed to update installed_plugins.json",
                extra={"plugin": plugin_name, "error": str(e)},
            )
            return PluginInstallResponse(
                success=False,
                message=f"Failed to register '{plugin_name}': {e}",
                stderr=str(e),
            )

        # Enable in settings.json so `claude` picks it up next session.
        settings_file = get_claude_user_settings_file()
        try:
            settings_data = read_json_file(settings_file) or {}
            enabled = settings_data.setdefault("enabledPlugins", {})
            enabled[plugin_key] = True
            with open(settings_file, "w") as f:
                json.dump(settings_data, f, indent=2)
        except Exception as e:
            logger.exception(
                "Failed to update settings.json",
                extra={"plugin": plugin_name, "error": str(e)},
            )
            return PluginInstallResponse(
                success=False,
                message=f"Failed to enable '{plugin_name}' in settings: {e}",
                stderr=str(e),
            )

        logger.info(
            "Archive plugin installed",
            extra={"plugin": plugin_name, "url": url, "sha256": sha256},
        )
        return PluginInstallResponse(
            success=True,
            message=f"Installed '{plugin_name}' from {source}",
            stdout=source,
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
