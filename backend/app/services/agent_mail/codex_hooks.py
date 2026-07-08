"""Edit ~/.codex/hooks.json to install/uninstall Agent Mail lifecycle hooks.

No MCP registration here — Codex connects to Cockpit's shared MCP server
the same generic way any other Codex MCP integration in this fork does.
This module only wires the SessionStart/UserPromptSubmit lifecycle hooks,
which need a real shim executable (see codex_hook_shim.py) because Codex's
hooks.json requires an argv, not a curl one-liner.
"""
import json
import shlex
import sys
from pathlib import Path

from app.config import settings
from app.services.providers.codex_cli import get_codex_home

CODEX_MAIL_HOOK_EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "user-prompt-submit"}
_HOOK_SHIM_MARKER = "codex_hook_shim.py"


def cockpit_base_url() -> str:
    return f"http://127.0.0.1:{settings.port}"


def hook_shim_path() -> str:
    return str(Path(__file__).resolve().with_name("codex_hook_shim.py"))


def codex_hooks_path() -> Path:
    return get_codex_home() / "hooks.json"


def _expected_matcher(event: str) -> str | None:
    return "startup|resume|clear|compact" if event == "SessionStart" else None


def _hook_command(slug: str) -> str:
    return " ".join([
        shlex.quote(sys.executable), shlex.quote(hook_shim_path()),
        "--cockpit-url", shlex.quote(cockpit_base_url()),
        "--provider", "codex-cli", "--event", shlex.quote(slug),
    ])


def _hook_entry(event: str, slug: str) -> dict:
    entry: dict = {
        "hooks": [{"type": "command", "command": _hook_command(slug), "statusMessage": "Checking Agent Mail", "timeout": 2}],
    }
    matcher = _expected_matcher(event)
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def _load_doc() -> dict:
    path = codex_hooks_path()
    if not path.exists():
        return {"hooks": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.setdefault("hooks", {})
    return doc


def _write_doc(doc: dict) -> None:
    path = codex_hooks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_managed(hook: object) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and _HOOK_SHIM_MARKER in hook["command"]


def _prune_managed_hooks(doc: dict) -> bool:
    changed = False
    hooks = doc.setdefault("hooks", {})
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept_hooks = [h for h in group["hooks"] if not _is_managed(h)]
            if len(kept_hooks) != len(group["hooks"]):
                changed = True
            if kept_hooks:
                kept_groups.append({**group, "hooks": kept_hooks})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    return changed


def _group_is_current(group: object, event: str, slug: str) -> bool:
    if not isinstance(group, dict) or group.get("matcher") != _expected_matcher(event):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(
        isinstance(h, dict) and h.get("type") == "command" and isinstance(h.get("command"), str)
        and _HOOK_SHIM_MARKER in h["command"] and f"--event {shlex.quote(slug)}" in h["command"]
        for h in hooks
    )


def installed_codex_hooks() -> list[str]:
    doc = _load_doc()
    hooks = doc.get("hooks", {})
    return sorted(
        event for event, slug in CODEX_MAIL_HOOK_EVENTS.items()
        if isinstance(hooks.get(event), list) and any(_group_is_current(g, event, slug) for g in hooks[event])
    )


def install_codex_hooks() -> None:
    doc = _load_doc()
    _prune_managed_hooks(doc)
    hooks = doc.setdefault("hooks", {})
    for event, slug in CODEX_MAIL_HOOK_EVENTS.items():
        hooks.setdefault(event, []).append(_hook_entry(event, slug))
    _write_doc(doc)


def uninstall_codex_hooks() -> bool:
    doc = _load_doc()
    changed = _prune_managed_hooks(doc)
    if changed:
        _write_doc(doc)
    return changed
