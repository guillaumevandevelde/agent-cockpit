"""APM (Agent Package Manager) service for managing dependencies per project."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

class ApmService:
    """Service for managing APM dependencies in projects."""

    @staticmethod
    def _find_apm_binary() -> str | None:
        """Find the APM binary in PATH."""
        import shutil
        return shutil.which("apm")

    @staticmethod
    def _get_apm_yml_path(project_path: str | None = None) -> Path:
        """Get the apm.yml path for a project."""
        if project_path:
            return Path(project_path) / "apm.yml"
        return Path.cwd() / "apm.yml"

    @staticmethod
    def _get_apm_lock_path(project_path: str | None = None) -> Path:
        """Get the apm.lock.yaml path for a project."""
        if project_path:
            return Path(project_path) / "apm.lock.yaml"
        return Path.cwd() / "apm.lock.yaml"

    @staticmethod
    def get_status(project_path: str | None = None) -> dict[str, Any]:
        """Get APM status for a project."""
        apm_binary = ApmService._find_apm_binary()
        apm_yml = ApmService._get_apm_yml_path(project_path)
        apm_lock = ApmService._get_apm_lock_path(project_path)

        return {
            "apm_installed": apm_binary is not None,
            "apm_binary_path": apm_binary,
            "apm_yml_exists": apm_yml.exists(),
            "apm_yml_path": str(apm_yml),
            "apm_lock_exists": apm_lock.exists(),
            "apm_lock_path": str(apm_lock),
            "project_path": project_path or str(Path.cwd()),
        }

    @staticmethod
    def list_dependencies(project_path: str | None = None) -> dict[str, Any]:
        """List dependencies from apm.yml."""
        apm_yml = ApmService._get_apm_yml_path(project_path)

        if not apm_yml.exists():
            return {
                "exists": False,
                "dependencies": [],
                "dev_dependencies": [],
                "project_path": project_path,
            }

        with open(apm_yml) as f:
            content = yaml.safe_load(f) or {}

        return {
            "exists": True,
            "name": content.get("name"),
            "version": content.get("version"),
            "dependencies": content.get("dependencies", {}),
            "dev_dependencies": content.get("devDependencies", {}),
            "project_path": project_path,
        }

    @staticmethod
    def add_dependency(
        name: str,
        source: str,
        project_path: str | None = None,
        is_dev: bool = False,
    ) -> dict[str, Any]:
        """Add a dependency to apm.yml."""
        apm_yml = ApmService._get_apm_yml_path(project_path)

        # Read existing or create new
        if apm_yml.exists():
            with open(apm_yml) as f:
                content = yaml.safe_load(f) or {}
        else:
            content = {
                "name": Path(project_path or Path.cwd()).name,
                "version": "0.1.0",
            }

        # Ensure dependencies structure
        if "dependencies" not in content:
            content["dependencies"] = {}
        if "apm" not in content["dependencies"]:
            content["dependencies"]["apm"] = []

        # Add the dependency
        dep_entry = {"github": source} if "/" in source else source
        if dep_entry not in content["dependencies"]["apm"]:
            content["dependencies"]["apm"].append(dep_entry)

        # Write back
        apm_yml.parent.mkdir(parents=True, exist_ok=True)
        with open(apm_yml, "w") as f:
            yaml.dump(content, f, default_flow_style=False, sort_keys=False)

        return {
            "success": True,
            "message": f"Added {source} to apm.yml",
            "dependency": dep_entry,
        }

    @staticmethod
    def remove_dependency(
        name: str,
        project_path: str | None = None,
    ) -> dict[str, Any]:
        """Remove a dependency from apm.yml."""
        apm_yml = ApmService._get_apm_yml_path(project_path)

        if not apm_yml.exists():
            return {"success": False, "message": "apm.yml not found"}

        with open(apm_yml) as f:
            content = yaml.safe_load(f) or {}

        # Find and remove the dependency
        apm_deps = content.get("dependencies", {}).get("apm", [])
        new_deps = []
        removed = False

        for dep in apm_deps:
            if isinstance(dep, dict):
                # Check if this is the dependency to remove
                for _key, value in dep.items():
                    if name in str(value):
                        removed = True
                        continue
                new_deps.append(dep)
            elif isinstance(dep, str) and name not in dep:
                new_deps.append(dep)
            elif isinstance(dep, str) and name in dep:
                removed = True

        content["dependencies"]["apm"] = new_deps

        with open(apm_yml, "w") as f:
            yaml.dump(content, f, default_flow_style=False, sort_keys=False)

        return {
            "success": removed,
            "message": f"Removed {name}" if removed else f"Dependency {name} not found",
        }

    @staticmethod
    def install_dependencies(
        project_path: str | None = None,
        frozen: bool = False,
    ) -> dict[str, Any]:
        """Run apm install for a project."""
        apm_binary = ApmService._find_apm_binary()
        if not apm_binary:
            return {"success": False, "message": "APM binary not found in PATH"}

        cmd = [apm_binary, "install"]
        if frozen:
            cmd.append("--frozen")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=project_path or str(Path.cwd()),
            )
            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "APM install timed out after 120 seconds"}
        except Exception:
            # The exception text reaches the client verbatim via
            # POST /apm/install (py/stack-trace-exposure, alert 234): a
            # subprocess failure here embeds absolute host paths. Log the full
            # trace server-side, hand the caller a generic message. The
            # frontend only toasts this string (ApmPage.tsx handleInstall), so
            # nothing downstream parses it.
            logger.exception("APM install failed")
            return {"success": False, "message": "APM install failed — see server logs for details"}

    @staticmethod
    def sync_dependencies(
        source_project: str,
        target_project: str,
    ) -> dict[str, Any]:
        """Sync apm.yml from source project to target project."""
        source_path = Path(source_project) / "apm.yml"
        target_path = Path(target_project) / "apm.yml"

        if not source_path.exists():
            return {"success": False, "message": f"Source apm.yml not found at {source_path}"}

        # Read source
        with open(source_path) as f:
            source_content = yaml.safe_load(f) or {}

        # Read target or create new
        if target_path.exists():
            with open(target_path) as f:
                target_content = yaml.safe_load(f) or {}
        else:
            target_content = {
                "name": Path(target_project).name,
                "version": "0.1.0",
            }

        # Merge dependencies
        source_deps = source_content.get("dependencies", {}).get("apm", [])
        target_deps = target_content.get("dependencies", {}).get("apm", [])

        # Add missing dependencies from source
        added = []
        for dep in source_deps:
            if dep not in target_deps:
                target_deps.append(dep)
                added.append(dep)

        # Ensure structure
        if "dependencies" not in target_content:
            target_content["dependencies"] = {}
        target_content["dependencies"]["apm"] = target_deps

        # Write target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w") as f:
            yaml.dump(target_content, f, default_flow_style=False, sort_keys=False)

        return {
            "success": True,
            "message": f"Synced {len(added)} dependencies from {source_project} to {target_project}",
            "added": added,
            "total_target_deps": len(target_deps),
        }

    @staticmethod
    def get_installed_modules(project_path: str | None = None) -> dict[str, Any]:
        """List installed APM modules in apm_modules/."""
        base_path = Path(project_path or Path.cwd()) / "apm_modules"

        if not base_path.exists():
            return {"exists": False, "modules": []}

        modules = []
        for item in base_path.iterdir():
            if item.is_dir():
                modules.append({
                    "name": item.name,
                    "path": str(item),
                })

        return {"exists": True, "modules": modules, "path": str(base_path)}
