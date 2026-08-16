"""Project management service for discovering and managing local projects."""
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Project
from app.models.schemas import ProjectBase, ProjectCreate, ProjectResponse, ProjectUpdate
from app.services.config_service import ConfigService
from app.utils.path_utils import (
    convert_path_to_folder_name,
    get_claude_projects_dir,
    get_project_claude_dir,
    get_project_mcp_config_file,
)

logger = logging.getLogger(__name__)


def _to_response(project: Project) -> ProjectResponse:
    """Serialize a ``Project`` ORM row to its API response schema."""
    return ProjectResponse(
        id=project.id,
        name=project.name,
        path=project.path,
        kind=project.kind,
        priority=project.priority,
        ceremony_profile=getattr(project, "ceremony_profile", "code"),
        is_active=project.is_active,
        last_accessed=project.last_accessed.isoformat(),
        created_at=project.created_at.isoformat(),
    )


class ProjectService:
    """Service for managing local projects."""

    def __init__(self, db: AsyncSession):
        """Initialize the project service."""
        self.db = db

    async def list_projects(self) -> list[ProjectResponse]:
        """List all tracked projects from the database."""
        result = await self.db.execute(select(Project).order_by(Project.last_accessed.desc()))
        projects = result.scalars().all()

        return [_to_response(p) for p in projects]

    async def add_project(self, project_data: ProjectCreate) -> ProjectResponse:
        """Add a project manually to the database."""
        # Check if project already exists
        result = await self.db.execute(
            select(Project).where(Project.path == project_data.path)
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing project
            existing.name = project_data.name
            existing.kind = project_data.kind
            existing.priority = project_data.priority
            existing.last_accessed = datetime.now(UTC)
            await self.db.commit()
            await self.db.refresh(existing)

            return _to_response(existing)

        # Create new project
        new_project = Project(
            name=project_data.name,
            path=project_data.path,
            kind=project_data.kind,
            priority=project_data.priority,
            ceremony_profile=project_data.ceremony_profile,
            is_active=False,
            last_accessed=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )

        self.db.add(new_project)
        await self.db.commit()
        await self.db.refresh(new_project)

        return _to_response(new_project)

    async def remove_project(self, project_id: int) -> bool:
        """Remove a project from the database."""
        result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()

        if not project:
            return False

        await self.db.delete(project)
        await self.db.commit()
        return True

    async def update_project(
        self, project_id: int, data: ProjectUpdate
    ) -> ProjectResponse | None:
        """Patch mutable project fields (name, is_active, kind, priority).

        Only fields explicitly set on ``data`` are applied, so a PATCH with
        just ``{"kind": "meta"}`` leaves everything else untouched.
        """
        result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()

        if not project:
            return None

        updates = data.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(project, field, value)

        await self.db.commit()
        await self.db.refresh(project)

        return _to_response(project)

    def discover_projects(self, base_path: str) -> list[ProjectBase]:
        """
        Scan a directory for project candidates.

        Claude Code metadata is preserved when present:
        - .claude/ directory (source="configured")
        - .mcp.json file (source="configured")
        - A matching entry in ~/.claude/projects/ (source="session_history")
        Otherwise, regular directories are returned with source="directory".
        """
        discovered = []
        discovered_paths: set[str] = set()
        base_dir = Path(base_path).expanduser().resolve()

        if not base_dir.exists() or not base_dir.is_dir():
            return []

        def add_discovered(directory: Path, source: str) -> None:
            dir_str = str(directory)
            if dir_str in discovered_paths:
                return
            discovered.append(
                ProjectBase(
                    name=directory.name,
                    path=dir_str,
                    source=source,
                )
            )
            discovered_paths.add(dir_str)

        # Scan the base directory and its immediate subdirectories
        dirs_to_check = [base_dir]

        # Add subdirectories (one level deep)
        try:
            for item in base_dir.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    dirs_to_check.append(item)
        except PermissionError:
            pass

        # Phase 1: Check for .claude/ directory or .mcp.json file
        for directory in dirs_to_check:
            try:
                claude_dir = get_project_claude_dir(str(directory))
                mcp_file = get_project_mcp_config_file(str(directory))

                if claude_dir.exists() or mcp_file.exists():
                    add_discovered(directory, "configured")
            except (PermissionError, OSError):
                continue

        # Phase 2: Check ~/.claude/projects/ for session history
        try:
            global_projects_dir = get_claude_projects_dir()
            if global_projects_dir.exists():
                global_entries = {
                    entry.name
                    for entry in global_projects_dir.iterdir()
                    if entry.is_dir()
                }

                for directory in dirs_to_check:
                    dir_str = str(directory)
                    if dir_str in discovered_paths:
                        continue

                    encoded = convert_path_to_folder_name(dir_str)
                    if encoded in global_entries:
                        add_discovered(directory, "session_history")
        except (PermissionError, OSError):
            pass

        # Phase 3: Include ordinary folders so projects are provider-agnostic.
        for directory in dirs_to_check:
            add_discovered(directory, "directory")

        return discovered

    async def set_active_project(self, project_id: int) -> ProjectResponse | None:
        """Set a project as the active project context."""
        # First, deactivate all projects
        result = await self.db.execute(select(Project))
        all_projects = result.scalars().all()

        for p in all_projects:
            p.is_active = False

        # Then activate the requested project
        result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()

        if not project:
            await self.db.commit()
            return None

        project.is_active = True
        project.last_accessed = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(project)

        return _to_response(project)

    async def clear_active_project(self) -> bool:
        """Clear the active project (deactivate all projects)."""
        result = await self.db.execute(select(Project))
        all_projects = result.scalars().all()

        for p in all_projects:
            p.is_active = False

        await self.db.commit()
        return True

    async def get_project_config(self, project_id: int) -> dict | None:
        """Get project-specific configuration."""
        result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()

        if not project:
            return None

        # Use ConfigService to get merged config for this project
        config_service = ConfigService()
        merged = config_service.get_merged_config(project_path=project.path)

        return {
            "project": _to_response(project).model_dump(),
            "config": merged.model_dump(),
        }

    async def get_active_project(self) -> ProjectResponse | None:
        """Get the currently active project."""
        result = await self.db.execute(
            select(Project).where(Project.is_active)
        )
        project = result.scalar_one_or_none()

        if not project:
            return None

        return _to_response(project)
