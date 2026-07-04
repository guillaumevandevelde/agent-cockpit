"""Shared constants and helpers used by both BackupService and RestoreService."""
import platform
import subprocess
from pathlib import Path
from typing import Optional

from app.utils.path_utils import get_user_home

CODEX_RESTORE_REFUSAL_MESSAGE = (
    "Codex backups are export-only; automatic restore is not supported because "
    "exports intentionally exclude auth, history, cache, and local SQLite state."
)


def get_backup_storage_dir() -> Path:
    """Get the backup storage directory."""
    backup_dir = get_user_home() / ".claude-registry" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _get_current_platform() -> str:
    """Get current platform identifier."""
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    elif system == "windows":
        return "win32"
    return "linux"


def _get_claude_code_version() -> Optional[str]:
    """Try to get Claude Code version."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None
