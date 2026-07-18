"""Disk storage for kanban card attachments (screenshots).

The binary lives under ``settings.kanban_attachment_dir``; the DB row (created
via the op-log, see ``operations._materialize``) only holds metadata. This
module owns the filesystem side effects — detecting/validating the image,
writing it atomically, unlinking it on delete — so ``_materialize`` (which also
runs during a ``rematerialize`` replay) never touches the filesystem.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.config import settings

# Magic-byte sniffing mirrors app.services.runs.attachments so both attachment
# flows accept the same image set (png/jpeg/gif/webp) without a shared import.
_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)


def detect_image(content: bytes) -> tuple[str, str]:
    """Return ``(mime_type, extension)`` or raise ``ValueError``."""
    max_bytes = settings.kanban_attachment_max_bytes
    if not content:
        raise ValueError("Attachment file is empty")
    if len(content) > max_bytes:
        raise ValueError(f"Attachment exceeds maximum size of {max_bytes} bytes")
    for signature, mime_type, extension in _IMAGE_SIGNATURES:
        if content.startswith(signature):
            return mime_type, extension
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise ValueError("Unsupported image type (expected png, jpeg, gif or webp)")


def _storage_root() -> Path:
    return Path(settings.kanban_attachment_dir).expanduser().resolve()


def save_attachment(card_id: str, content: bytes) -> dict:
    """Validate + persist ``content`` to disk for ``card_id``.

    Returns the metadata dict (``id``, ``filename``, ``mime_type``,
    ``size_bytes``, ``storage_path``) that the caller writes to the op-log.
    """
    mime_type, extension = detect_image(content)
    attachment_id = hashlib.sha256(content).hexdigest()[:16] + os.urandom(4).hex()
    card_dir = _storage_root() / _safe_segment(card_id)
    card_dir.mkdir(parents=True, exist_ok=True)
    storage_path = card_dir / f"{attachment_id}.{extension}"
    tmp_path = storage_path.with_suffix(storage_path.suffix + ".tmp")
    tmp_path.write_bytes(content)
    os.replace(tmp_path, storage_path)
    return {
        "id": attachment_id,
        "filename": f"{attachment_id}.{extension}",
        "mime_type": mime_type,
        "size_bytes": len(content),
        "storage_path": str(storage_path),
    }


def unlink_attachment(storage_path: str) -> None:
    """Best-effort delete of a stored file, refusing paths outside the root."""
    try:
        root = _storage_root()
        path = Path(storage_path).resolve()
        path.relative_to(root)
        if path.is_file():
            path.unlink()
    except (OSError, ValueError):
        return


def _safe_segment(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "_-" else "-" for c in value).strip("-.")
    return cleaned[:80] or hashlib.sha256(value.encode()).hexdigest()[:12]
