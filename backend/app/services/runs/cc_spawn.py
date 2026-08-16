"""Spawn and kill Claude Code sessions in tmux.

Legacy CC-Bridge-only spawn path. Kanban dispatch (including resume/take-over
transports) calls ``app.services.runs.spawn.spawn_session`` via
``app.kanban.dispatch.make_resume_transport`` instead — that module is the
canonical spawn path for anything dispatched through the board.
"""
import json
import logging
import re
import shlex
import subprocess
import uuid
from pathlib import Path

from app.services.agentic_cli.provider_env import _record_audit, build_spawn_env
from app.utils.git_ref import sanitize_git_branch_name

logger = logging.getLogger(__name__)

_spawned_sessions: dict[str, dict] = {}

# Default transport when ``runtime`` is omitted. Backward compat for the
# legacy CC Bridge route that doesn't yet pass the new env-injection
# kwargs. See kanban card
# `[security][D] Per-project env-injectie in spawn_session`.
_DEFAULT_RUNTIME = "worktree"

# Where merged MCP configs are cached on disk. Persistent (not /tmp) so a
# dispatch followed by a follow-up shell call can still read what we wrote.
# See kanban card ``[self-improve] context-mode-plugin blokkeert WebFetch en
# curl naar een MCP-server die niet verbonden is`` — the merge exists so the
# plugin's hooks (which always load, regardless of --strict-mcp-config) can
# actually call the ctx_* tools they redirect to.
_MERGED_MCP_CACHE_DIR = Path.home() / ".cache" / "cockpit-ship" / "merged-mcp"

# Plugin whose MCP server we transparently merge when installed. Detected via
# ``installed_plugins.json``. Add more names here only when the plugin's hooks
# are known to redirect to an MCP tool that would otherwise be missing — i.e.
# the same failure class the merge is built to prevent. Other plugins' MCP
# servers stay outside the dispatch sandbox by design (``--strict-mcp-config``).
_MERGED_PLUGIN_KEYS = ("context-mode@context-mode",)


def _read_plugin_mcp_server(plugin_install_path: Path, server_name: str) -> dict | None:
    """Return the plugin's ``server_name`` MCP server dict, with plugin-root
    vars resolved, or ``None`` if not declared / unreadable.

    Reads ``<install>/.mcp.json`` first (legacy format), then
    ``<install>/.claude-plugin/plugin.json`` under ``mcpServers`` (newer format;
    may be a dict or a relative-path string pointing at another JSON file —
    see ``MCPConfigService._read_plugin_mcp_servers`` for the canonical reader).
    """
    from app.utils.file_utils import read_json_file  # local import: keeps top of module light

    mcp_servers: dict = {}

    plugin_mcp_path = plugin_install_path / ".mcp.json"
    cfg = read_json_file(plugin_mcp_path)
    if isinstance(cfg, dict):
        if isinstance(cfg.get("mcpServers"), dict):
            mcp_servers.update(cfg["mcpServers"])
        else:
            # Flat dict of server_name → server-config
            mcp_servers.update(cfg)

    plugin_json_path = plugin_install_path / ".claude-plugin" / "plugin.json"
    plugin_json = read_json_file(plugin_json_path)
    if isinstance(plugin_json, dict):
        value = plugin_json.get("mcpServers")
        if isinstance(value, dict):
            mcp_servers.update(value)
        elif isinstance(value, str):
            ref_path = (plugin_json_path.parent / value).resolve()
            # Ref must stay inside the plugin install dir — guard against
            # a malicious plugin.json pointing at /etc/passwd or similar.
            if str(ref_path).startswith(str(plugin_install_path.resolve()) + "/") or ref_path == plugin_install_path.resolve():
                ref_data = read_json_file(ref_path)
                if isinstance(ref_data, dict) and isinstance(ref_data.get("mcpServers"), dict):
                    mcp_servers.update(ref_data["mcpServers"])

    server_cfg = mcp_servers.get(server_name)
    if not isinstance(server_cfg, dict):
        return None

    # Substitute ${CLAUDE_PLUGIN_ROOT} recursively so args / env / headers
    # all see the resolved install path.
    install_path_str = str(plugin_install_path)
    resolved = json.loads(
        json.dumps(server_cfg, default=str).replace(
            "${CLAUDE_PLUGIN_ROOT}", install_path_str
        )
    )
    return resolved


def _resolve_merged_mcp_path(project_mcp_path: Path) -> Path | None:
    """If a known hook-only plugin is installed and its MCP server isn't
    already in ``project_mcp_path``, write a merged copy under
    ``_MERGED_MCP_CACHE_DIR`` and return its path.

    Returns ``None`` when no merge is needed (plugin absent, project already
    declares it, or the plugin's MCP can't be read). The dispatch then falls
    back to passing ``project_mcp_path`` unchanged.

    Kanban card: ``[self-improve] context-mode-plugin blokkeert WebFetch en
    curl naar een MCP-server die niet verbonden is``. The plugin's
    ``hooks.json`` registers ``PreToolUse`` matchers for ``WebFetch`` and
    ``Bash|curl``; the hook denies those calls and tells the session to call
    ``mcp__plugin_context-mode_context-mode__ctx_fetch_and_index`` (and friends).
    ``--strict-mcp-config`` (kaart ``00fa8325``) excludes plugin-discovered
    MCPs from the dispatched session, so the redirect target does not exist
    and the session is broken. Merging the plugin's MCP into the dispatch's
    ``--mcp-config`` keeps the rest of the strict-isolation guarantee while
    making the redirect actually reachable.
    """
    from app.utils.file_utils import read_json_file  # local import: cc_spawn is hot
    from app.utils.path_utils import get_installed_plugins_file

    registry_path = get_installed_plugins_file()
    if not registry_path.is_file():
        return None

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt registry must not break dispatch — fall through to None.
        return None

    plugins = registry.get("plugins", {}) if isinstance(registry, dict) else {}
    if not isinstance(plugins, dict):
        return None

    project_cfg = read_json_file(project_mcp_path)
    project_servers = (
        project_cfg.get("mcpServers", {}) if isinstance(project_cfg, dict) else {}
    )
    if not isinstance(project_servers, dict):
        project_servers = {}

    for plugin_key in _MERGED_PLUGIN_KEYS:
        installations = plugins.get(plugin_key)
        if not installations or not isinstance(installations, list):
            continue
        first = installations[0]
        if not isinstance(first, dict):
            continue
        # Use the first installation (registry lists at most one per scope
        # pair in practice; entries may differ by scope=project/user, but the
        # plugin root is the same regardless).
        install_path_raw = first.get("installPath")
        install_path = Path(install_path_raw) if install_path_raw else None
        if install_path is None or not install_path.is_dir():
            continue
        # The plugin's MCP server name is the LAST segment of the key
        # (``context-mode`` from ``context-mode@context-mode``). Hard-coded
        # below for now — extend the registry shape if more names join the
        # merge list.
        server_name = plugin_key.split("@", 1)[0]
        if server_name in project_servers:
            # Project already declares it — nothing to merge.
            continue
        plugin_server = _read_plugin_mcp_server(install_path, server_name)
        if plugin_server is None:
            continue
        project_servers[server_name] = plugin_server
        # One merge per dispatch is enough; don't pile multiple plugin servers
        # into one file when only one is in the registry.
        merged = {"mcpServers": project_servers}
        cache_dir = _MERGED_MCP_CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Stable cache key: hash the project path so different repos don't
        # collide and the same repo reuses the same merged file across
        # dispatches (idempotent).
        cache_key = re.sub(r"[^A-Za-z0-9._-]", "_", str(project_mcp_path.parent))[:80]
        merged_path = cache_dir / f"{cache_key}.mcp.json"
        merged_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        return merged_path

    return None


def _project_mcp_config_args(directory: str, repo_path: str | None = None) -> list[str]:
    """Return the MCP-isolation flags for ``directory``.

    Always emits ``--strict-mcp-config`` so a host-user's global
    ``~/.claude.json`` MCP entries — and any plugin-discovered MCPs — never
    leak into dispatched sessions. See kanban card ``00fa8325`` /
    ``docs/cockpit/token-optimization-analysis.md`` §4 R5.

    Emits ``--mcp-config`` when a project ``.mcp.json`` exists. Resolution
    order:

    1. ``<directory>/.mcp.json`` — the launch cwd (the worktree for a
       worktree-transport dispatch). This is the canonical case for cockpit
       itself, where ``.mcp.json`` is tracked and so present in every fresh
       worktree.
    2. ``<repo_path>/.mcp.json`` — the repo-root fallback, when supplied and
       the launch cwd has none. This covers the **external product-project**
       case: ``POST /enable`` writes ``.mcp.json`` into the repo-root, but a
       product-project keeps it untracked (gitignored), so a fresh
       ``git worktree add origin/master`` worktree never receives it. Pointing
       ``--mcp-config`` at the repo-root copy gives the dispatched agent its
       ``cockpit-kanban`` MCP **without** placing an untracked file inside the
       worktree — the file-copy route left a dirty worktree that broke the
       ship gate and risked committing Cockpit's ``Authorization: Bearer``
       token into the customer's git history (kaart ``3672c073…``).

    Only emits ``--mcp-config`` when the resolved file actually exists: a fresh
    product-project has none, and Claude Code would otherwise exit 1 in ~2s
    with ``MCP config file not found`` (see kanban card `[problem]
    Product-project zonder .mcp.json sterft binnen ~2s bij elke dispatch`).
    When no file is found, ``--strict-mcp-config`` alone means zero MCPs are
    loaded — the safest possible default for a brand-new project.

    If a plugin whose hooks always redirect to a plugin-only MCP is installed
    (currently: context-mode — see kanban card ``[self-improve]
    context-mode-plugin blokkeert WebFetch en curl``), and that plugin's MCP
    server is NOT already declared in the project's ``.mcp.json``, the helper
    writes a merged copy under ``~/.cache/cockpit-ship/merged-mcp/`` and
    passes that to ``--mcp-config`` instead. This keeps the strict-isolation
    contract (no global ``~/.claude.json`` MCPs, no other plugin MCPs) while
    letting the plugin's own hooks reach their redirect target.

    Single source of truth: both this legacy bridge and the newer
    ``agentic_cli/claude_code.build_spawn_command`` import the same helper so a
    security fix can't drift between paths.
    """
    args = ["--strict-mcp-config"]
    mcp_path = Path(directory) / ".mcp.json"
    if not mcp_path.is_file() and repo_path:
        repo_mcp = Path(repo_path) / ".mcp.json"
        if repo_mcp.is_file():
            mcp_path = repo_mcp
    if mcp_path.is_file():
        # ``mcp_path`` here is the resolved project MCP config (worktree or
        # repo-root copy). If a hook-only plugin needs its MCP merged in, the
        # resolved path is replaced with a freshly-written merged file.
        # The project file itself is never mutated.
        merged_path = _resolve_merged_mcp_path(mcp_path)
        effective_path = merged_path if merged_path is not None else mcp_path
        args += ["--mcp-config", str(effective_path)]
    return args


def _resolve_project_directory(project_folder: str, session_id: str | None = None) -> str:
    """Resolve a Claude project folder name to the actual project directory.

    Prefer the selected transcript's recorded cwd. Reconstructing the path from
    Claude's folder name is lossy because both slashes and hyphens are encoded as
    hyphens.
    """
    folder_path = Path(project_folder)
    if folder_path.name != project_folder or ".." in folder_path.parts:
        raise ValueError(f"Invalid project folder: '{project_folder}'")

    decoded = "/" + project_folder.lstrip("-").replace("-", "/")
    decoded_path = Path(decoded)
    # Guard against path traversal — must be an existing absolute directory
    if not decoded_path.is_absolute() or ".." in decoded_path.parts:
        raise ValueError(f"Invalid project folder: '{project_folder}'")

    # Fast-path: the decoded worktree exists, so the resume project_folder is
    # still good. Skip the transcript walker entirely (kaart cac950cb…).
    if decoded_path.is_dir():
        return str(decoded_path.resolve())

    # Slow path: the decoded worktree is gone — look for a transcript cwd that
    # points at a still-existing directory. This preserves the original
    # transcript-preference behavior for the edge case where the user renamed
    # the branch and the leftover transcript's cwd is the new (different) path.
    # The fast-fail below intercepts the bug case where the worktree was
    # merged + GC'd AND the transcript cwd points to the same dead path: the
    # walker used to spin for ~27s parsing 285 × ~3KB lines on a 947K
    # transcript (the real 2026-08-16 reproduce for kaart cac950cb…) before
    # raising, blocking the entire dispatch tick while holding a column slot.
    # Probe the first line cheaply before paying for the full parse.
    if session_id:
        transcript = Path.home() / ".claude" / "projects" / project_folder / f"{session_id}.jsonl"
        if transcript.is_file():
            first_cwd = _read_first_transcript_cwd(transcript)
            if first_cwd is not None:
                if not _is_safe_absolute_path(first_cwd):
                    raise ValueError(
                        f"Could not resolve project directory for '{project_folder}'. "
                        f"Please provide the directory path explicitly."
                    )
                resolved = Path(first_cwd).resolve()
                if resolved.is_dir():
                    return str(resolved)
                # First cwd matches something but the directory is gone — same
                # case as the decoded path, fall through to the raise.
            else:
                # No cwd anywhere in the transcript — same fast-fail as the
                # decoded-path missing case.
                pass

    raise ValueError(
        f"Could not resolve project directory for '{project_folder}'. "
        f"Please provide the directory path explicitly."
    )


def _read_first_transcript_cwd(transcript: Path) -> str | None:
    """Read the first ``cwd`` value from a Claude transcript file.

    Walks the file line-by-line until a JSON object with a ``cwd`` field is
    found. Avoids the cost of full json.loads() on every line by streaming
    raw bytes and stopping at the first match. Returns ``None`` when no
    ``cwd`` is found or the file cannot be read.
    """
    try:
        with transcript.open("r", encoding="utf-8") as handle:
            for line in handle:
                # Cheap prefix check skips the bulk of assistant/hook lines
                # that have no cwd. Avoids paying for json.loads on the
                # usually-much-larger non-cwd entries.
                if '"cwd"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = entry.get("cwd")
                if cwd:
                    return cwd
    except OSError:
        logger.warning("Could not read Claude transcript for directory resolution: %s", transcript)
    return None


def _is_safe_absolute_path(cwd: str) -> bool:
    """True when ``cwd`` is an absolute path without traversal segments."""
    return Path(cwd).is_absolute() and ".." not in Path(cwd).parts


def spawn_session(
    directory: str,
    mode: str = "plain",
    worktree_name: str | None = None,
    session_id: str | None = None,
    project_folder: str | None = None,
    skip_permissions: bool = False,
    extra_args: list[str] | None = None,
    *,
    project_key: str | None = None,
    runtime: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Spawn a new Claude Code session inside a tmux session.

    Args:
        directory: Absolute path to the working directory.
        mode: One of "plain", "worktree", or "resume".
        worktree_name: Name for the worktree (mode="worktree" only).
        session_id: Claude session ID to resume (mode="resume" only).
        project_folder: Claude project folder name (for resume mode directory resolution).
        skip_permissions: Append --dangerously-skip-permissions flag.
        project_key: Optional project key (``git:host/path`` or ``slug:name``)
            — when supplied, gets injected as ``COCKPIT_PROJECT_KEY``.
        runtime: Transport hint (``worktree|sandcastle|headless|host``) —
            when supplied, gets injected as ``COCKPIT_RUNTIME``. Defaults
            to ``"worktree"`` for backward compat with existing callers.
        extra_env: Explicit env vars to inject (``-e KEY=VALUE``) — caller-
            resolved per-project secrets land here once follow-up #4 lands.
            The backend's ``os.environ`` is **never** merged in.

    Returns:
        Dict with tmux_target and session_name.

    Raises:
        ValueError: For invalid arguments.
    """
    # For resume mode, derive directory from project_folder if not provided
    if mode == "resume" and (not directory or not directory.strip()) and project_folder:
        directory = _resolve_project_directory(project_folder, session_id)

    # Validate directory — resolve to canonical path to prevent traversal attacks
    dir_path = Path(directory).resolve()
    if not dir_path.is_absolute():
        raise ValueError(f"Directory must be an absolute path: {directory}")
    if ".." in Path(directory).parts:
        raise ValueError(f"Directory must not contain path traversal: {directory}")
    if not dir_path.is_dir():
        raise ValueError(f"Directory does not exist: {directory}")
    # Use the resolved canonical path from here on
    directory = str(dir_path)

    # A user-supplied worktree name becomes a git branch — coerce it to a valid
    # ref before it reaches `--worktree`, and report whether it had to change.
    worktree_name_adjusted = False
    if mode == "worktree" and worktree_name:
        sanitized = sanitize_git_branch_name(worktree_name)
        worktree_name_adjusted = sanitized != worktree_name
        worktree_name = sanitized

    # Generate tmux session name including project directory basename
    import re
    dir_basename = dir_path.name or "project"
    # Sanitize: tmux disallows dots and colons in session names
    safe_basename = re.sub(r"[^a-zA-Z0-9_-]", "-", dir_basename)[:20]
    name = f"{safe_basename}-{uuid.uuid4().hex[:4]}"

    # Build command
    command = ["claude"]

    if mode == "plain":
        pass
    elif mode == "worktree":
        wt_name = worktree_name or name
        command += ["--worktree", wt_name]
    elif mode == "resume":
        if not session_id:
            raise ValueError("session_id is required for resume mode")
        command += ["--resume", session_id]
    else:
        raise ValueError(f"Unknown mode: {mode}")

    # Pin MCP servers to the project-`.mcp.json` only. Shared helper with
    # the agent-bridge ``claude_code.py:build_spawn_command`` so both paths
    # can't drift — see kanban card `00fa8325`.
    command += _project_mcp_config_args(directory)

    if skip_permissions:
        command.append("--dangerously-skip-permissions")

    if extra_args:
        command += extra_args

    # Build the explicit env dict for the spawned tmux session. Single
    # entry point lives in ``provider_env.build_spawn_env`` — shares the
    # extras-cleaning + cockpit-injection contract with the agent-bridge
    # ``spawn.py`` so a security fix lands in both. No provider env here
    # (the legacy CC-bridge doesn't have a provider abstraction), but the
    # ``cli_id`` default is ``claude-code`` — the only CLI this path ever
    # spawns — so ``CLAUDE_CODE_BASELINE_ENV`` still applies.
    effective_runtime = runtime if runtime is not None else _DEFAULT_RUNTIME
    spawn_env = build_spawn_env(
        provider_env={},
        extra_env=extra_env,
        project_key=project_key,
        runtime=effective_runtime,
    )

    env_flags: list[str] = []
    for key, value in spawn_env.env.items():
        env_flags += ["-e", f"{key}={value}"]

    _record_audit(
        project_key=project_key,
        runtime=effective_runtime,
        session_name=name,
        env_var_names=list(spawn_env.env.keys()),
    )

    # Spawn tmux session — tmux passes shell_command to $SHELL -c, so quote args
    shell_command = " ".join(shlex.quote(part) for part in command)
    try:
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "-c", directory, *env_flags, shell_command],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ValueError(f"tmux new-session failed: {result.stderr.strip()}")
    except FileNotFoundError:
        raise ValueError("tmux is not installed or not in PATH")
    except subprocess.TimeoutExpired:
        raise ValueError("tmux new-session timed out")

    # Store metadata
    _spawned_sessions[name] = {
        "mode": mode,
        "directory": directory,
        "worktree_name": worktree_name or (name if mode == "worktree" else None),
        "project_key": project_key,
        "runtime": effective_runtime,
        "env_var_names": spawn_env.names,
    }

    logger.info("Spawned session %s in %s (mode=%s)", name, directory, mode)
    return {
        "tmux_target": f"{name}:0.0",
        "session_name": name,
        "worktree_name": _spawned_sessions[name]["worktree_name"],
        "worktree_name_adjusted": worktree_name_adjusted,
    }


def kill_session(session_name: str, cleanup_worktree: bool = False) -> dict:
    """Kill a tmux session and optionally clean up its worktree.

    Args:
        session_name: The tmux session name to kill.
        cleanup_worktree: Remove the git worktree if applicable.

    Returns:
        Dict with killed status and optional error.
    """
    metadata = _spawned_sessions.get(session_name)

    # Kill the tmux session
    try:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"killed": False, "error": result.stderr.strip()}
    except FileNotFoundError:
        return {"killed": False, "error": "tmux is not installed or not in PATH"}
    except subprocess.TimeoutExpired:
        return {"killed": False, "error": "tmux kill-session timed out"}

    # Clean up worktree if requested
    if cleanup_worktree and metadata and metadata["mode"] == "worktree":
        wt_name = metadata.get("worktree_name")
        directory = metadata["directory"]
        if wt_name:
            try:
                subprocess.run(
                    ["git", "-C", directory, "worktree", "remove", wt_name, "--force"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                logger.info("Removed worktree %s in %s", wt_name, directory)
            except Exception:
                logger.warning(
                    "Failed to remove worktree %s in %s", wt_name, directory
                )

    # Remove from tracked sessions
    _spawned_sessions.pop(session_name, None)

    logger.info("Killed session %s", session_name)
    return {"killed": True}


def get_spawned_sessions() -> dict[str, dict]:
    """Return all sessions spawned by Deck."""
    return _spawned_sessions
