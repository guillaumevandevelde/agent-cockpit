"""Project-scoped secrets store.

Cockpit needs a place to keep per-project secrets (provider API keys,
MCP bearer tokens, etc.) that survives across processes but never
appears in plaintext on disk. The interface in this module is small
on purpose: ``get / put / list / delete``, one ``project_key`` per
caller. Implementations are pluggable — the MVP is ``AGESecretStore``
(symmetric scrypt + ChaCha20-Poly1305 encryption to a flat file per
project); a future implementation can swap in HashiCorp Vault / AWS
SM / Doppler without touching call sites.

Why a class with sync methods (not coroutines):
- The encryption is purely in-process (no subprocess, no socket).
  Wrapping it in asyncio would only add ceremony.
- ``threading.Lock`` per ``project_key`` serializes concurrent
  callers; ``asyncio`` callers can wrap with
  ``asyncio.to_thread(store.put, ...)``.

The interface deliberately exposes plain ``str`` values rather than
typed wrappers: callers (Agent Bridge, MCP server registration) all
want strings; over-typing here would force every caller through a
cast. Validation of *which* secrets are allowed (name regex, value
length, etc.) is a separate concern and out of scope for this MVP.
"""
from __future__ import annotations

import abc
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

logger = logging.getLogger(__name__)


# Public errors — callers can ``except SecretStoreError`` to catch
# anything the store raises and branch on the specific subclass.


class SecretStoreError(Exception):
    """Base class for all SecretStore failures."""


class SecretNotFound(SecretStoreError):
    """Raised by ``get`` / ``delete`` when the requested secret does not exist."""


class AuthenticationError(SecretStoreError):
    """Raised when the store cannot decrypt the file (wrong passphrase, corrupt file, etc.)."""


class ConfigurationError(SecretStoreError):
    """Raised when the store is mis-configured (no passphrase resolver available)."""


# -- interface --------------------------------------------------------------


class SecretStore(abc.ABC):
    """Project-scoped key-value store for sensitive strings.

    Implementations must be safe to call from multiple threads for
    distinct ``project_key`` values; concurrency on a single
    ``project_key`` is the implementation's responsibility.
    """

    @abc.abstractmethod
    def get(self, project_key: str, name: str) -> str | None:
        """Return the secret value, or ``None`` if the project has the
        secret-store file but the name is not in it.

        Raises ``SecretNotFound`` when the project has no secret-store
        file at all (distinct from 'file exists, name absent')."""

    @abc.abstractmethod
    def put(self, project_key: str, name: str, value: str) -> None:
        """Insert or overwrite the secret. Idempotent."""

    @abc.abstractmethod
    def list(self, project_key: str) -> list[str]:
        """Return the sorted names of all secrets for this project."""

    @abc.abstractmethod
    def delete(self, project_key: str, name: str) -> None:
        """Remove the secret. Raises ``SecretNotFound`` if absent."""


# -- helpers ----------------------------------------------------------------


# Anything outside [A-Za-z0-9_-] becomes '_'. We deliberately *don't*
# use percent-encoding here — secrets-files are 1:1 with project_keys
# and there are only a handful of legitimate characters in our key
# scheme (':', '/', '@', '.'), so escaping them all to '_' is fine and
# reversible-enough for debugging via ``ls``.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _safe_filename(project_key: str) -> str:
    safe = _UNSAFE_CHARS.sub("_", project_key)
    # Reject empty / all-unsafe inputs up front; these would produce
    # '.age' which collides with hidden files and would silently overwrite.
    if not safe or safe in {".", ".."}:
        raise ValueError(f"project_key produces unsafe filename: {project_key!r}")
    return f"{safe}.age"


def resolve_passphrase() -> str:
    """Return the store's symmetric passphrase.

    Resolution order:
    1. ``COCKPIT_SECRETS_PASSPHRASE`` environment variable.
    2. ``keyring.get_password("claude-cockpit", "secrets-passphrase")``
       (Linux: GNOME Keyring / KWallet via ``secretstorage``; macOS:
       Keychain; Windows: Windows Credential Locker).

    Raises ``ConfigurationError`` if neither source yields a passphrase.
    """
    pw = os.environ.get("COCKPIT_SECRETS_PASSPHRASE")
    if pw:
        return pw
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        raise ConfigurationError(
            "No passphrase: set COCKPIT_SECRETS_PASSPHRASE or `pip install keyring` "
            "to read it from the OS keyring."
        )
    pw = keyring.get_password("claude-cockpit", "secrets-passphrase")
    if pw is None:
        raise ConfigurationError(
            "No passphrase: set COCKPIT_SECRETS_PASSPHRASE or store the passphrase "
            'under "claude-cockpit/secrets-passphrase" in the OS keyring.'
        )
    return pw


# -- symmetric crypto -------------------------------------------------------
#
# On-disk format (binary, little extension `.age`):
#
#   offset  size  field
#   ------  ----  -----
#   0       6     magic bytes b"AGE1\x00\x00" — identifies the file as ours
#                 (NOT the official age format — see docs/features/secrets.md
#                 for why we don't reuse the `age` CLI's symmetric mode).
#   6       1     scrypt log2(N) — 1 byte, value 16..22; 20 is the default
#                 (matches Filippo Valsorda's recommended cost).
#   7       32    salt (random per file)
#   39      12    nonce (random per file)
#   51      *     ChaCha20-Poly1305 ciphertext (overhead = 16 bytes)
#
# AAD bound to the ciphertext = magic + scrypt_cost + salt + nonce, so
# swapping any of those by an attacker causes the MAC to fail. The
# magic also acts as a "version" marker; bumping it lets us detect
# re-keying scenarios later.

_MAGIC = b"AGE1\x00\x00"
_MAGIC_LEN = len(_MAGIC)
_SCRYPT_COST_LEN = 1
_SALT_LEN = 32
_NONCE_LEN = 12
_HEADER_LEN = _MAGIC_LEN + _SCRYPT_COST_LEN + _SALT_LEN + _NONCE_LEN  # 51
_DEFAULT_SCRYPT_LOG_N = 20  # ~1s derivation on a modern x86 server; tune via env later
# Accepted cost range. Mirrors the bounds `_decrypt` enforces on the header
# byte, so anything we agree to write is also something we agree to read.
_MIN_SCRYPT_LOG_N = 14
_MAX_SCRYPT_LOG_N = 22


def _derive_key(passphrase: str, salt: bytes, log_n: int) -> bytes:
    """Derive a 32-byte ChaCha20 key from the passphrase via scrypt."""
    kdf = Scrypt(salt=salt, length=32, n=1 << log_n, r=8, p=1)
    return kdf.derive(passphrase.encode("utf-8"))


def _encrypt(
    plaintext: bytes, passphrase: str, log_n: int = _DEFAULT_SCRYPT_LOG_N
) -> bytes:
    """Encrypt + frame ``plaintext`` under ``passphrase``.

    ``log_n`` is the scrypt cost. It is written into the header, and
    ``_decrypt`` reads it back from there, so files encrypted at different
    costs stay mutually readable — lowering it only weakens the files written
    while it is lowered. Production leaves it at the default; the test suite
    drops it (see ``AGESecretStore(scrypt_log_n=...)``) because at cost 20 a
    single put costs ~2-4.5s, which put tests/test_secrets_store.py past a
    300s timeout.
    """
    if not _MIN_SCRYPT_LOG_N <= log_n <= _MAX_SCRYPT_LOG_N:
        raise ValueError(
            f"scrypt log_n must be {_MIN_SCRYPT_LOG_N}..{_MAX_SCRYPT_LOG_N}, got {log_n}"
        )
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(passphrase, salt, log_n)
    aad = _MAGIC + bytes([log_n]) + salt + nonce
    cipher = ChaCha20Poly1305(key)
    ct = cipher.encrypt(nonce, plaintext, aad)
    return _MAGIC + bytes([log_n]) + salt + nonce + ct


def _decrypt(blob: bytes, passphrase: str) -> bytes:
    """Decrypt a framed blob. Raises ``AuthenticationError`` on any failure."""
    if len(blob) < _HEADER_LEN or blob[:_MAGIC_LEN] != _MAGIC:
        raise AuthenticationError("file is not an AGESecretStore blob")
    log_n = blob[_MAGIC_LEN]
    if not _MIN_SCRYPT_LOG_N <= log_n <= _MAX_SCRYPT_LOG_N:
        raise AuthenticationError(f"invalid scrypt cost in file: {log_n}")
    salt = blob[_MAGIC_LEN + _SCRYPT_COST_LEN : _MAGIC_LEN + _SCRYPT_COST_LEN + _SALT_LEN]
    nonce = blob[_MAGIC_LEN + _SCRYPT_COST_LEN + _SALT_LEN : _HEADER_LEN]
    ct = blob[_HEADER_LEN:]
    key = _derive_key(passphrase, salt, log_n)
    aad = blob[:_HEADER_LEN]
    cipher = ChaCha20Poly1305(key)
    try:
        return cipher.decrypt(nonce, ct, aad)
    except Exception as e:  # cryptography.exceptions.InvalidTag, etc.
        raise AuthenticationError(f"decryption failed (wrong passphrase or corrupt file): {e}") from e


# -- MVP implementation -----------------------------------------------------


class AGESecretStore(SecretStore):
    """File-backed secret store using scrypt + ChaCha20-Poly1305 encryption.

    One file per ``project_key`` under ``root``, named after the
    sanitized key with a ``.age`` suffix. The plaintext inside the
    file is a JSON object ``{name: value}``; this lets a future
    iteration add metadata (created_at, version, …) without breaking
    the on-disk format.

    File permissions are forced to ``0o600`` on every write. The store
    uses atomic-rename so a crashed write never leaves a half-written
    file readable to other processes.

    See module docstring for the on-disk format and why we don't use
    the upstream ``age`` CLI's symmetric mode (it requires a TTY,
    which server contexts don't have).
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        passphrase: str | None = None,
        passphrase_resolver: Any = None,
        scrypt_log_n: int = _DEFAULT_SCRYPT_LOG_N,
    ) -> None:
        """Construct the store.

        ``root`` defaults to ``~/.claude-registry/secrets`` so the
        canonical install puts secrets alongside the registry DB.

        ``passphrase`` wins when supplied directly (used by tests and
        by callers that have already resolved a passphrase). When
        omitted, ``passphrase_resolver`` is called on first use; if
        neither is given, ``resolve_passphrase()`` is used (env-var +
        keyring fallback).

        ``scrypt_log_n`` is the KDF cost used for *writes*; reads always honour
        the cost recorded in each file's header. Leave it at the default in
        production — only the test suite lowers it, to keep a put under a few
        milliseconds instead of seconds.
        """
        if root is None:
            root = Path.home() / ".claude-registry" / "secrets"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._scrypt_log_n = scrypt_log_n
        # Lock per project_key so concurrent callers don't interleave
        # read-modify-write on the JSON body. threading.Lock (not
        # asyncio.Lock) because the store is fully sync; async callers
        # can wrap with `asyncio.to_thread(store.put, ...)` if they
        # need offloading.
        self._locks: dict[str, threading.Lock] = {}
        self._explicit_passphrase = passphrase
        self._passphrase_resolver = passphrase_resolver

    def _get_passphrase(self) -> str:
        if self._explicit_passphrase is not None:
            return self._explicit_passphrase
        if self._passphrase_resolver is not None:
            return self._passphrase_resolver()
        return resolve_passphrase()

    def _lock_for(self, project_key: str) -> threading.Lock:
        lock = self._locks.get(project_key)
        if lock is None:
            lock = threading.Lock()
            self._locks[project_key] = lock
        return lock

    def _path_for(self, project_key: str) -> Path:
        return self.root / _safe_filename(project_key)

    # -- core I/O ---------------------------------------------------------

    def _read_payload(self, project_key: str) -> dict[str, str]:
        """Decrypt the project's file and return its JSON payload.

        Raises ``SecretNotFound`` when the file does not exist, and
        ``AuthenticationError`` for a bad passphrase or corrupt file.
        """
        path = self._path_for(project_key)
        if not path.exists():
            raise SecretNotFound(f"no secrets file for project_key={project_key!r}")
        blob = path.read_bytes()
        plaintext = _decrypt(blob, self._get_passphrase())
        try:
            data = json.loads(plaintext.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SecretStoreError(
                f"secrets file for {project_key!r} is not valid JSON after decrypt: {e}"
            ) from e
        if not isinstance(data, dict):
            raise SecretStoreError(
                f"secrets file for {project_key!r} has unexpected top-level type "
                f"{type(data).__name__}; expected object"
            )
        return {str(k): str(v) for k, v in data.items()}

    def _write_payload(self, project_key: str, payload: dict[str, str]) -> None:
        """Encrypt ``payload`` and write it atomically to the project's file."""
        path = self._path_for(project_key)
        plaintext = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        blob = _encrypt(plaintext, self._get_passphrase(), self._scrypt_log_n)

        # Atomic write: encrypt -> tempfile in same dir -> chmod 600
        # -> os.replace. Same-dir so os.replace is atomic on the same FS.
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=".tmp-", suffix=".age", dir=str(self.root)
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
        except Exception:
            # Best-effort cleanup so we never leak tempfiles.
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise

    # -- public API (sync) ------------------------------------------------

    def get(self, project_key: str, name: str) -> str | None:
        with self._lock_for(project_key):
            payload = self._read_payload(project_key)
            return payload.get(name)

    def put(self, project_key: str, name: str, value: str) -> None:
        with self._lock_for(project_key):
            # Read-modify-write under the lock so concurrent puts don't
            # overwrite each other.
            try:
                payload = self._read_payload(project_key)
            except SecretNotFound:
                payload = {}
            payload[name] = value
            self._write_payload(project_key, payload)

    def list(self, project_key: str) -> list[str]:
        # No lock needed: list is a read; a concurrent put may finish
        # mid-decrypt and we either see the old payload or the new one,
        # both well-formed (atomic write guarantees that).
        try:
            payload = self._read_payload(project_key)
        except SecretNotFound:
            return []
        return sorted(payload.keys())

    def delete(self, project_key: str, name: str) -> None:
        with self._lock_for(project_key):
            try:
                payload = self._read_payload(project_key)
            except SecretNotFound:
                raise SecretNotFound(
                    f"no secret {name!r} for project_key={project_key!r}"
                ) from None
            if name not in payload:
                raise SecretNotFound(
                    f"no secret {name!r} for project_key={project_key!r}"
                )
            del payload[name]
            if payload:
                self._write_payload(project_key, payload)
            else:
                # Empty payload — drop the file rather than keep
                # an encrypted empty JSON around.
                try:
                    self._path_for(project_key).unlink()
                except FileNotFoundError:
                    pass