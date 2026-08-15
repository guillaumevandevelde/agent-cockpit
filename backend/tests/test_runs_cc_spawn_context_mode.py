"""When the context-mode plugin is installed, its MCP server is merged into the
dispatched session's ``--mcp-config`` so its hooks (which always load) can
actually call the ``ctx_*`` tools they redirect to.

Background: kanban card ``[self-improve] context-mode-plugin blokkeert WebFetch
en curl naar een MCP-server die niet verbonden is``. The plugin's
``hooks.json`` registers ``PreToolUse`` matchers for ``WebFetch`` and
``Bash|curl``, but ``--strict-mcp-config`` (which the dispatch always emits for
isolation — see kanban card ``00fa8325``) excludes the plugin-discovered MCP
servers. The hook then denies the call and tells the session to use
``mcp__plugin_context-mode_context-mode__ctx_fetch_and_index``, which doesn't
exist in the session. Result: every external URL fetch dies.

Detection happens via the user's installed_plugins.json. The plugin's own
``.mcp.json`` provides the server definition with a ``${CLAUDE_PLUGIN_ROOT}``
template in ``args`` that has to be substituted with the actual install path —
that variable is only set when the plugin itself runs, not when its MCP server
is loaded via ``--mcp-config``.
"""

import json
from pathlib import Path


class _Result:
    returncode = 0
    stderr = ""


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _make_fake_context_mode_plugin(plugins_root: Path) -> Path:
    """Lay down a fake context-mode plugin install under ``plugins_root``."""
    install = plugins_root / "context-mode" / "1.0.168"
    install.mkdir(parents=True)
    # The plugin's own .mcp.json — same shape as the real one
    # (verified against /home/vdvgu/.claude/plugins/cache/context-mode/
    # context-mode/1.0.168/.mcp.json.example on 2026-08-15).
    _write_json(
        install / ".mcp.json",
        {
            "mcpServers": {
                "context-mode": {
                    "command": "node",
                    "args": ["${CLAUDE_PLUGIN_ROOT}/start.mjs"],
                }
            }
        },
    )
    return install


def _patch_installed_plugins(monkeypatch, plugins_root: Path, install: Path) -> None:
    """Point ``get_installed_plugins_file()`` at our fake registry."""
    from app.utils import path_utils

    registry = plugins_root / "installed_plugins.json"
    _write_json(
        registry,
        {
            "version": 2,
            "plugins": {
                "context-mode@context-mode": [
                    {
                        "scope": "user",
                        "installPath": str(install),
                        "version": "1.0.168",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(path_utils, "get_installed_plugins_file", lambda: registry)


def test_context_mode_merged_when_plugin_installed(monkeypatch, tmp_path):
    """Installed plugin → its MCP server lands in the dispatched .mcp.json
    with ``${CLAUDE_PLUGIN_ROOT}`` substituted to the install path.
    """
    import app.services.runs.cc_spawn as sp

    # 1) Fake plugin install + registry
    plugins_root = tmp_path / "plugins"
    install = _make_fake_context_mode_plugin(plugins_root)
    _patch_installed_plugins(monkeypatch, plugins_root, install)

    # 2) Project .mcp.json with cockpit-kanban only
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project_mcp = project_dir / ".mcp.json"
    _write_json(
        project_mcp,
        {
            "mcpServers": {
                "cockpit-kanban": {
                    "type": "sse",
                    "url": "http://localhost:8000/kanban-mcp/sse",
                }
            }
        },
    )

    # 3) Pin the merged-config cache to tmp_path so the test stays hermetic
    cache_dir = tmp_path / "merged-cache"
    monkeypatch.setattr(sp, "_MERGED_MCP_CACHE_DIR", cache_dir)

    args = sp._project_mcp_config_args(str(project_dir))

    # --strict-mcp-config stays — the merge happens via the explicit --mcp-config
    assert args[0] == "--strict-mcp-config"
    assert "--mcp-config" in args
    merged_path = Path(args[args.index("--mcp-config") + 1])

    # The dispatch passes the merged file, NOT the project's original .mcp.json,
    # because the original doesn't contain context-mode.
    assert merged_path != project_mcp
    assert merged_path.is_file()

    merged = json.loads(merged_path.read_text())
    servers = merged["mcpServers"]
    assert "cockpit-kanban" in servers, "project MCP must survive the merge"
    assert "context-mode" in servers, "plugin MCP must be merged in"

    # ${CLAUDE_PLUGIN_ROOT} must be substituted to the actual install path.
    # Claude Code resolves this var when plugins run their own MCP, but does
    # NOT resolve it when --mcp-config points at a file we generated, so we
    # substitute up front.
    ctx_args = servers["context-mode"]["args"]
    assert "${CLAUDE_PLUGIN_ROOT}" not in str(ctx_args)
    assert str(ctx_args[0]).endswith("/start.mjs")
    assert str(ctx_args[0]).startswith(str(install))


def test_context_mode_skipped_when_plugin_not_installed(monkeypatch, tmp_path):
    """No installed_plugins.json entry → no merge, project .mcp.json passed as-is."""
    import app.services.runs.cc_spawn as sp

    # Empty registry — context-mode not installed
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    _patch_installed_plugins(monkeypatch, plugins_root, install=None)  # type: ignore[arg-type]

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project_mcp = project_dir / ".mcp.json"
    _write_json(
        project_mcp,
        {
            "mcpServers": {
                "cockpit-kanban": {
                    "type": "sse",
                    "url": "http://localhost:8000/kanban-mcp/sse",
                }
            }
        },
    )

    args = sp._project_mcp_config_args(str(project_dir))
    assert "--mcp-config" in args
    assert Path(args[args.index("--mcp-config") + 1]) == project_mcp


def test_context_mode_skipped_when_project_has_no_mcp_json(monkeypatch, tmp_path):
    """A project without .mcp.json stays at the strict-only default — no merge."""
    import app.services.runs.cc_spawn as sp

    plugins_root = tmp_path / "plugins"
    install = _make_fake_context_mode_plugin(plugins_root)
    _patch_installed_plugins(monkeypatch, plugins_root, install)

    project_dir = tmp_path / "proj"
    project_dir.mkdir()  # no .mcp.json

    args = sp._project_mcp_config_args(str(project_dir))
    assert args == ["--strict-mcp-config"]


def test_context_mode_already_in_project_mcp_not_duplicated(monkeypatch, tmp_path):
    """If the project already declares context-mode, the merge is a no-op
    so the project file is passed unchanged.
    """
    import app.services.runs.cc_spawn as sp

    plugins_root = tmp_path / "plugins"
    install = _make_fake_context_mode_plugin(plugins_root)
    _patch_installed_plugins(monkeypatch, plugins_root, install)

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project_mcp = project_dir / ".mcp.json"
    _write_json(
        project_mcp,
        {
            "mcpServers": {
                "cockpit-kanban": {
                    "type": "sse",
                    "url": "http://localhost:8000/kanban-mcp/sse",
                },
                "context-mode": {
                    "command": "node",
                    "args": ["/custom/path/start.mjs"],
                },
            }
        },
    )

    args = sp._project_mcp_config_args(str(project_dir))
    assert Path(args[args.index("--mcp-config") + 1]) == project_mcp


def test_context_mode_corrupt_registry_does_not_crash(monkeypatch, tmp_path):
    """A malformed installed_plugins.json must not break dispatch — falls back
    to the project .mcp.json as-is.
    """
    import app.services.runs.cc_spawn as sp
    from app.utils import path_utils

    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    registry = plugins_root / "installed_plugins.json"
    registry.write_text("{not valid json")
    monkeypatch.setattr(path_utils, "get_installed_plugins_file", lambda: registry)

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project_mcp = project_dir / ".mcp.json"
    _write_json(project_mcp, {"mcpServers": {"cockpit-kanban": {"type": "sse"}}})

    args = sp._project_mcp_config_args(str(project_dir))
    assert Path(args[args.index("--mcp-config") + 1]) == project_mcp