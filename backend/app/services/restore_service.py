"""Service for restoring configuration from backups."""
import logging
import shlex
import subprocess
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING

from app.models.schemas import (
    BackupPluginInfo,
    DependencyInstallRequest,
    DependencyInstallResult,
    DependencyInstallStatus,
    RestoreOptions,
    RestorePlan,
    RestorePlanDependency,
    RestorePlanWarning,
    RestoreResult,
)
from app.utils.path_utils import get_user_home, get_claude_user_skills_dir
from app.services.backup_shared import (
    CODEX_RESTORE_REFUSAL_MESSAGE,
    _get_current_platform,
)

if TYPE_CHECKING:
    from app.services.backup_service import BackupService


logger = logging.getLogger(__name__)


class RestoreService:
    """Service for analyzing, validating, and restoring configuration backups."""

    def __init__(self, backup_service: "BackupService"):
        self.backup_service = backup_service

    async def get_restore_plan(
        self, backup_id: int, project_path: Optional[str] = None
    ) -> Optional[RestorePlan]:
        """
        Analyze a backup and generate a restore plan.

        Args:
            backup_id: Backup ID
            project_path: Target project path

        Returns:
            RestorePlan or None if backup not found
        """
        backup = await self.backup_service.get_backup(backup_id)
        if not backup:
            return None

        archive_path = Path(backup.file_path)
        if not archive_path.exists():
            return None

        current_platform = _get_current_platform()
        manifest = self.backup_service.get_manifest_from_backup(backup.file_path)

        plan = RestorePlan(
            backup_id=backup.id,
            backup_name=backup.name,
            created_at=backup.created_at.isoformat(),
            scope=backup.scope,
            platform_current=current_platform,
            platform_backup=manifest.platform if manifest else "unknown",
            platform_compatible=True,
        )
        if backup.scope == "codex":
            policy = self.backup_service._get_codex_backup_policy()
            plan.warnings.append(
                RestorePlanWarning(
                    type="unsupported_restore",
                    message=CODEX_RESTORE_REFUSAL_MESSAGE,
                    severity="error",
                )
            )
            for reason in policy.get("restore_refusal_reasons", []):
                plan.manual_steps.append(reason)
            plan.manual_steps.append(
                "Download and review the redacted Codex export before manually copying files into CODEX_HOME."
            )

        # Check platform compatibility
        if manifest and manifest.platform != current_platform:
            plan.platform_compatible = False
            plan.warnings.append(
                RestorePlanWarning(
                    type="platform",
                    message=f"Backup was created on {manifest.platform}, current platform is {current_platform}. Some paths or scripts may not work correctly.",
                    severity="warning",
                )
            )

        # Get files list
        with zipfile.ZipFile(archive_path, "r") as zf:
            plan.files_to_restore = [
                f for f in zf.namelist() if f != "manifest.json"
            ]

        if manifest:
            plan.skills_to_restore = manifest.contents.skills
            plan.plugins_to_restore = manifest.contents.plugins
            plan.mcp_servers_to_restore = manifest.contents.mcp_servers

            # Collect dependencies
            for skill in manifest.contents.skills:
                for dep in skill.dependencies:
                    plan.dependencies.append(
                        RestorePlanDependency(
                            kind=dep.kind,
                            name=dep.name,
                            version=dep.version,
                            source=f"skill:{skill.name}",
                        )
                    )
                if skill.has_install_script:
                    plan.manual_steps.append(
                        f"Run install.sh for skill '{skill.name}'"
                    )

            for plugin in manifest.contents.plugins:
                if plugin.install_command:
                    plan.dependencies.append(
                        RestorePlanDependency(
                            kind="plugin",
                            name=plugin.name,
                            source=plugin.marketplace,
                            install_command=plugin.install_command,
                        )
                    )
                elif plugin.source:
                    plan.manual_steps.append(
                        f"Reinstall plugin '{plugin.name}' from {plugin.source or 'marketplace'}"
                    )

            for mcp in manifest.contents.mcp_servers:
                if mcp.requires_npm_install:
                    # Extract package name from npx command
                    if mcp.args:
                        pkg_name = mcp.args[0] if mcp.args else mcp.name
                    else:
                        pkg_name = mcp.name
                    plan.dependencies.append(
                        RestorePlanDependency(
                            kind="mcp_npm",
                            name=pkg_name,
                            source=f"mcp:{mcp.name}",
                        )
                    )

            plan.has_dependencies = len(plan.dependencies) > 0

        return plan

    async def validate_backup(self, backup_id: int) -> Tuple[bool, List[str]]:
        """
        Validate a backup before restore.

        Args:
            backup_id: Backup ID

        Returns:
            Tuple of (is_valid, list of issues)
        """
        backup = await self.backup_service.get_backup(backup_id)
        issues = []

        if not backup:
            return False, ["Backup not found"]

        archive_path = Path(backup.file_path)
        if not archive_path.exists():
            return False, ["Backup file not found on disk"]

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                # Test archive integrity
                bad_file = zf.testzip()
                if bad_file:
                    issues.append(f"Corrupted file in archive: {bad_file}")

                # Check for manifest
                if "manifest.json" not in zf.namelist():
                    issues.append("Backup is missing manifest.json (older format)")
        except zipfile.BadZipFile:
            return False, ["Backup file is corrupted"]

        return len(issues) == 0, issues

    def _install_skill_dependencies(self, skill_path: Path) -> Tuple[bool, str]:
        """
        Install dependencies for a skill.

        Args:
            skill_path: Path to skill directory

        Returns:
            Tuple of (success, log output)
        """
        logs = []
        success = True

        # npm install
        if (skill_path / "package.json").exists():
            try:
                result = subprocess.run(
                    ["npm", "install"],
                    cwd=str(skill_path),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                logs.append(f"npm install in {skill_path.name}:")
                logs.append(result.stdout)
                if result.returncode != 0:
                    logs.append(f"Error: {result.stderr}")
                    success = False
            except Exception as e:
                logs.append(f"npm install failed: {e}")
                success = False

        # pip install
        if (skill_path / "requirements.txt").exists():
            try:
                result = subprocess.run(
                    ["pip", "install", "-r", "requirements.txt"],
                    cwd=str(skill_path),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                logs.append(f"pip install in {skill_path.name}:")
                logs.append(result.stdout)
                if result.returncode != 0:
                    logs.append(f"Error: {result.stderr}")
                    success = False
            except Exception as e:
                logs.append(f"pip install failed: {e}")
                success = False

        return success, "\n".join(logs)

    def _reinstall_plugin(self, plugin_info: BackupPluginInfo) -> Tuple[bool, str]:
        """
        Reinstall a plugin using its install command.

        Args:
            plugin_info: Plugin information

        Returns:
            Tuple of (success, log output)
        """
        if not plugin_info.install_command:
            return False, f"No install command for plugin {plugin_info.name}"

        try:
            command = shlex.split(plugin_info.install_command)
            if not command:
                return False, f"Invalid install command for plugin {plugin_info.name}"
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=300,
            )
            logs = f"Installing {plugin_info.name}:\n{result.stdout}"
            if result.returncode != 0:
                logs += f"\nError: {result.stderr}"
                return False, logs
            return True, logs
        except Exception as e:
            return False, f"Failed to install {plugin_info.name}: {e}"

    async def restore_backup(
        self,
        backup_id: int,
        project_path: Optional[str] = None,
        options: Optional[RestoreOptions] = None,
    ) -> RestoreResult:
        """
        Restore from a backup.

        Args:
            backup_id: Backup ID
            project_path: Target project path for project-scoped backups
            options: Restore options

        Returns:
            RestoreResult with details
        """
        if options is None:
            options = RestoreOptions()

        logger.info("Restoring backup", extra={"backup_id": backup_id, "dry_run": options.dry_run})
        backup = await self.backup_service.get_backup(backup_id)
        if not backup:
            logger.warning("Restore failed: backup not found", extra={"backup_id": backup_id})
            return RestoreResult(success=False, message="Backup not found")

        if backup.scope == "codex":
            return RestoreResult(
                success=False,
                message=CODEX_RESTORE_REFUSAL_MESSAGE,
            )

        archive_path = Path(backup.file_path)
        if not archive_path.exists():
            return RestoreResult(
                success=False, message=f"Backup file not found: {archive_path}"
            )

        # Determine restore target
        if backup.scope == "project" and project_path:
            target_path = Path(project_path)
        else:
            target_path = get_user_home()

        result = RestoreResult(success=True, message="", dry_run=options.dry_run)
        manifest = self.backup_service.get_manifest_from_backup(backup.file_path)

        # Extract the archive
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.namelist():
                # Skip manifest
                if member == "manifest.json":
                    continue

                # Check selective restore
                if options.selective_restore:
                    if member not in options.selective_restore:
                        result.files_skipped += 1
                        continue

                # Skip skills if requested
                if options.skip_skills and ".claude/skills/" in member:
                    result.files_skipped += 1
                    continue

                # Skip plugins if requested
                if options.skip_plugins and ".claude/plugins/" in member:
                    result.files_skipped += 1
                    continue

                # Determine the full target path
                member_target = target_path / member

                if options.dry_run:
                    result.files_restored += 1
                    continue

                # Ensure parent directory exists
                member_target.parent.mkdir(parents=True, exist_ok=True)

                # Extract the file
                with zf.open(member) as source:
                    with open(member_target, "wb") as dest:
                        dest.write(source.read())

                result.files_restored += 1

        # Handle dependency installation if requested
        if options.install_dependencies and not options.dry_run and manifest:
            dep_result = await self.install_dependencies(
                backup_id,
                DependencyInstallRequest(
                    install_npm=True,
                    install_pip=True,
                    install_plugins=True,
                ),
            )
            result.dependency_results = dep_result.installed + dep_result.failed

        # Add manual steps from manifest
        if manifest:
            for skill in manifest.contents.skills:
                if skill.has_install_script:
                    result.manual_steps.append(
                        f"Run: cd ~/.claude/skills/{skill.name} && ./install.sh"
                    )

        result.message = (
            f"{'Would restore' if options.dry_run else 'Restored'} "
            f"{result.files_restored} files"
            + (f", skipped {result.files_skipped}" if result.files_skipped else "")
        )

        logger.info(
            "Backup restore complete",
            extra={"backup_id": backup_id, "dry_run": options.dry_run, "files_restored": result.files_restored, "files_skipped": result.files_skipped},
        )
        return result

    async def install_dependencies(
        self, backup_id: int, request: DependencyInstallRequest
    ) -> DependencyInstallResult:
        """
        Install dependencies from a backup.

        Args:
            backup_id: Backup ID
            request: What to install

        Returns:
            DependencyInstallResult
        """
        backup = await self.backup_service.get_backup(backup_id)
        if not backup:
            return DependencyInstallResult(
                success=False, message="Backup not found"
            )

        manifest = self.backup_service.get_manifest_from_backup(backup.file_path)
        if not manifest:
            return DependencyInstallResult(
                success=False, message="No manifest in backup"
            )

        result = DependencyInstallResult(success=True, message="")
        logs = []

        # Install skill dependencies
        if request.install_npm or request.install_pip:
            skills_dir = get_claude_user_skills_dir()
            for skill_info in manifest.contents.skills:
                # Filter by name if specified
                if request.skill_names and skill_info.name not in request.skill_names:
                    continue

                skill_path = skills_dir / skill_info.name
                if not skill_path.exists():
                    result.failed.append(
                        DependencyInstallStatus(
                            name=skill_info.name,
                            kind="skill",
                            success=False,
                            message=f"Skill directory not found: {skill_path}",
                        )
                    )
                    continue

                success, log = self._install_skill_dependencies(skill_path)
                logs.append(log)

                status = DependencyInstallStatus(
                    name=skill_info.name,
                    kind="skill",
                    success=success,
                    message="Dependencies installed" if success else "Installation failed",
                )

                if success:
                    result.installed.append(status)
                else:
                    result.failed.append(status)

        # Reinstall plugins
        if request.install_plugins:
            for plugin_info in manifest.contents.plugins:
                # Filter by name if specified
                if request.plugin_names and plugin_info.name not in request.plugin_names:
                    continue

                if plugin_info.install_command:
                    success, log = self._reinstall_plugin(plugin_info)
                    logs.append(log)

                    status = DependencyInstallStatus(
                        name=plugin_info.name,
                        kind="plugin",
                        success=success,
                        message="Plugin reinstalled" if success else "Reinstall failed",
                    )

                    if success:
                        result.installed.append(status)
                    else:
                        result.failed.append(status)

        result.logs = "\n".join(logs)
        result.message = (
            f"Installed {len(result.installed)} dependencies, "
            f"{len(result.failed)} failed"
        )

        if result.failed:
            result.success = False

        return result
