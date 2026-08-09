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
import stat
import subprocess
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


def _seed_column_sync(project_key: str, name: str,
                      token_saver_enabled: int = 0) -> str:
    """Sync wrapper for the sync-bridge test — uses asyncio.run so
    the test itself stays sync (the autouse async fixture is the only
    async surface this test touches)."""
    import asyncio
    return asyncio.run(_seed_column(project_key, name, token_saver_enabled))


async def _seed_kill_switch(project_key: str, enabled: bool) -> None:
    """Insert the per-project kill-switch row in KanbanMeta."""
    async with KanbanSessionLocal() as s:
        s.add(KanbanMeta(
            key=f"token_saver:{project_key}",
            value="1" if enabled else "0",
        ))
        await s.commit()


def _seed_kill_switch_sync(project_key: str, enabled: bool) -> None:
    """Sync wrapper for the sync-bridge test."""
    import asyncio
    asyncio.run(_seed_kill_switch(project_key, enabled))


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
    """Read and JSON-parse the worktree's ``.claude/settings.local.json``.

    NOTE: kept the historical name so test bodies stay readable; the
    helper points at ``settings.local.json`` because that's the file the
    helper actually patches.
    """
    path = worktree / ".claude" / "settings.local.json"
    return json.loads(path.read_text())


def _read_local_settings(worktree: Path) -> dict:
    """Alias for :func:`_read_settings` — clarifies intent at call sites."""
    return _read_settings(worktree)


def _write_settings(worktree: Path, payload: dict) -> None:
    """Write ``.claude/settings.local.json`` (the gitignored variant)."""
    path = worktree / ".claude" / "settings.local.json"
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
    assert not (worktree / ".claude" / "settings.local.json").exists()


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
    assert not (worktree / ".claude" / "settings.local.json").exists()


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
    """``.claude/settings.local.json`` is unparseable → ``failed``,
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
    # Pre-populate with unparseable content so the JSON-load step raises.
    settings_path = worktree / ".claude" / "settings.local.json"
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


# --- Active branch: settings.local.json merge + cached wrapper ---------------
#
# The wrapper hook lives in the RTK cache (NOT in the worktree) and the
# hook-config is written to ``.claude/settings.local.json`` (gitignored by
# the repo's own ``.gitignore``). Both moves keep the dispatch worktree's
# ``git status`` clean so the ship-gate accepts the agent's commits —
# the broken-state symptom this test exists to prevent: a previous
# version wrote a tracked ``.claude/settings.json`` and a new
# ``.claude/hooks/`` directory, every ship aborted at the first
# uncommitted-changes guard (kanban card c31333bf… reviewer-gate, 4th
# iteration). The ignore pattern used to live in Claude Code's host-global
# gitignore; that broke the guarantee on a fresh host, so the rule was
# moved into the repo (kanban card f760c505… /
# docs/cockpit/token-saver-mechanismen-decision.md §8).


@pytest.mark.asyncio
async def test_active_branch_writes_hook_and_settings(tmp_path, monkeypatch):
    """Active branch writes the wrapper into the cache and patches
    ``.claude/settings.local.json`` (not the tracked settings.json), and
    is idempotent on the second invocation."""
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

    # Wrapper hook script lives in the RTK cache, NOT the worktree.
    wrapper = token_saver.RTK_CACHE_ROOT / token_saver.RTK_PINNED_VERSION \
        / "hooks" / "rtk-cockpit-rewrite-wrapper.sh"
    assert wrapper.is_file()
    assert wrapper.stat().st_mode & 0o111  # executable

    # No hook script in the worktree (otherwise it would pollute git status).
    assert not (worktree / ".claude" / "hooks").exists(), (
        "wrapper hook leaked into worktree — ship gate would abort on "
        "untracked .claude/hooks/"
    )

    # settings.local.json carries the PreToolUse entry on Bash.
    settings = _read_local_settings(worktree)
    pretooluse = settings["hooks"]["PreToolUse"]
    bash_matchers = [e for e in pretooluse if e.get("matcher") == "Bash"]
    assert bash_matchers, f"no Bash PreToolUse entry: {pretooluse}"
    cmd = bash_matchers[0]["hooks"][0]["command"]
    assert str(wrapper) in cmd

    # settings.json (the tracked file) is NOT touched.
    assert not (worktree / ".claude" / "settings.json").exists()


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


# --- Clean-git-status contract ------------------------------------------------
#
# The previous iteration of the helper wrote the wrapper to
# ``<worktree>/.claude/hooks/`` and patched ``<worktree>/.claude/settings.json``
# (a TRACKED file). On every dispatch that fired the installer the worktree
# ended up with a modified tracked file + an untracked ``.claude/hooks/``
# directory; the ship-gate aborted on the first ``git status --porcelain``
# check, and the prescribed recovery (``git add -A && git commit``) committed
# the hook + the patched settings.json to master, which made RTK active
# board-wide from the next dispatch on, with the kill-switch unable to undo
# it. The fix: the wrapper lives in the RTK cache (outside the worktree),
# the hook-config goes to ``settings.local.json`` (gitignored by the
# repo's own ``.gitignore``), and ``git status`` stays clean. These tests
# pin that contract against a real git worktree, not ``tmp_path`` (where
# ``git status`` has nothing to look at). The ignore rule was moved out
# of Claude Code's host-global default into the repo so the guarantee
# holds on a fresh host (kanban card f760c505…); the new
# ``test_repo_gitignore_covers_settings_local_json`` test pins that
# invariant against the real repo.


def _seed_repo_local_gitignore(repo, *, pattern: str) -> None:
    """Seed the *test repo's* own ``.gitignore`` with ``pattern``.

    The token-saver installer writes ``.claude/settings.local.json`` and the
    clean-worktree guarantee hinges on that path being gitignored. The ship
    lands on any host when the repo's own ``.gitignore`` carries the pattern
    (kanban card f760c505… — moved out of Claude Code's host-global default
    rule into the repo to drop the host dependency). The test repo is a
    fresh ``git init`` and inherits no ignore rules, so this helper plants
    the same line the real repo has, before the install runs.
    """
    (repo / ".gitignore").write_text(pattern)


def _real_repo_root() -> Path:
    """Resolve the git toplevel of the test's working tree.

    Lets the host-independent guarantee test read the real repo's
    ``.gitignore`` from CI, a fresh checkout, and a developer machine —
    any path that ships the same tree.
    """
    import subprocess

    root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(Path(__file__).resolve().parent),
        text=True,
    ).strip()
    return Path(root)


def test_repo_gitignore_covers_settings_local_json():
    """The repo's own ``.gitignore`` keeps ``.claude/settings.local.json``
    out of ``git status``.

    Host-independent guarantee: previously this rule lived only in Claude
    Code's default global gitignore (``~/.config/git/ignore``), so the
    clean-worktree contract held on a developer's box but broke on a fresh
    machine, in CI, or in a container — the dispatch install would then
    leave a ``?? .claude/`` in every worktree and the ship-gate would abort
    from the first second. Pin the rule in the repo's own ``.gitignore``
    so the contract travels with the codebase. See kanban card f760c505…
    and docs/cockpit/token-saver-mechanismen-decision.md §8.
    """
    gitignore = _real_repo_root() / ".gitignore"
    assert gitignore.is_file(), f"repo .gitignore missing: {gitignore}"
    text = gitignore.read_text()
    # Anchor on the literal repo path (not the host-global ``**/`` glob) so
    # the assertion verifies the project-owned rule, not a coincidental
    # match against a similar Claude Code rule.
    assert ".claude/settings.local.json" in text, (
        "repo .gitignore must ignore .claude/settings.local.json — "
        "without this the token-saver install leaks untracked files into "
        "every dispatch worktree (kanban card f760c505…):\n"
        f"{text}"
    )


@pytest.mark.asyncio
async def test_active_branch_leaves_worktree_git_status_clean(
    tmp_path, monkeypatch,
):
    """Driving ``write_rtk_settings_into_worktree`` against a real git
    worktree leaves ``git status --porcelain`` empty.

    Kanban card c31333bf… reviewer-gate 4th iteration: a previous
    implementation wrote ``.claude/settings.json`` (TRACKED) and
    ``.claude/hooks/rtk-cockpit-rewrite.sh`` (UNTRACKED) into the dispatch
    worktree, polluting its ``git status`` from the first second of the
    session. The ship-gate aborts on any ``M`` or ``??`` line under
    ``.claude/``, so every shipping lane that ran the saver became
    unshippable, and the prescribed recovery committed the hook to master
    board-wide. This test pins the clean-status invariant.
    """
    import subprocess

    # Set up a real git repo + a single-branch worktree off it. ``tmp_path``
    # alone is not enough — ``git status`` only sees what ``git`` itself
    # sees, and ``tmp_path`` is not a repo.
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-b", "main", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        ["git", "-C", str(repo), "config", "user.name", "test"],
    ):
        subprocess.run(cmd, check=True, capture_output=True)
    _seed_repo_local_gitignore(
        repo, pattern=".claude/settings.local.json\n",
    )
    (repo / "README.md").write_text("hello\n")
    for cmd in (
        ["git", "-C", str(repo), "add", ".gitignore", "README.md"],
        ["git", "-C", str(repo), "commit", "-m", "init"],
        ["git", "-C", str(repo), "worktree", "add",
         "-b", "feature", str(wt), "main"],
    ):
        subprocess.run(cmd, check=True, capture_output=True)

    # Sandbox the cache root so the helper doesn't touch the real one.
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(token_saver, "RTK_CACHE_ROOT", cache_root)

    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    # Drive the public seam. ``write_rtk_settings_into_worktree`` is the
    # same function the dispatch path uses; the autouse async fixture
    # gives us a clean kanban DB even though the seam itself is sync.
    token_saver.write_rtk_settings_into_worktree(
        str(wt), str(bin_dir / "rtk"),
    )

    # The ship-gate's pre-flight: any non-empty porcelain line aborts.
    status_out = subprocess.run(
        ["git", "-C", str(wt), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert status_out.strip() == "", (
        f"worktree has uncommitted/untracked changes after token-saver "
        f"install — ship-gate would abort:\n{status_out}"
    )

    # And the wrapper hook actually exists at the documented cache path,
    # so the install was real (not a no-op pretending to be clean).
    wrapper = cache_root / token_saver.RTK_PINNED_VERSION \
        / "hooks" / "rtk-cockpit-rewrite-wrapper.sh"
    assert wrapper.is_file()
    assert wrapper.stat().st_mode & 0o111, "wrapper is not executable"


@pytest.mark.asyncio
async def test_active_branch_preserves_tracked_settings_json(
    tmp_path, monkeypatch,
):
    """A pre-existing ``.claude/settings.json`` (TRACKED) is left alone.

    The wrapper installer writes ONLY to ``settings.local.json``. Operators
    who already have hand-curated entries in ``settings.json`` keep them
    verbatim — the Bash PreToolUse hook lands in the local override
    alongside, where Claude Code reads it.
    """
    import subprocess

    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-b", "main", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        ["git", "-C", str(repo), "config", "user.name", "test"],
    ):
        subprocess.run(cmd, check=True, capture_output=True)
    _seed_repo_local_gitignore(
        repo, pattern=".claude/settings.local.json\n",
    )
    (repo / "README.md").write_text("hello\n")
    (repo / ".claude").mkdir()
    tracked = {
        "includeCoAuthoredBy": False,
        "permissions": {"allow": ["Read(*.py)"]},
    }
    (repo / ".claude" / "settings.json").write_text(
        json.dumps(tracked, indent=2),
    )
    for cmd in (
        ["git", "-C", str(repo), "add", "README.md", ".claude/settings.json"],
        ["git", "-C", str(repo), "commit", "-m", "init"],
        ["git", "-C", str(repo), "worktree", "add",
         "-b", "feature", str(wt), "main"],
    ):
        subprocess.run(cmd, check=True, capture_output=True)

    monkeypatch.setattr(token_saver, "RTK_CACHE_ROOT", tmp_path / "cache")
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    token_saver.write_rtk_settings_into_worktree(
        str(wt), str(bin_dir / "rtk"),
    )

    # Tracked settings.json is untouched.
    on_disk = json.loads(
        (wt / ".claude" / "settings.json").read_text(),
    )
    assert on_disk == tracked

    # Git status: only ``.claude/settings.local.json`` may appear, and it
    # must NOT appear (it is gitignored by the repo's own ``.gitignore``).
    status_out = subprocess.run(
        ["git", "-C", str(wt), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert status_out.strip() == "", (
        f"tracked settings.json was modified or settings.local.json leaked "
        f"through gitignore:\n{status_out}"
    )


# --- Fail-open op het hook-uitvoeringspad -----------------------------------
#
# Claude Code's PreToolUse hook contract: exit 2 BLOCKS the tool call and
# prompts the model to retry; any other non-zero exit is non-blocking
# (stderr shown, call proceeds). A broken RTK upstream must NOT exit 2,
# or the agent's Bash tool gets blocked and the dispatch stalls — the
# opposite of the card's fail-open-eis. The wrapper guards against
# this by ALWAYS exiting 0 (force-`exit 0` after the upstream call,
# stderr discarded). These tests pin that contract end-to-end by
# substituting the upstream ``rtk-rewrite.sh`` with one that exits
# non-zero, including the blocking exit 2.


def _write_upstream_rtk_rewrite(
    cache_root: Path, *, exit_code: int, stderr_msg: str = "",
) -> Path:
    """Drop a fake ``rtk-rewrite.sh`` that exits ``exit_code``.

    Mirrors the cache layout used by the real helper: same dir as the
    wrapper, named ``rtk-rewrite.sh`` (NOT ``-wrapper.sh``). The
    wrapper resolves it via ``$(dirname "$0")/rtk-rewrite.sh``.
    """
    upstream_dir = cache_root / token_saver.RTK_PINNED_VERSION / "hooks"
    upstream_dir.mkdir(parents=True, exist_ok=True)
    upstream = upstream_dir / "rtk-rewrite.sh"
    upstream.write_text(
        "#!/usr/bin/env bash\n"
        f"if [ -n '{stderr_msg}' ]; then echo '{stderr_msg}' >&2; fi\n"
        f"exit {exit_code}\n"
    )
    upstream.chmod(
        upstream.stat().st_mode
        | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
    )
    return upstream


def _run_wrapper(wrapper: Path, command: str) -> subprocess.CompletedProcess:
    """Drive the wrapper with a fake PreToolUse payload that names the
    given Bash command. Returns the subprocess result so the test can
    inspect exit code + stderr.
    """
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    return subprocess.run(
        [str(wrapper)],
        input=payload, capture_output=True, text=True, timeout=5,
        check=False,
    )


@pytest.mark.asyncio
async def test_fail_open_when_upstream_rtk_exits_1(
    tmp_path, monkeypatch,
):
    """Upstream ``rtk-rewrite.sh`` exits 1 → wrapper still exits 0.

    Claude Code treats exit 1 as a non-blocking error (stderr shown,
    call proceeds). The wrapper must preserve that semantic regardless
    of what RTK does internally — that's the fail-open contract on
    the hook-execution path (kaart ``c31333bf…`` acceptance #2:
    "Fail-open aangetoond met een test die de saver moedwillig laat
    falen en verifieert dat de originele inhoud doorgaat").
    """
    monkeypatch.setattr(token_saver, "RTK_CACHE_ROOT", tmp_path / "cache")
    _ensure_wrapper_for_test(tmp_path / "cache")
    _write_upstream_rtk_rewrite(tmp_path / "cache", exit_code=1)

    wrapper = (
        tmp_path / "cache" / token_saver.RTK_PINNED_VERSION
        / "hooks" / "rtk-cockpit-rewrite-wrapper.sh"
    )

    result = _run_wrapper(wrapper, "echo hello")
    assert result.returncode == 0, (
        f"wrapper exited {result.returncode} when upstream RTK exited 1 — "
        f"a non-zero exit would block the Bash call on Claude Code's "
        f"hook contract. stderr was: {result.stderr!r}"
    )


@pytest.mark.asyncio
async def test_fail_open_when_upstream_rtk_exits_2_blocking(
    tmp_path, monkeypatch,
):
    """Upstream ``rtk-rewrite.sh`` exits 2 → wrapper MUST still exit 0.

    Exit 2 in Claude Code's PreToolUse contract is the BLOCKING
    failure mode: the model is prompted to retry. If the wrapper
    propagates an upstream exit 2, a broken RTK silently turns into a
    dispatch-stall. The wrapper force-exits 0 to prevent that
    downgrade — even at the cost of swallowing the upstream's
    stderr noise.
    """
    monkeypatch.setattr(token_saver, "RTK_CACHE_ROOT", tmp_path / "cache")
    _ensure_wrapper_for_test(tmp_path / "cache")
    _write_upstream_rtk_rewrite(
        tmp_path / "cache", exit_code=2,
        stderr_msg="RTK upstream crashed",
    )

    wrapper = (
        tmp_path / "cache" / token_saver.RTK_PINNED_VERSION
        / "hooks" / "rtk-cockpit-rewrite-wrapper.sh"
    )

    result = _run_wrapper(wrapper, "echo hello")
    assert result.returncode == 0, (
        f"wrapper propagated upstream exit 2 — Claude Code would BLOCK "
        f"the agent's Bash call. stderr was: {result.stderr!r}"
    )


@pytest.mark.asyncio
async def test_fail_open_when_upstream_rtk_missing(
    tmp_path, monkeypatch,
):
    """Upstream ``rtk-rewrite.sh`` doesn't exist → wrapper exits 0.

    A fresh cache (after a partial install or a clean-rtk reset)
    has the wrapper but not yet the upstream. Without force-exit-0,
    bash's "No such file or directory" propagates as exit 127, which
    Claude Code treats as a non-blocking error — acceptable, but
    strictly exit 0 is the contract.
    """
    monkeypatch.setattr(token_saver, "RTK_CACHE_ROOT", tmp_path / "cache")
    _ensure_wrapper_for_test(tmp_path / "cache")
    # Deliberately do NOT write rtk-rewrite.sh.

    wrapper = (
        tmp_path / "cache" / token_saver.RTK_PINNED_VERSION
        / "hooks" / "rtk-cockpit-rewrite-wrapper.sh"
    )

    result = _run_wrapper(wrapper, "echo hello")
    assert result.returncode == 0, (
        f"wrapper exited {result.returncode} with no upstream present — "
        f"fresh-cache installs should fail-open. stderr: {result.stderr!r}"
    )


def _ensure_wrapper_for_test(cache_root: Path) -> Path:
    """Write the wrapper into the cache by running the helper. Used by
    the fail-open tests that monkeypatch ``RTK_CACHE_ROOT`` to a fresh
    tmp dir.
    """
    return token_saver._ensure_wrapper_in_cache()


# --- Instructie-isolatie -----------------------------------------------------
#
# Kaart ``c31333bf…`` acceptance #3: "Aangetoond dat instructie-inhoud
# (systeemprompt/persona/kaarttekst) niet gemuteerd wordt". The
# implementation is structured around this: the helper only patches
# ``.claude/settings.local.json`` (Bash PreToolUse config) and the
# wrapper-script lives in the cache. It never opens CLAUDE.md, the
# persona file, the kaarttekst file, or any other instruction-bearing
# path. This test pins that contract end-to-end by pre-populating
# those paths in the worktree, running the install, and asserting
# byte-identical content after.


@pytest.mark.asyncio
async def test_active_branch_does_not_mutate_instruction_content(
    tmp_path, monkeypatch,
):
    """Install writes ONLY to ``.claude/settings.local.json`` and the
    RTK cache. CLAUDE.md, persona files, and a stand-in card-text
    file are byte-identical before vs. after.

    The wrapper script lives in the cache (``<cache>/<version>/hooks/``),
    so it can never modify anything inside the worktree. The
    ``settings.local.json`` patch is bounded to a single Bash
    PreToolUse entry — no instructions of any kind flow through this
    path.
    """
    monkeypatch.setattr(token_saver, "RTK_CACHE_ROOT", tmp_path / "cache")
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(
        token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"),
    )
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    # Pre-populate the worktree with representative instruction-bearing
    # files. None of these are tracked by the helper — but the test
    # pins that they stay untouched anyway.
    worktree = tmp_path / "wt"
    worktree.mkdir()
    claude_md = worktree / "CLAUDE.md"
    persona_md = worktree / ".claude" / "persona.md"
    card_text = worktree / ".claude" / "card-text.md"
    card_text_before = (
        "# Card c31333bf…\n\nThis card text must survive install.\n"
    )
    claude_md.write_text(
        "# project rules\n\nDon't touch the prompt.\n",
    )
    persona_md.parent.mkdir(parents=True, exist_ok=True)
    persona_md.write_text(
        "You are an engineer. Always TDD.\n",
    )
    card_text.write_text(card_text_before)

    # Snapshot the bytes of every instruction-bearing path before install.
    snapshot_before = {
        p.relative_to(worktree): p.read_bytes()
        for p in (claude_md, persona_md, card_text)
    }

    token_saver.write_rtk_settings_into_worktree(
        str(worktree), str(bin_dir / "rtk"),
    )

    snapshot_after = {
        p.relative_to(worktree): p.read_bytes()
        for p in (claude_md, persona_md, card_text)
    }

    assert snapshot_after == snapshot_before, (
        "instruction content was mutated by the install.\n"
        "  diff: " + ", ".join(
            f"{k} changed" for k in snapshot_before
            if snapshot_before[k] != snapshot_after.get(k)
        )
    )

    # And the card-text-specific check — the file must not only be
    # byte-equal, it must still contain the original marker line.
    assert card_text_before in card_text.read_text()


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
        from sqlalchemy import func, select

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


# --- Dispatch-bridge: post_note on the activity feed -------------------------
#
# The kanban-card acceptance criterion reads: "Als een saver actief was op
# een dispatch, moet dat achteraf terug te vinden zijn. Anders is een
# kwaliteitsklacht niet te debuggen." That is the activity-feed obligation
# on top of the helper API — the previous unit-test (post_note_dedups_…)
# only proved the *direct* call path; the dispatch-bridge has to actually
# land the comment on a real card. These tests assert that contract by
# driving ``_install_rtk_for_dispatch`` and inspecting the op-log through
# the same kanban DB the production code path writes through (the helper
# opens its own private engine on ``settings.kanban_database_url``).
# We monkeypatch that URL to the test-DB file so both ends see the same
# data.


def _test_kanban_database_url() -> str:
    """Mirror the test-DB file URL that ``kanban_test_db`` opens."""
    from tests.kanban_test_db import _db_path
    return f"sqlite+aiosqlite:///{_db_path}"


async def _create_card(project_key: str = "PROJ", column: str = "Backlog") -> str:
    """Create a real card via ``apply_operation`` and return its id."""
    from app.kanban.operations import apply_operation
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=project_key, entity_id=None,
            payload={"title": "dispatch test", "column": column},
        )
        await s.commit()
    return cid


async def _count_token_saver_notes(card_id: str) -> int:
    """Count ``**Note:** Token saver …`` comments on the card's op-log."""
    from sqlalchemy import func, select

    from app.kanban.models import KanbanOp
    async with KanbanSessionLocal() as s:
        n = (await s.execute(
            select(func.count()).select_from(KanbanOp)
            .where(KanbanOp.entity_id == card_id)
            .where(KanbanOp.op_type == "comment")
            .where(KanbanOp.payload["text"].as_string().like(
                "%Token saver%",
            ))
        )).scalar_one()
    return n


async def _token_saver_note_text(card_id: str) -> str | None:
    """Return the text of the first ``**Note:** Token saver …`` comment, or None."""
    from sqlalchemy import select

    from app.kanban.models import KanbanOp
    async with KanbanSessionLocal() as s:
        op = (await s.execute(
            select(KanbanOp)
            .where(KanbanOp.entity_id == card_id)
            .where(KanbanOp.op_type == "comment")
            .where(KanbanOp.payload["text"].as_string().like(
                "%Token saver%",
            ))
            .order_by(KanbanOp.hlc.asc())
            .limit(1)
        )).scalar_one_or_none()
    if op is None:
        return None
    return (op.payload or {}).get("text")


async def _call_dispatch_bridge(
    card_id: str, project_key: str, column_name: str,
    worktree_path: str, repo_path: str,
) -> tuple[str, str]:
    """Call ``_install_rtk_for_dispatch_async`` directly and return ``(status, reason)``.

    We invoke the async core (not the sync wrapper) because the test
    already runs inside a pytest-asyncio event loop, which the sync
    wrapper's ``asyncio.run()`` cannot re-enter. The async core is the
    one that actually calls ``post_note`` on the activity feed, so the
    test exercises the real path end-to-end.
    """
    from app.kanban.dispatch import _install_rtk_for_dispatch_async
    return await _install_rtk_for_dispatch_async(
        card_id=card_id,
        project_key=project_key,
        column_name=column_name,
        worktree_path=worktree_path,
        repo_path=repo_path,
    )


@pytest.mark.asyncio
async def test_dispatch_bridge_posts_activated_note_on_active(
    tmp_path, monkeypatch,
):
    """When the bridge returns ``active``, a
    ``**Note:** Token saver activated: …`` comment lands on the card."""
    from app.config import settings
    monkeypatch.setattr(settings, "kanban_database_url", _test_kanban_database_url())

    cid = await _create_card(project_key="PROJ", column="engineer")
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"))
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    status, _ = await _call_dispatch_bridge(
        card_id=cid, project_key="PROJ", column_name="engineer",
        worktree_path=str(worktree), repo_path=str(tmp_path),
    )
    assert status == "active"

    text = await _token_saver_note_text(cid)
    assert text is not None, (
        "active dispatch did not post a Token saver note on the card's "
        "activity feed — the dispatch-bridge must call post_note on the "
        "active path (kaart c31333bf… acceptance criterion: 'Zichtbaar in "
        "de activity-feed')"
    )
    assert text.startswith("**Note:** Token saver")
    assert "activated" in text
    assert "0.43.0" in text


@pytest.mark.asyncio
async def test_dispatch_bridge_posts_fail_open_note_on_missing_binary(
    tmp_path, monkeypatch,
):
    """When the bridge returns ``failed`` (fail-open), a
    ``**Note:** Token saver fail-open: <reason>`` comment lands on the card."""
    from app.config import settings
    monkeypatch.setattr(settings, "kanban_database_url", _test_kanban_database_url())

    cid = await _create_card(project_key="PROJ", column="engineer")
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)
    monkeypatch.delenv("COCKPIT_RTK_BIN", raising=False)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(token_saver.shutil, "which", lambda _: None)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    status, _ = await _call_dispatch_bridge(
        card_id=cid, project_key="PROJ", column_name="engineer",
        worktree_path=str(worktree), repo_path=str(tmp_path),
    )
    assert status == "failed"

    text = await _token_saver_note_text(cid)
    assert text is not None, (
        "failed (fail-open) dispatch did not post a 'Token saver fail-open' "
        "note on the card's activity feed — the card promise was: 'één "
        "**Note:** Token saver fail-open: <reden> comment'"
    )
    assert text.startswith("**Note:** Token saver")
    assert "fail-open" in text
    assert "rtk binary missing" in text


@pytest.mark.asyncio
async def test_dispatch_bridge_silent_on_inactive_path(
    tmp_path, monkeypatch,
):
    """Default-off path (per-lane flag off) does NOT post a note.

    A note on every Backlog dispatch would flood the feed — the comment
    is reserved for the diagnostic paths (active + fail-open)."""
    from app.config import settings
    monkeypatch.setattr(settings, "kanban_database_url", _test_kanban_database_url())

    cid = await _create_card(project_key="PROJ", column="Backlog")
    await _seed_column("PROJ", "Backlog", token_saver_enabled=0)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    status, _ = await _call_dispatch_bridge(
        card_id=cid, project_key="PROJ", column_name="Backlog",
        worktree_path=str(worktree), repo_path=str(tmp_path),
    )
    assert status == "inactive"

    count = await _count_token_saver_notes(cid)
    assert count == 0, (
        f"inactive path posted {count} note(s); the default-off path must "
        "be silent so the activity feed is not flooded"
    )


@pytest.mark.asyncio
async def test_sync_bridge_works_when_called_inside_running_event_loop(
    tmp_path, monkeypatch,
):
    """The production caller invokes the sync transport on its event-loop thread."""
    from app.config import settings
    from app.kanban.dispatch import _install_rtk_for_dispatch

    monkeypatch.setattr(settings, "kanban_database_url", _test_kanban_database_url())
    cid = await _create_card(project_key="PROJ", column="engineer")
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"))
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    status = _install_rtk_for_dispatch(
        card_id=cid,
        project_key="PROJ",
        column_name="engineer",
        worktree_path=str(worktree),
        repo_path=str(tmp_path),
    )

    assert status == "active"
    assert await _token_saver_note_text(cid) == (
        "**Note:** Token saver activated: RTK 0.43.0"
    )


@pytest.mark.asyncio
async def test_dispatch_commits_claim_before_running_worktree_token_saver(
    tmp_path, monkeypatch,
):
    """The worker DB connection must not collide with the dispatch transaction."""
    from app.config import settings
    from app.kanban import dispatch

    monkeypatch.setattr(settings, "kanban_database_url", _test_kanban_database_url())
    cid = await _create_card(project_key="PROJ", column="Backlog")
    await _seed_column("PROJ", "engineer", token_saver_enabled=1)
    await _seed_kill_switch("PROJ", enabled=True)

    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(token_saver.shutil, "which", lambda _: str(bin_dir / "rtk"))
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    observed: dict[str, str] = {}

    def _transport(
        *, directory, prompt, session_name, cli_id="claude-code",
        provider="anthropic", model=None, card_id=None, column_name=None,
        **_kwargs,
    ):
        observed["status"] = dispatch._install_rtk_for_dispatch(
            card_id=card_id,
            project_key="PROJ",
            column_name=column_name,
            worktree_path=str(worktree),
            repo_path=str(tmp_path),
        )
        return {"session_name": session_name}

    _transport.transport_kind = "worktree"

    async with KanbanSessionLocal() as session:
        await dispatch.dispatch_card(
            session,
            card_id=cid,
            project_path=str(tmp_path),
            transport=_transport,
        )
        await session.commit()

    assert observed["status"] == "active"
    assert await _token_saver_note_text(cid) == (
        "**Note:** Token saver activated: RTK 0.43.0"
    )


@pytest.mark.asyncio
async def test_sync_bridge_failure_posts_fail_open_note(tmp_path, monkeypatch):
    """A failure outside the async core remains visible and does not escape."""
    from app.config import settings
    from app.kanban import dispatch

    monkeypatch.setattr(settings, "kanban_database_url", _test_kanban_database_url())
    cid = await _create_card(project_key="PROJ", column="engineer")

    async def _raise_bridge_error(**_kwargs):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(
        dispatch, "_install_rtk_for_dispatch_async", _raise_bridge_error,
    )

    status = dispatch._install_rtk_for_dispatch(
        card_id=cid,
        project_key="PROJ",
        column_name="engineer",
        worktree_path=str(tmp_path / "wt"),
        repo_path=str(tmp_path),
    )

    assert status == "failed"
    assert await _token_saver_note_text(cid) == (
        "**Note:** Token saver fail-open: bridge RuntimeError: worker exploded"
    )


# --- Sync-bridge: non-async caller fallback ----------------------------------
#
# Production invokes the wrapper on an active event-loop thread; the tests
# above cover that path and the open dispatch transaction. This secondary test
# keeps the bridge's no-running-loop fallback covered for direct sync callers.


def _run_sync_bridge_in_thread(
    card_id: str, project_key: str, column_name: str,
    worktree_path: str, repo_path: str, kanban_database_url: str,
) -> str:
    """Execute ``_install_rtk_for_dispatch`` in a fresh thread + event loop.

    Returns the status string the sync wrapper gives back. We patch
    ``settings.kanban_database_url`` *inside* the thread so the worker's
    private engine (built by the wrapper) opens the test-DB file. The
    commit happens on the worker's session, which writes through the
    same on-disk DB the test then reads.
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.config import settings
    from app.kanban.dispatch import _install_rtk_for_dispatch

    def _call() -> str:
        settings.kanban_database_url = kanban_database_url
        return _install_rtk_for_dispatch(
            card_id=card_id, project_key=project_key,
            column_name=column_name,
            worktree_path=worktree_path, repo_path=repo_path,
        )

    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_call).result()


def test_sync_bridge_posts_activated_note_from_non_async_caller(
    tmp_path, monkeypatch,
):
    """The sync fallback opens a private loop and lands the activity note."""
    import asyncio

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import apply_operation

    monkeypatch.setattr("app.config.settings.kanban_database_url",
                        _test_kanban_database_url())

    # Seed the column + kill-switch on the test DB.
    bin_dir = tmp_path / "bin"
    _write_fake_rtk(bin_dir)
    monkeypatch.setattr(token_saver, "_resolve_cache_binary", lambda: None)
    monkeypatch.setattr(token_saver.shutil, "which",
                        lambda _: str(bin_dir / "rtk"))
    monkeypatch.setattr(token_saver, "_ensure_cache_ready", lambda: None)
    _seed_kill_switch_sync("PROJ", enabled=True)
    _seed_column_sync("PROJ", "engineer", token_saver_enabled=1)

    # Create a real card.
    async def _create() -> str:
        async with KanbanSessionLocal() as s:
            cid = await apply_operation(
                s, op_type="create", entity_type="card",
                project_key="PROJ", entity_id=None,
                payload={"title": "sync-bridge test", "column": "Backlog"},
            )
            await s.commit()
            return cid

    cid = asyncio.run(_create())

    worktree = tmp_path / "wt"
    worktree.mkdir()

    # Run the wrapper from a worker without a pre-existing event loop. This is
    # the secondary direct-sync path; normal dispatch is covered above.
    status = _run_sync_bridge_in_thread(
        card_id=cid, project_key="PROJ", column_name="engineer",
        worktree_path=str(worktree), repo_path=str(tmp_path),
        kanban_database_url=_test_kanban_database_url(),
    )
    assert status == "active"

    # Read the note back from the op-log of the same card the wrapper
    # just touched. The bridge posts to ``card_id``; the test created
    # the card on the test DB; the bridge ran against the test DB →
    # the note must be on the test DB.
    async def _read() -> str | None:
        async with KanbanSessionLocal() as s:
            from sqlalchemy import select

            from app.kanban.models import KanbanOp
            op = (await s.execute(
                select(KanbanOp)
                .where(KanbanOp.entity_id == cid)
                .where(KanbanOp.op_type == "comment")
                .where(KanbanOp.payload["text"].as_string().like(
                    "%Token saver%",
                ))
                .order_by(KanbanOp.hlc.asc())
                .limit(1)
            )).scalar_one_or_none()
            return (op.payload or {}).get("text") if op else None

    text = asyncio.run(_read())
    assert text is not None, (
        "sync-bridge dispatch did not post a Token saver note on the "
        "card's activity feed — make_worktree_transport calls this "
        "wrapper, so a missing note means production dispatches are "
        "invisible to operators investigating quality complaints (kaart "
        "c31333bf… acceptance criterion: 'Zichtbaar in de activity-feed')"
    )
    assert text.startswith("**Note:** Token saver")
    assert "activated" in text
    assert "0.43.0" in text
