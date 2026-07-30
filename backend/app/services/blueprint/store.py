"""File-based store for `Blueprint` JSON documents.

Blueprints live at ``~/.claude-registry/blueprints/<name>.json`` — one file
per blueprint, named after the blueprint's ``name`` field. The store is
deliberately minimal: read / write / delete / list. Validation of the
``name`` shape is the only thing it does beyond straight filesystem
operations.

The store is **not** the engine — `BlueprintService` (sibling module) is.
The store just persists documents; the engine consumes them.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import Blueprint

logger = logging.getLogger(__name__)


# Blueprint names are filesystem-safe slugs: lowercase, digits, dot,
# underscore, dash. No slashes (those would let callers escape the store
# directory), no leading dot (those would create hidden files).
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESERVED_NAME = ".tmp"


class BlueprintStoreError(Exception):
    """Base class for BlueprintStore errors."""


class BlueprintNameError(BlueprintStoreError):
    """Raised when a blueprint name fails the slug validation."""


class BlueprintAlreadyExists(BlueprintStoreError):
    """Raised by `save()` when overwrite=False and the file exists."""


class BlueprintNotFound(BlueprintStoreError):
    """Raised by `get()` / `delete()` when the file does not exist."""


class BlueprintStore:
    """File-backed CRUD for blueprint documents.

    Default root is ``~/.claude-registry/blueprints/``; tests can pass a
    `tmp_path`-style directory explicitly. The store creates the root on
    first use so callers don't have to.
    """

    def __init__(self, root: Path | str | None = None):
        if root is None:
            root = Path.home() / ".claude-registry" / "blueprints"
        self.root = Path(root)

    # -- name validation --------------------------------------------------

    @staticmethod
    def validate_name(name: str) -> str:
        """Reject names that would escape the store or create awkward files.

        Returns the validated name unchanged so callers can chain
        ``bp = Blueprint(name=BlueprintStore.validate_name(raw))``.
        """
        if not isinstance(name, str) or not name:
            raise BlueprintNameError("blueprint name must be a non-empty string")
        if name != name.strip():
            raise BlueprintNameError("blueprint name has leading or trailing whitespace")
        if not _NAME_PATTERN.match(name):
            raise BlueprintNameError(
                "blueprint name must match "
                f"{_NAME_PATTERN.pattern!r} (lowercase, digits, ._-; max 64 chars)"
            )
        if name == _RESERVED_NAME:
            raise BlueprintNameError(f"blueprint name {_RESERVED_NAME!r} is reserved")
        return name

    def _path_for(self, name: str) -> Path:
        """Resolve a validated name to its on-disk path. Refuses traversal."""
        validated = self.validate_name(name)
        target = (self.root / f"{validated}.json").resolve()
        if target.parent != self.root.resolve():
            # Defence in depth: a future pattern change must never let a
            # caller escape the store root.
            raise BlueprintNameError(f"blueprint name {validated!r} escapes store root")
        return target

    # -- root management --------------------------------------------------

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    # -- CRUD -------------------------------------------------------------

    def list(self) -> list[Blueprint]:
        """Return all blueprints in the store, sorted by name."""
        if not self.root.exists():
            return []
        out: list[Blueprint] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                out.append(self._read(path))
            except Exception:
                logger.warning("blueprint store: failed to read %s, skipping", path)
                continue
        return out

    def get(self, name: str) -> Blueprint:
        """Load a blueprint by name. Raises BlueprintNotFound if missing."""
        path = self._path_for(name)
        if not path.exists():
            raise BlueprintNotFound(f"blueprint {name!r} not found")
        return self._read(path)

    def save(self, blueprint: Blueprint, *, overwrite: bool = True) -> Blueprint:
        """Persist a blueprint. Atomic write via temp-file + rename.

        `created_at` is stamped on first save; `updated_at` is bumped on
        every save. Returns the persisted copy (with timestamps filled in)
        so callers can echo the canonical state back to the client.
        """
        name = self.validate_name(blueprint.name)
        if blueprint.name != name:
            # Defensive: keep the model's name in sync with what we stored it under.
            blueprint = blueprint.model_copy(update={"name": name})

        path = self._path_for(name)
        self._ensure_root()
        if path.exists() and not overwrite:
            raise BlueprintAlreadyExists(f"blueprint {name!r} already exists")

        now = datetime.now(UTC)
        if blueprint.created_at is None:
            # "First save" means the first save of this *name*, not of this
            # in-memory object. Callers that update via REST build a fresh
            # Blueprint from the request body, so `created_at` is None on every
            # update; trusting it would re-stamp `created_at` each time and lose
            # the original. Recover it from the record already on disk.
            blueprint = blueprint.model_copy(
                update={"created_at": self._stored_created_at(path) or now}
            )
        blueprint = blueprint.model_copy(update={"updated_at": now})

        # Atomic write: temp file in the same directory, fsync, rename.
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{name}.", suffix=".json.tmp", dir=str(self.root),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(blueprint.model_dump(mode="json"), fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except Exception:
            # Best-effort cleanup of the temp file if the rename never landed.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        logger.info("blueprint store: saved %s at %s", name, path)
        return blueprint

    def delete(self, name: str) -> None:
        """Remove a blueprint. Raises BlueprintNotFound if missing."""
        path = self._path_for(name)
        if not path.exists():
            raise BlueprintNotFound(f"blueprint {name!r} not found")
        path.unlink()
        logger.info("blueprint store: deleted %s", path)

    # -- internal ---------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> Blueprint:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Blueprint.model_validate(data)

    @staticmethod
    def _stored_created_at(path: Path) -> datetime | None:
        """`created_at` of the record already at `path`, or None.

        Best-effort by design: a missing, unreadable, or corrupt file must not
        block a save — the caller falls back to stamping the current time.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        raw = data.get("created_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None