"""Tests for `AGESecretStore`.

These tests exercise the store end-to-end against the real on-disk
format. A temporary root is injected per test so production state is
never touched.

Test passphrase strategy:
- the store is constructed with `passphrase=...` directly, bypassing the
  env/keyring resolver (so these tests don't need keyring installed).
- the API tests (`test_api_secrets.py`) cover the resolver path.
"""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from app.services.secrets_store import (
    AGESecretStore,
    SecretNotFound,
    SecretStoreError,
)

PASSPHRASE = "test-passphrase-1234"


def _make_store(tmp_path: Path) -> AGESecretStore:
    return AGESecretStore(root=tmp_path, passphrase=PASSPHRASE)


# -- happy path -------------------------------------------------------------


def test_roundtrip_put_get(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.put("git:github.com/foo/bar", "MINIMAX_API_KEY", "sk-secret-value")
    assert store.get("git:github.com/foo/bar", "MINIMAX_API_KEY") == "sk-secret-value"


def test_put_overwrites_existing_value(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    project_key = "git:github.com/foo/bar"
    store.put(project_key, "TOK", "v1")
    store.put(project_key, "TOK", "v2")
    assert store.get(project_key, "TOK") == "v2"


def test_list_returns_all_names(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    project_key = "git:github.com/foo/bar"
    store.put(project_key, "B_KEY", "b")
    store.put(project_key, "A_KEY", "a")
    store.put(project_key, "C_KEY", "c")
    assert store.list(project_key) == ["A_KEY", "B_KEY", "C_KEY"]


def test_list_returns_empty_for_untouched_project(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.list("git:github.com/foo/bar") == []


def test_delete_removes_secret(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    project_key = "git:github.com/foo/bar"
    store.put(project_key, "DOOMED", "x")
    store.delete(project_key, "DOOMED")
    # Deleting the last secret removes the file entirely (see impl); both
    # `get` and `list` should report absence.
    with pytest.raises(SecretNotFound):
        store.get(project_key, "DOOMED")
    assert store.list(project_key) == []


def test_delete_one_of_many_keeps_file(tmp_path: Path) -> None:
    """When other secrets remain, the file persists and `get` on the deleted
    name returns None (file exists, name absent — different code path)."""
    store = _make_store(tmp_path)
    project_key = "git:github.com/foo/bar"
    store.put(project_key, "KEEP", "yes")
    store.put(project_key, "DROP", "no")
    store.delete(project_key, "DROP")
    assert store.get(project_key, "DROP") is None
    assert store.get(project_key, "KEEP") == "yes"


def test_delete_missing_secret_raises(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(SecretNotFound):
        store.delete("git:github.com/foo/bar", "NEVER_WAS")


# -- error cases ------------------------------------------------------------


def test_get_missing_secret_in_existing_file_returns_none(tmp_path: Path) -> None:
    """A file exists for the project but the requested name is absent."""
    store = _make_store(tmp_path)
    project_key = "git:github.com/foo/bar"
    store.put(project_key, "OTHER", "y")
    assert store.get(project_key, "MINIMAX_API_KEY") is None


def test_get_for_brand_new_project_raises(tmp_path: Path) -> None:
    """No file at all for this project — different from 'file exists, name missing'."""
    store = _make_store(tmp_path)
    with pytest.raises(SecretNotFound):
        store.get("git:github.com/never/seen", "ANY")


def test_wrong_passphrase_raises(tmp_path: Path) -> None:
    """A file encrypted with passphrase A cannot be read with passphrase B."""
    writer = AGESecretStore(root=tmp_path, passphrase="passphrase-A")
    writer.put("git:github.com/foo/bar", "TOK", "v")
    # Force-close the file handle and wait for any async work (none here, but
    # be explicit about the sequencing).
    reader = AGESecretStore(root=tmp_path, passphrase="passphrase-B")
    with pytest.raises(SecretStoreError):
        reader.get("git:github.com/foo/bar", "TOK")


# -- filesystem invariants --------------------------------------------------


def test_file_mode_is_0600(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.put("git:github.com/foo/bar", "TOK", "v")
    # find the age file written under tmp_path
    age_files = list(tmp_path.glob("*.age"))
    assert len(age_files) == 1
    mode = age_files[0].stat().st_mode
    assert stat.S_IMODE(mode) == 0o600, f"expected 0o600, got {oct(stat.S_IMODE(mode))}"


def test_project_key_with_path_separators_creates_no_subdir(tmp_path: Path) -> None:
    """`git:github.com/foo/bar` must land as one flat file, not nested dirs."""
    store = _make_store(tmp_path)
    store.put("git:github.com/foo/bar", "TOK", "v")
    # No subdirectories created under root.
    subdirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert subdirs == [], f"unexpected subdirs: {subdirs}"
    # All files are flat .age files at the root.
    files = list(tmp_path.iterdir())
    assert all(f.suffix == ".age" for f in files)


def test_separate_projects_get_separate_files(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.put("git:github.com/foo/bar", "TOK", "value-for-bar")
    store.put("git:github.com/foo/baz", "TOK", "value-for-baz")
    assert store.get("git:github.com/foo/bar", "TOK") == "value-for-bar"
    assert store.get("git:github.com/foo/baz", "TOK") == "value-for-baz"


# -- concurrency ------------------------------------------------------------


def test_concurrent_writes_to_same_project_key(tmp_path: Path) -> None:
    """20 concurrent puts on the same project_key must all be visible afterwards.

    Without per-project locking this can either lose writes (last writer
    wins on read-modify-write) or leave a corrupt half-written file.
    """
    store = AGESecretStore(root=tmp_path, passphrase=PASSPHRASE)
    project_key = "git:github.com/foo/bar"
    n = 20

    import concurrent.futures

    def write(i: int) -> None:
        store.put(project_key, f"KEY_{i:02d}", f"v{i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(write, range(n)))

    names = store.list(project_key)
    assert len(names) == n
    for i in range(n):
        assert store.get(project_key, f"KEY_{i:02d}") == f"v{i}"


# -- atomic write -----------------------------------------------------------


def test_atomic_write_failure_leaves_no_tempfile(tmp_path: Path) -> None:
    """If the final os.replace fails, no *.tmp file should be left behind."""
    store = _make_store(tmp_path)
    project_key = "git:github.com/foo/bar"
    store.put(project_key, "OTHER", "y")  # ensures file exists

    import unittest.mock as mock

    def boom(src: str, dst: str) -> None:
        raise OSError("simulated rename failure")

    with mock.patch("app.services.secrets_store.os.replace", side_effect=boom):
        with pytest.raises(OSError):
            store.put(project_key, "TOK", "v")

    # No leftover .tmp-* files in the secrets root.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == [], f"leftover temp files: {leftovers}"


# -- encoding sanity --------------------------------------------------------


def test_payload_is_json_with_name_value_keys(tmp_path: Path) -> None:
    """The plaintext inside the file is JSON; this matters for
    forward-compatibility (we may add metadata fields later) and lets
    a human inspect the file after decrypting it."""
    store = _make_store(tmp_path)
    project_key = "git:github.com/foo/bar"
    store.put(project_key, "TOK", "v")

    age_file = next(tmp_path.glob("*.age"))
    from app.services.secrets_store import _decrypt

    plaintext = _decrypt(age_file.read_bytes(), PASSPHRASE)
    parsed = json.loads(plaintext)
    assert parsed == {"TOK": "v"}


def test_file_has_our_magic_header(tmp_path: Path) -> None:
    """Sanity check on the magic bytes — catches accidental format breakage."""
    store = _make_store(tmp_path)
    store.put("git:github.com/foo/bar", "TOK", "v")
    age_file = next(tmp_path.glob("*.age"))
    header = age_file.read_bytes()[:6]
    assert header == b"AGE1\x00\x00"