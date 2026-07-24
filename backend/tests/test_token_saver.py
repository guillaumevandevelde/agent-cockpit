"""Tests for the worktree-local RTK hook installer in
``app.kanban.token_saver``.

This is the production surface of the per-lane opt-in acceptance
criterion (§1 #1) and the fail-open contract (§1 #3): every helper step
must degrade to a no-op + audit comment rather than raise. The helper
writes to a *worktree* filesystem path, never the user's ``~/.claude/``
directory, so tests use ``tmp_path`` for full isolation.

Spec: docs/superpowers/specs/2026-07-24-token-saver-integration-design.md §5.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import pytest_asyncio

from app.kanban import token_saver
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanColumn, KanbanMeta
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _seed_column(project_key: str, name: str,
                       token_saver_enabled: int = 0) -> str:
    """Create a column row and return its id."""
    async with KanbanSessionLocal() as s:
        col = KanbanColumn(
            id=f"col-{name}", project_key=project_key, name=name,
            rank="0000", token_saver_enabled=token_saver_enabled,
        )
        s.add(col)
        await s.commit()
    return col.id


async def _seed_kill_switch(project_key: str, enabled: bool) -> None:
    """Insert the per-project kill-switch row in KanbanMeta."""
    async with KanbanSessionLocal() as s:
        s.add(KanbanMeta(
            key=f"token_saver:{project_key}",
            value="1" if enabled else "0",
        ))
        await s.commit()


def _write_fake_rtk(bin_dir: Path, version: str = "0.43.0") -> Path:
    """Drop a stub ``rtk`` binary that reports the requested version.

    The stub exits 0 and prints ``rtk <version>`` on stdout so the
    version-parse step in the helper sees a real-looking response.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / "rtk"
    p.write_text(
        "#!/usr/bin/env bash\n"
        f"if [ \"$1\" = \"--version\" ]; then echo 'rtk {version}'; exit 0; fi\n"
        "echo 'fake rtk invoked'; exit 0\n"
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _read_settings(worktree: Path) -> dict:
    """Read and JSON-parse the worktree's ``.claude/settings.json``."""
    path = worktree / ".claude" / "settings.json"
    return json.loads(path.read_text())


def _write_settings(worktree: Path, payload: dict) -> None:
    path = worktree / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


# --- Fail-open: nothing to do -------------------------------------------------


@pytest.mark.asyncio
async def test_inactive_when_per_lane_flag_off(tmp_path, monkeypatch):
    """Column flag off → status ``inactive``, reason ``per-lane flag off``,
    no filesystem writes, no activity comment."""
    await _seed_column("PROJ", "engineer", token_saver_enabled=0)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    async with KanbanSessionLocal() as s:
        status, reason = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "inactive"
    assert reason == "per-lane flag off"
    # No settings.json was written.
    assert not (worktree / ".claude" / "settings.json").exists()


@pytest.mark.asyncio
async def test_inactive_when_kill_switch_off(tmp_path, monkeypatch):
    """Column flag on, kill-switch off → ``inactive`` + ``board kill-switch off``."""
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=False)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    async with KanbanSessionLocal() as s:
        status, reason = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "inactive"
    assert reason == "board kill-switch off"


# --- Fail-open: RTK missing / wrong version ---------------------------------


@pytest.mark.asyncio
async def test_fail_open_when_rtk_binary_missing(tmp_path, monkeypatch):
    """No ``rtk`` on PATH and no cache directory → ``failed`` +
    ``rtk binary missing`` + no filesystem writes."""
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)
    monkeypatch.delenv("COCKPIT_RTK_BIN", raising=False)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(token_saver.shutil, "which", lambda _: None)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    async with KanbanSessionLocal() as s:
        status, reason = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "failed"
    assert "rtk binary missing" in reason
    assert not (worktree / ".claude" / "settings.json").exists()


@pytest.mark.asyncio
async def test_fail_open_when_rtk_version_wrong(tmp_path, monkeypatch):
    """RTK present but reports ``0.42.0`` → ``failed`` + version reason."""
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir, version="0.42.0")
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    async with KanbanSessionLocal() as s:
        status, reason = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "failed"
    assert "0.42.0" in reason
    assert "0.43.0" in reason
    assert not (worktree / ".claude" / "settings.json").exists()


# --- Fail-open: filesystem problems ------------------------------------------


@pytest.mark.asyncio
async def test_fail_open_when_worktree_missing(tmp_path, monkeypatch):
    """``worktree_path`` doesn't exist → ``failed`` + reason; no exception."""
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    worktree = tmp_path / "does-not-exist"  # not mkdir'd

    async with KanbanSessionLocal() as s:
        status, reason = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "failed"
    assert "worktree" in reason.lower()


@pytest.mark.asyncio
async def test_fail_open_when_settings_unwritable(tmp_path, monkeypatch):
    """``.claude/settings.json`` is a *directory* (unparseable) → ``failed``,
    no exception, dispatch continues."""
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    # Block writes by making .claude a directory of an unwritable kind.
    settings_path = worktree / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text("not json {{{ broken")

    async with KanbanSessionLocal() as s:
        status, reason = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "failed"
    # Original file untouched — atomic write semantics.
    assert settings_path.read_text() == "not json {{{ broken"


# --- Active branch: settings.json merge --------------------------------------


@pytest.mark.asyncio
async def test_active_branch_writes_hook_and_settings(tmp_path, monkeypatch):
    """Active branch writes the wrapper hook + settings.json and is
    idempotent on the second invocation."""
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    # Avoid any network or cache downloads during the test.
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    async with KanbanSessionLocal() as s:
        status, reason = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "active"
    assert "0.43.0" in reason

    # Wrapper hook script exists.
    hook = worktree / ".claude" / "hooks" / "rtk-cockpit-rewrite.sh"
    assert hook.is_file()
    assert hook.stat().st_mode & 0o111  # executable

    # settings.json carries the PreToolUse entry on Bash.
    settings = _read_settings(worktree)
    pretooluse = settings["hooks"]["PreToolUse"]
    bash_matchers = [e for e in pretooluse if e.get("matcher") == "Bash"]
    assert bash_matchers, f"no Bash PreToolUse entry: {pretooluse}"
    cmd = bash_matchers[0]["hooks"][0]["command"]
    assert "rtk-cockpit-rewrite.sh" in cmd


@pytest.mark.asyncio
async def test_existing_pre_tool_use_entries_preserved(tmp_path, monkeypatch):
    """An existing ``hooks.PreToolUse`` entry on a different matcher survives."""
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    _write_settings(worktree, {
        "hooks": {"PreToolUse": [
            {"matcher": "Read", "hooks": [
                {"type": "command", "command": "/usr/local/bin/read-guard"},
            ]},
        ]},
    })

    async with KanbanSessionLocal() as s:
        status, _ = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "active"

    settings = _read_settings(worktree)
    pretooluse = settings["hooks"]["PreToolUse"]
    # Both the original Read entry and the new Bash entry are present.
    matchers = [e.get("matcher") for e in pretooluse]
    assert "Read" in matchers
    assert "Bash" in matchers


@pytest.mark.asyncio
async def test_existing_permissions_preserved(tmp_path, monkeypatch):
    """``permissions.allow`` / ``permissions.deny`` survive verbatim."""
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    _write_settings(worktree, {
        "includeCoAuthoredBy": False,
        "permissions": {
            "allow": ["Read(*.py)"],
            "deny": ["Bash(rm:*)"],
        },
    })

    async with KanbanSessionLocal() as s:
        status, _ = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "active"

    settings = _read_settings(worktree)
    assert settings["includeCoAuthoredBy"] is False
    assert settings["permissions"]["allow"] == ["Read(*.py)"]
    assert settings["permissions"]["deny"] == ["Bash(rm:*)"]


@pytest.mark.asyncio
async def test_settings_without_hooks_key_gets_hooks_added(tmp_path, monkeypatch):
    """Existing settings.json without a ``hooks`` key grows one without
    disturbing the other keys."""
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    _write_settings(worktree, {"includeCoAuthoredBy": False})

    async with KanbanSessionLocal() as s:
        status, _ = await token_saver.maybe_install(
            session=s, card_id="card1", project_key="PROJ",
            column_name="engineer",
            worktree_path=str(worktree), repo_path=str(tmp_path),
        )
    assert status == "active"
    settings = _read_settings(worktree)
    assert "hooks" in settings
    assert settings["includeCoAuthoredBy"] is False


@pytest.mark.asyncio
async def test_active_branch_is_idempotent(tmp_path, monkeypatch):
    """Two consecutive calls do not double-merge the Bash PreToolUse entry."""
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    for _ in range(2):
        async with KanbanSessionLocal() as s:
            status, _ = await token_saver.maybe_install(
                session=s, card_id="card1", project_key="PROJ",
                column_name="engineer",
                worktree_path=str(worktree), repo_path=str(tmp_path),
            )
        assert status == "active"

    settings = _read_settings(worktree)
    bash_matchers = [
        e for e in settings["hooks"]["PreToolUse"]
        if e.get("matcher") == "Bash"
    ]
    # Idempotent: only one Bash entry, even though maybe_install ran twice.
    assert len(bash_matchers) == 1


# --- Helper: board kill-switch read ------------------------------------------


@pytest.mark.asyncio
async def test_is_board_enabled_returns_false_when_meta_row_absent():
    """No ``token_saver:<key>`` row → ``False``."""
    async with KanbanSessionLocal() as s:
        assert await token_saver.is_board_enabled(s, "PROJ") is False


@pytest.mark.asyncio
async def test_is_board_enabled_returns_true_when_meta_value_is_1():
    """``token_saver:PROJ = "1"`` → ``True``."""
    await _seed_kill_switch("PROJ", enabled=True)
    async with KanbanSessionLocal() as s:
        assert await token_saver.is_board_enabled(s, "PROJ") is True


@pytest.mark.asyncio
async def test_is_board_enabled_returns_false_when_meta_value_is_0():
    """``token_saver:PROJ = "0"`` → ``False`` (anything other than ``"1"``)."""
    await _seed_kill_switch("PROJ", enabled=False)
    async with KanbanSessionLocal() as s:
        assert await token_saver.is_board_enabled(s, "PROJ") is False


@pytest.mark.asyncio
async def test_set_board_enabled_round_trips():
    """``set_board_enabled(..., True)`` writes ``"1"``; ``False`` writes ``"0"``."""
    async with KanbanSessionLocal() as s:
        await token_saver.set_board_enabled(s, "PROJ", True)
        assert await token_saver.is_board_enabled(s, "PROJ") is True
        await token_saver.set_board_enabled(s, "PROJ", False)
        assert await token_saver.is_board_enabled(s, "PROJ") is False


# --- Activity-feed dedup -----------------------------------------------------


@pytest.mark.asyncio
async def test_post_note_dedups_within_60s(tmp_path, monkeypatch):
    """Two ``post_note`` calls within 60s on the same card → only one lands.

    Imported inside the test so the test file fails to collect if
    ``post_note`` isn't part of the helper's public surface yet.
    """
    async with KanbanSessionLocal() as s:
        from app.kanban.operations import apply_operation
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="PROJ", entity_id=None,
            payload={"title": "t", "column": "Backlog"},
        )

        await token_saver.post_note(s, cid, "Token saver activated: RTK 0.43.0")
        # Force the dedup window to a positive value so the second call
        # (well within the window) is suppressed. A window of 0 would
        # never match the ``age < 0`` gate, so dedup never triggers.
        monkeypatch.setattr(token_saver, "_DEDUP_WINDOW_SECONDS", 3600)
        await token_saver.post_note(
            s, cid, "Token saver activated: RTK 0.43.0",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        from sqlalchemy import select, func
        from app.kanban.models import KanbanOp
        n = (await s.execute(
            select(func.count()).select_from(KanbanOp)
            .where(KanbanOp.entity_id == cid)
            .where(KanbanOp.op_type == "comment")
            .where(KanbanOp.payload["text"].as_string().like(
                "%Token saver%",
            ))
        )).scalar_one()
    assert n == 1
