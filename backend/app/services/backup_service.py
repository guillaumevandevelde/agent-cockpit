"""Service for managing configuration backups."""
import json
import logging
import os
import re
import sqlite3
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import Backup
from app.models.schemas import (
    BackupManifest,
    BackupManifestContents,
    BackupMCPServerInfo,
    BackupPluginInfo,
    BackupSkillDependency,
    BackupSkillInfo,
    DependencyInstallRequest,
    DependencyInstallResult,
    RestoreOptions,
    RestorePlan,
    RestoreResult,
)
from app.services.agentic_cli import get_agentic_cli
from app.services.agentic_cli.codex_cli import get_codex_home
from app.services.backup_shared import (
    CODEX_RESTORE_REFUSAL_MESSAGE,  # noqa: F401  (re-exported for tests/backup_service consumers)
    _get_claude_code_version,
    _get_current_platform,
    get_backup_storage_dir,
)
from app.services.cli_executor import AgenticCliExecutor
from app.services.restore_service import RestoreService
from app.utils.path_utils import (
    get_claude_user_agents_dir,
    get_claude_user_commands_dir,
    get_claude_user_config_file,
    get_claude_user_plugins_dir,
    get_claude_user_settings_file,
    get_claude_user_settings_local_file,
    get_claude_user_skills_dir,
    get_project_claude_dir,
    get_project_claude_md_file,
    get_project_mcp_config_file,
    get_user_home,
)

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(r"(token|secret|password|credential|api[_-]?key|auth|cookie|session)", re.I)
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<key>[A-Za-z0-9_.-]*(?:token|secret|password|credential|api[_-]?key|auth|cookie|session)[A-Za-z0-9_.-]*)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>[^\s,;]+)",
    re.I,
)
SENSITIVE_TOML_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^"
    r"(?P<prefix>\s*[A-Za-z0-9_.-]*(?:token|secret|password|credential|api[_-]?key|auth|cookie|session)"
    r"[A-Za-z0-9_.-]*\s*=\s*)"
    r"(?P<value>.+?)"
    r"(?P<suffix>\s*(?:#.*)?)$"
)


def kanban_db_path() -> Path | None:
    """Filesystem path of the kanban SQLite DB, parsed from the live
    ``settings.kanban_database_url`` setting.

    Returns ``None`` for non-sqlite URLs (a future Postgres-backed kanban
    store wouldn't have a single file to ship) and for in-memory sqlite
    URLs (``":memory:"``). The caller is expected to skip the inclusion
    when ``None`` — the backup is still valid, just without the board.
    """
    url = settings.kanban_database_url
    if not isinstance(url, str) or not url.startswith("sqlite"):
        return None
    db = make_url(url).database
    if not db or db == ":memory:":
        return None
    return Path(db)


def _is_kanban_db_held_open(path: Path) -> bool:
    """Return True iff the kanban DB at ``path`` is currently held open
    by another process (the running cockpit backend in the typical case).

    Used by ``RestoreService.restore_backup`` to refuse the opt-in
    kanban-DB restore path (kanban card
    141f2eba42444ddebc821d4182dd4cea, human direction B:
    refuse-while-running). The detection walks ``/proc/<pid>/fd`` and
    checks whether any *other* process has the kanban DB file open via
    a file descriptor — which is what the cockpit backend does at
    startup (``backend/app/kanban/db.py`` opens a connection pool that
    holds the primary file open for the lifetime of the process).

    Why not SQLite's ``BEGIN EXCLUSIVE`` for the check? In WAL mode the
    DB file is only SHARED-locked for reads and EXCLUSIVE-locked
    briefly during checkpoints; an idle WAL-mode connection does not
    hold a lock that another connection's ``BEGIN EXCLUSIVE`` can see,
    so the check would falsely report "free" against a running engine.
    The ``/proc`` walk sees the file descriptor regardless of whether
    SQLite is mid-transaction, mid-checkpoint, or just idle — which
    is the actual signal we want.

    Returns ``False`` when ``path`` doesn't exist (nothing to corrupt;
    the ZIP entry creates a fresh DB on disk) and on non-Linux
    platforms (Windows + macOS would need a different strategy; this
    detection is best-effort and the operator can still stop the
    cockpit manually). On Linux + path missing, returns ``False``.
    """
    if not path.exists():
        return False
    if not sys.platform.startswith("linux"):
        return False
    try:
        real = str(path.resolve())
        proc_root = Path("/proc")
        for fd_dir in proc_root.glob("*/fd"):
            pid_str = fd_dir.parent.name
            try:
                int(pid_str)
            except ValueError:
                continue
            # NOTE: do NOT skip our own PID. The cockpit backend opens
            # the kanban DB in the same process that handles the
            # ``/api/v1/backup/<id>/restore`` endpoint, so the live
            # file descriptor lives under our own ``/proc/self/fd``.
            # Skipping self would mean the restore path can never
            # detect the cockpit-itself case.
            try:
                for fd in fd_dir.iterdir():
                    try:
                        target = os.readlink(str(fd))
                    except OSError:
                        continue
                    if target == real or os.path.realpath(target) == real:
                        return True
            except OSError:
                # Process may have exited between glob and iterdir.
                continue
    except OSError:
        # ``/proc`` not available (container/sandbox without it).
        return False
    return False


class BackupService:
    """Service for managing configuration backups."""

    def __init__(self, db: AsyncSession, codex_home: Path | None = None):
        self.db = db
        self.codex_home = codex_home or get_codex_home()
        self._restore = RestoreService(self)

    def _get_user_config_paths(self) -> list[Path]:
        """Get all user-level configuration paths."""
        paths = []

        # Main config files
        for path_fn in [
            get_claude_user_config_file,
            get_claude_user_settings_file,
            get_claude_user_settings_local_file,
        ]:
            path = path_fn()
            if path.exists():
                paths.append(path)

        # Directories
        for dir_fn in [
            get_claude_user_commands_dir,
            get_claude_user_agents_dir,
            get_claude_user_skills_dir,
            get_claude_user_plugins_dir,
        ]:
            dir_path = dir_fn()
            if dir_path.exists():
                for file_path in dir_path.rglob("*"):
                    if file_path.is_file():
                        paths.append(file_path)

        return paths

    def _get_project_config_paths(self, project_path: str) -> list[Path]:
        """Get all project-level configuration paths."""
        paths = []

        # .claude directory
        claude_dir = get_project_claude_dir(project_path)
        if claude_dir.exists():
            for file_path in claude_dir.rglob("*"):
                if file_path.is_file():
                    paths.append(file_path)

        # .mcp.json
        mcp_file = get_project_mcp_config_file(project_path)
        if mcp_file.exists():
            paths.append(mcp_file)

        # CLAUDE.md
        claude_md = get_project_claude_md_file(project_path)
        if claude_md.exists():
            paths.append(claude_md)

        return paths

    def _get_kanban_db_path(self) -> Path | None:
        """Path of the kanban SQLite file if it exists on disk.

        The kanban DB is a one-per-machine store (see kanban-pro analyse
        §4.2 / kanban card 39d2d54a…), so it doesn't naturally fit under
        a project_path. We include it in the project + full backup set so
        a project (or whole box) can be restored without losing the
        board's institutional memory — every `**Summary:**` /
        ``**Impediment:**`` / Done summary, every deliverable, every
        dependency graph. Returns ``None`` when the file doesn't exist
        (the kanban DB may be on a different machine in a Docker deploy)
        so the backup picks up the rest of the project config without
        erroring.
        """
        path = kanban_db_path()
        if path is None or not path.is_file():
            return None
        return path

    def _snapshot_kanban_db(self, src: Path) -> Path:
        """Take a transactionally-consistent snapshot of a WAL-mode SQLite
        DB. Returns the path of the snapshot file (caller is responsible
        for cleanup).

        The kanban engine runs in WAL mode
        (``backend/app/kanban/db.py:34-42``); copying ``src`` directly
        with ``ZipFile.write`` silently misses any frames still in the
        ``src-wal`` sidecar, which can leave the ZIP holding a board
        that is hours behind the live one. ``sqlite3.Connection.backup``
        walks the source through the WAL and writes a fresh
        non-WAL-mode file, so the resulting snapshot contains every
        committed frame. This is the same pattern ``app/kanban/db.py``
        already uses for the legacy-file migration.

        The snapshot lands in the same directory as ``src`` so its
        ``relative_to(home)`` arcname is the canonical
        ``<home>/.claude-registry/kanban.db`` form a future restore
        expects. The ``-wal`` / ``-shm`` sidecars from the snapshot
        itself are not part of the backup — the snapshot is a fresh,
        non-WAL-mode file.
        """
        # Generate a unique sibling path so concurrent backups don't
        # race on the same file.
        suffix = f".snap-{datetime.now().timestamp():.0f}-{id(self)}.db"
        tmp = src.with_name(src.name + suffix)
        # The source connection must be opened in the default mode (NOT
        # read-only) so it sees the WAL frames. The backup API only
        # reads.
        src_conn = sqlite3.connect(str(src))
        try:
            dst_conn = sqlite3.connect(str(tmp))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        return tmp

    def _get_codex_config_paths(self) -> list[Path]:
        """Get safe Codex configuration files for export-only backups."""
        paths: list[Path] = []
        config_file = self.codex_home / "config.toml"
        if config_file.exists() and config_file.is_file():
            paths.append(config_file)

        if self.codex_home.exists():
            for profile in sorted(self.codex_home.glob("*.config.toml")):
                if profile.is_file():
                    paths.append(profile)

            rules_dir = self.codex_home / "rules"
            if rules_dir.exists():
                for rule in sorted(rules_dir.glob("*.rules")):
                    if rule.is_file():
                        paths.append(rule)

        return paths

    def _get_codex_backup_policy(self) -> dict[str, Any]:
        """Return the Codex export/restore policy used in manifests and status."""
        try:
            cli_policy = get_agentic_cli("codex-cli").get_backup_policy()
        except Exception:
            cli_policy = None
        return dict(cli_policy or {
            "provider": "codex-cli",
            "export_supported": True,
            "automatic_restore_supported": False,
            "restore_mode": "manual_review",
            "included": [
                "config.toml with secret-like assignments redacted",
                "*.config.toml profile files with secret-like assignments redacted",
                "rules/*.rules files with secret-like assignments redacted",
                "redacted provider inventory metadata",
            ],
            "excluded": [
                "auth.json",
                "history.jsonl",
                "models_cache.json",
                "*.sqlite and related SQLite sidecar files",
                "raw cache payloads and prompt text",
            ],
            "restore_refusal_reasons": [
                "Codex auth, history, cache, and local state are intentionally excluded from exports.",
                "Automatic restore could overwrite active Codex state without a stable provider-owned restore API.",
            ],
        })

    def _redact_value(self, value: Any, parent_key: str = "") -> Any:
        """Redact sensitive values from generated backup metadata."""
        if value is None:
            return None
        if isinstance(value, dict):
            return {
                key: "[redacted]" if SENSITIVE_KEY_PATTERN.search(key) or SENSITIVE_KEY_PATTERN.search(parent_key)
                else self._redact_value(child, key)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [self._redact_value(item, parent_key) for item in value]
        if SENSITIVE_KEY_PATTERN.search(parent_key):
            return "[redacted]"
        if isinstance(value, str):
            return SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\g<key>\g<sep>[redacted]", value)
        return value

    def _redact_text_content(self, content: str) -> str:
        """Redact obvious secret assignments in text files before exporting."""
        redacted = SENSITIVE_TOML_ASSIGNMENT_PATTERN.sub(
            lambda match: f'{match.group("prefix")}"[redacted]"{match.group("suffix")}',
            content,
        )
        return SENSITIVE_ASSIGNMENT_PATTERN.sub(r'\g<key>\g<sep>"[redacted]"', redacted)

    def _read_redacted_file(self, path: Path) -> str:
        try:
            return self._redact_text_content(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            return self._redact_text_content(path.read_text(errors="replace"))

    def _get_codex_provider_inventory(self, paths: list[Path]) -> dict[str, Any]:
        """Collect safe provider metadata for Codex export manifests."""
        inventory: dict[str, Any] = {
            "provider": "codex-cli",
            "codex_home": str(self.codex_home),
            "backup_policy": self._get_codex_backup_policy(),
            "files": [
                {
                    "path": str(path),
                    "scope": (
                        "user" if path.name == "config.toml"
                        else "rules" if path.suffix == ".rules"
                        else "profile"
                    ),
                }
                for path in paths
            ],
        }

        try:
            executor = AgenticCliExecutor("codex-cli")
            if not executor.binary_path:
                inventory["cli"] = {"installed": False}
                return inventory
        except Exception as exc:
            inventory["cli"] = {"installed": False, "error": str(exc)}
            return inventory

        inventory["cli"] = {"installed": True, "binary_path": executor.binary_path}

        mcp_result = executor.execute("mcp", ["list", "--json"], timeout=30)
        mcp_inventory: dict[str, Any] = {
            "exit_code": mcp_result.exit_code,
            "stderr": self._redact_value(mcp_result.stderr),
        }
        if mcp_result.stdout.strip():
            try:
                mcp_inventory["servers"] = self._redact_value(json.loads(mcp_result.stdout))
            except json.JSONDecodeError as exc:
                mcp_inventory["parse_error"] = str(exc)
                mcp_inventory["raw_stdout"] = self._redact_value(mcp_result.stdout)
        inventory["mcp"] = mcp_inventory

        plugin_result = executor.execute("plugin", ["list"], timeout=30)
        inventory["plugins"] = {
            "exit_code": plugin_result.exit_code,
            "stderr": self._redact_value(plugin_result.stderr),
            "raw_stdout": self._redact_value(plugin_result.stdout),
        }

        return self._redact_value(inventory)

    def _detect_skill_dependencies(self, skill_path: Path) -> BackupSkillInfo:
        """Detect dependencies in a skill directory."""
        skill_name = skill_path.name
        info = BackupSkillInfo(
            name=skill_name,
            path=str(skill_path),
        )

        # Check for package.json
        package_json = skill_path / "package.json"
        if package_json.exists():
            info.has_package_json = True
            try:
                with open(package_json) as f:
                    pkg = json.load(f)
                    deps = pkg.get("dependencies", {})
                    dev_deps = pkg.get("devDependencies", {})
                    for name, version in {**deps, **dev_deps}.items():
                        info.dependencies.append(
                            BackupSkillDependency(kind="npm", name=name, version=version)
                        )
            except Exception:
                pass

        # Check for requirements.txt
        requirements_txt = skill_path / "requirements.txt"
        if requirements_txt.exists():
            info.has_requirements_txt = True
            try:
                with open(requirements_txt) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            # Parse package==version or package>=version
                            for sep in ["==", ">=", "<=", "~=", "!="]:
                                if sep in line:
                                    name, version = line.split(sep, 1)
                                    info.dependencies.append(
                                        BackupSkillDependency(
                                            kind="pip", name=name.strip(), version=version.strip()
                                        )
                                    )
                                    break
                            else:
                                info.dependencies.append(
                                    BackupSkillDependency(kind="pip", name=line)
                                )
            except Exception:
                pass

        # Check for install.sh
        install_sh = skill_path / "install.sh"
        if install_sh.exists():
            info.has_install_script = True

        return info

    def _get_plugin_install_info(self, plugin_name: str, plugin_path: Path) -> BackupPluginInfo:
        """Get plugin install information from plugin metadata."""
        info = BackupPluginInfo(name=plugin_name)

        # Try to read plugin manifest
        manifest_path = plugin_path / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
                    info.version = manifest.get("version")
                    info.source = manifest.get("source")
            except Exception:
                pass

        # Check for .source file that claude creates
        source_file = plugin_path / ".source"
        if source_file.exists():
            try:
                with open(source_file) as f:
                    source_data = json.load(f)
                    info.marketplace = source_data.get("marketplace")
                    info.install_command = source_data.get("install_command")
            except Exception:
                pass

        return info

    def _detect_mcp_server_info(
        self, name: str, config: dict[str, Any], scope: str
    ) -> BackupMCPServerInfo:
        """Extract MCP server info from config."""
        server_type = "stdio"
        if "url" in config:
            server_type = "sse" if "sse" in config.get("url", "").lower() else "http"

        info = BackupMCPServerInfo(
            name=name,
            type=server_type,
            scope=scope,
            command=config.get("command"),
            args=config.get("args"),
            url=config.get("url"),
        )

        # Check if it's an npx command that might need npm install
        if info.command and info.command.startswith("npx"):
            info.requires_npm_install = True

        return info

    def _generate_manifest(
        self,
        paths: list[Path],
        scope: str,
        extra_files: dict[str, str] | None = None,
        provider_inventory: dict[str, Any] | None = None,
    ) -> BackupManifest:
        """Generate a backup manifest with all dependency information."""
        contents = BackupManifestContents()

        # Track files
        home = get_user_home()
        for path in paths:
            try:
                rel_path = str(path.relative_to(home))
            except ValueError:
                rel_path = str(path)
            contents.files.append(rel_path)
        if extra_files:
            contents.files.extend(extra_files.keys())
        if provider_inventory:
            contents.provider_inventory = provider_inventory
            contents.backup_policy = provider_inventory.get("backup_policy", {})

        # Detect skills
        if scope != "codex":
            skills_dir = get_claude_user_skills_dir()
            if skills_dir.exists():
                for skill_path in skills_dir.iterdir():
                    if skill_path.is_dir():
                        skill_info = self._detect_skill_dependencies(skill_path)
                        contents.skills.append(skill_info)

            # Detect plugins
            plugins_dir = get_claude_user_plugins_dir()
            if plugins_dir.exists():
                for plugin_path in plugins_dir.iterdir():
                    if plugin_path.is_dir():
                        plugin_info = self._get_plugin_install_info(plugin_path.name, plugin_path)
                        contents.plugins.append(plugin_info)

            # Detect MCP servers from user config
            config_file = get_claude_user_config_file()
            if config_file.exists():
                try:
                    with open(config_file) as f:
                        config = json.load(f)
                        mcp_servers = config.get("mcpServers", {})
                        for name, srv_config in mcp_servers.items():
                            mcp_info = self._detect_mcp_server_info(name, srv_config, "user")
                            contents.mcp_servers.append(mcp_info)
                except Exception:
                    pass

            # Detect agents
            agents_dir = get_claude_user_agents_dir()
            if agents_dir.exists():
                for agent_file in agents_dir.glob("*.md"):
                    contents.agents.append(agent_file.stem)

            # Detect commands
            commands_dir = get_claude_user_commands_dir()
            if commands_dir.exists():
                for cmd_file in commands_dir.rglob("*.md"):
                    try:
                        rel = cmd_file.relative_to(commands_dir)
                        contents.commands.append(str(rel).replace(".md", ""))
                    except ValueError:
                        contents.commands.append(cmd_file.stem)

        manifest = BackupManifest(
            created_at=datetime.now(UTC).isoformat(),
            claude_code_version=None if scope == "codex" else _get_claude_code_version(),
            platform=_get_current_platform(),
            scope=scope,
            contents=contents,
        )

        return manifest

    def _create_archive(
        self,
        name: str,
        paths: list[Path],
        scope: str,
        base_path: Path | None = None,
        extra_files: dict[str, str] | None = None,
        file_overrides: dict[Path, str] | None = None,
        provider_inventory: dict[str, Any] | None = None,
        path_renames: dict[Path, str] | None = None,
    ) -> tuple[Path, int, BackupManifest]:
        """
        Create a zip archive from the given paths.

        Args:
            name: Backup name
            paths: List of file paths to include
            scope: Backup scope
            base_path: Base path for relative paths in archive
            extra_files: Synthetic files to include (arcname → content)
            file_overrides: Map input ``Path`` → replacement content
                string. Used by the codex branch to inline redacted
                text. Distinct from ``path_renames`` which only changes
                the arcname.
            path_renames: Map input ``Path`` → final arcname string.
                Used by the kanban snapshot path so the WAL-safe
                snapshot lands in the ZIP as the canonical
                ``kanban.db`` rather than ``kanban.db.snap-…db``.

        Returns:
            Tuple of (archive_path, size_bytes, manifest)
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{name}_{timestamp}.zip"
        archive_path = get_backup_storage_dir() / archive_name

        # Generate manifest
        manifest = self._generate_manifest(paths, scope, extra_files, provider_inventory)

        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add manifest.json first
            zf.writestr("manifest.json", manifest.model_dump_json(indent=2))
            for arcname, content in (extra_files or {}).items():
                zf.writestr(arcname, content)

            for file_path in paths:
                if base_path:
                    try:
                        arcname = str(file_path.relative_to(base_path))
                    except ValueError:
                        arcname = str(file_path)
                else:
                    # Use path relative to home for user configs
                    try:
                        arcname = str(file_path.relative_to(get_user_home()))
                    except ValueError:
                        arcname = str(file_path)

                if path_renames and file_path in path_renames:
                    arcname = path_renames[file_path]
                if file_overrides and file_path in file_overrides:
                    zf.writestr(arcname, file_overrides[file_path])
                else:
                    zf.write(file_path, arcname)

        size_bytes = archive_path.stat().st_size
        return archive_path, size_bytes, manifest

    async def create_backup(
        self,
        name: str,
        scope: str,
        project_path: str | None = None,
        description: str | None = None,
        project_id: int | None = None,
        is_automatic: bool = False,
    ) -> tuple[Backup, BackupManifest]:
        """
        Create a new backup.

        Args:
            name: Backup name
            scope: Scope ("full", "user", "project")
            project_path: Project path for project/full scope
            description: Optional description
            project_id: Optional project ID reference
            is_automatic: Whether this backup was created by the scheduler

        Returns:
            Tuple of (Backup record, BackupManifest)
        """
        logger.info("Creating backup", extra={"backup_name": name, "scope": scope, "automatic": is_automatic})
        paths = []
        extra_files: dict[str, str] = {}
        file_overrides: dict[Path, str] = {}
        path_renames: dict[Path, str] = {}
        provider_inventory: dict[str, Any] | None = None
        # Snapshots to clean up after the zip is written — see kanban
        # card 39d2d54a…. We don't append the live WAL-mode DB directly
        # because the ZIP would silently miss any un-checkpointed frames.
        _kanban_snapshots: list[Path] = []

        if scope in ["full", "user"]:
            paths.extend(self._get_user_config_paths())

        if scope in ["full", "project"] and project_path:
            paths.extend(self._get_project_config_paths(project_path))

        # The kanban DB is a global store but ticket 39d2d54a… requires
        # it to ride along in any project / full backup so a board can
        # be restored after a schema-rot wrecks the live DB. Skipped
        # silently when the file doesn't exist (the DB may live on a
        # different volume in a Docker deploy); the helper returns None
        # in that case. For ``project`` scope it is only included when a
        # ``project_path`` was supplied — the historical contract that
        # empty project-scope backups must raise is preserved.
        #
        # We do NOT pass the live file to ``paths``: the kanban engine
        # runs in WAL mode (``backend/app/kanban/db.py:34-42``), and a
        # plain ``ZipFile.write(kanban.db)`` would silently omit any
        # frames still resident in ``kanban.db-wal``. Instead, snapshot
        # the DB through SQLite's online backup API into a temp file
        # sitting next to the live DB; the snapshot's path is therefore
        # ``<...>/kanban.db.snap-*.db``. We then rename the arcname
        # (via ``path_renames``) so the ZIP entry is the canonical
        # ``<home>/.claude-registry/kanban.db`` a future restore
        # expects. ``file_overrides`` is wrong here — that map is for
        # inlining redacted file content (the codex branch).
        if scope == "full" or (scope == "project" and project_path):
            kanban_path = self._get_kanban_db_path()
            if kanban_path is not None:
                snap = self._snapshot_kanban_db(kanban_path)
                _kanban_snapshots.append(snap)
                # Restore-side expects a file named exactly ``kanban.db``
                # at the canonical location, so the arcname is the
                # home-relative path of the *live* DB — not the snapshot.
                home = get_user_home()
                try:
                    canonical_arcname = str(kanban_path.relative_to(home))
                except ValueError:
                    canonical_arcname = str(kanban_path)
                path_renames[snap] = canonical_arcname
                paths.append(snap)

        if scope == "codex":
            paths.extend(self._get_codex_config_paths())
            provider_inventory = self._get_codex_provider_inventory(paths)
            inventory_arcname = str(
                (self.codex_home / "provider-inventory.json").relative_to(self.codex_home.parent)
            )
            extra_files[inventory_arcname] = json.dumps(
                provider_inventory,
                indent=2,
                sort_keys=True,
            )
            file_overrides = {
                path: self._read_redacted_file(path)
                for path in paths
            }

        if not paths:
            raise ValueError("No configuration files found to backup")

        # Determine base path for relative paths
        base_path = None
        if scope == "project" and project_path:
            base_path = Path(project_path)
        elif scope == "codex":
            base_path = self.codex_home.parent

        archive_path, size_bytes, manifest = self._create_archive(
            name,
            paths,
            scope,
            base_path,
            extra_files=extra_files,
            file_overrides=file_overrides,
            provider_inventory=provider_inventory,
            path_renames=path_renames or None,
        )

        # The snapshot is now safely inside the zip — drop the temp
        # file. Best-effort: a leftover snapshot is recoverable (next
        # run will overwrite), so a transient cleanup failure must not
        # fail the backup.
        for snap in _kanban_snapshots:
            try:
                snap.unlink()
            except OSError as exc:
                logger.warning(
                    "Failed to remove kanban snapshot %s: %s",
                    snap, exc,
                )

        backup = Backup(
            name=name,
            description=description,
            file_path=str(archive_path),
            scope=scope,
            project_id=project_id,
            size_bytes=size_bytes,
            is_automatic=is_automatic,
        )

        self.db.add(backup)
        await self.db.commit()
        await self.db.refresh(backup)

        logger.info("Backup created", extra={"backup_id": backup.id, "backup_name": name, "scope": scope, "size_bytes": size_bytes})
        return backup, manifest

    async def list_backups(self) -> list[Backup]:
        """List all backups."""
        result = await self.db.execute(
            select(Backup).order_by(Backup.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_backup(self, backup_id: int) -> Backup | None:
        """Get a backup by ID."""
        result = await self.db.execute(select(Backup).where(Backup.id == backup_id))
        return result.scalar_one_or_none()

    async def delete_backup(self, backup_id: int) -> bool:
        """
        Delete a backup.

        Args:
            backup_id: Backup ID

        Returns:
            True if deleted, False if not found
        """
        backup = await self.get_backup(backup_id)
        if not backup:
            logger.warning("Delete failed: backup not found", extra={"backup_id": backup_id})
            return False

        # Delete the archive file
        archive_path = Path(backup.file_path)
        if archive_path.exists():
            archive_path.unlink()

        # Delete the database record
        await self.db.delete(backup)
        await self.db.commit()

        logger.info("Backup deleted", extra={"backup_id": backup_id, "backup_name": backup.name})
        return True

    def get_manifest_from_backup(self, file_path: str) -> BackupManifest | None:
        """Extract manifest from a backup zip file."""
        archive_path = Path(file_path)
        if not archive_path.exists():
            return None

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                if "manifest.json" in zf.namelist():
                    manifest_data = zf.read("manifest.json")
                    return BackupManifest.model_validate_json(manifest_data)
        except Exception:
            pass

        return None

    # Restore operations delegate to RestoreService (see restore_service.py).

    async def get_restore_plan(
        self, backup_id: int, project_path: str | None = None
    ) -> RestorePlan | None:
        """Analyze a backup and generate a restore plan."""
        return await self._restore.get_restore_plan(backup_id, project_path)

    async def validate_backup(self, backup_id: int) -> tuple[bool, list[str]]:
        """Validate a backup before restore."""
        return await self._restore.validate_backup(backup_id)

    async def restore_backup(
        self,
        backup_id: int,
        project_path: str | None = None,
        options: RestoreOptions | None = None,
    ) -> RestoreResult:
        """Restore from a backup."""
        return await self._restore.restore_backup(backup_id, project_path, options)

    async def install_dependencies(
        self, backup_id: int, request: DependencyInstallRequest
    ) -> DependencyInstallResult:
        """Install dependencies from a backup."""
        return await self._restore.install_dependencies(backup_id, request)

    def get_backup_contents(self, backup_id: int, file_path: str) -> list[str]:
        """
        Get the list of files in a backup.

        Args:
            backup_id: Backup ID (not used, file_path is used directly)
            file_path: Path to the backup file

        Returns:
            List of file names in the archive
        """
        archive_path = Path(file_path)
        if not archive_path.exists():
            return []

        with zipfile.ZipFile(archive_path, "r") as zf:
            return [f for f in zf.namelist() if f != "manifest.json"]

    async def export_config(
        self, paths: list[str], name: str = "export"
    ) -> tuple[Path, int]:
        """
        Export specific configuration files.

        Args:
            paths: List of absolute paths to export
            name: Export name

        Returns:
            Tuple of (archive_path, size_bytes)
        """
        valid_paths = [Path(p) for p in paths if Path(p).exists()]
        if not valid_paths:
            raise ValueError("No valid paths to export")

        archive_path, size_bytes, _ = self._create_archive(name, valid_paths, "export")
        return archive_path, size_bytes
