"""Aggregate Agent Mail install status across Claude Code hooks and Codex hooks.
No MCP-installed flags here — MCP wiring is a generic Cockpit concern (see
the MCP Server page), not managed by this installer."""
import json
import shutil
import sys

from app.config import settings
from app.models.agent_mail_schemas import AgentMailInstallStatus, AgentMailSnippets
from app.services.agent_mail import codex_hooks, hook_installer
from app.services.agent_mail.hook_script import MAIL_HOOK_EVENTS
from app.utils.path_utils import get_claude_user_settings_file


def cockpit_base_url() -> str:
    return f"http://127.0.0.1:{settings.port}"


def codex_cli_available() -> bool:
    try:
        from app.services.cli_executor import ProviderCLIExecutor
        return ProviderCLIExecutor("codex-cli").binary_path is not None
    except Exception:
        return False


async def get_install_status() -> AgentMailInstallStatus:
    installed = [event for event, ok in hook_installer.get_hooks_status().items() if ok]
    missing = [event for event in MAIL_HOOK_EVENTS if event not in installed]
    codex_installed = codex_hooks.installed_codex_hooks()
    codex_missing = [event for event in codex_hooks.CODEX_MAIL_HOOK_EVENTS if event not in codex_installed]
    return AgentMailInstallStatus(
        claude_code_hooks=sorted(installed),
        claude_code_hooks_missing=missing,
        codex_cli_available=codex_cli_available(),
        codex_hooks=codex_installed,
        codex_hooks_missing=codex_missing,
        curl_available=shutil.which("curl") is not None,
        codex_hook_shim_path=codex_hooks.hook_shim_path(),
        python_path=sys.executable,
        cockpit_url=cockpit_base_url(),
        claude_settings_path=str(get_claude_user_settings_file()),
        codex_hooks_path=str(codex_hooks.codex_hooks_path()),
    )


async def apply_claude_code_install() -> AgentMailInstallStatus:
    hook_installer.install_missing_hooks()
    return await get_install_status()


async def uninstall_claude_code() -> AgentMailInstallStatus:
    settings_file = get_claude_user_settings_file()
    if settings_file.exists():
        doc = json.loads(settings_file.read_text())
        hooks = doc.get("hooks", {})
        for event in list(hooks.keys()):
            hooks[event] = [
                g for g in hooks[event]
                if not any("agent-mail/hooks/" in h.get("command", "") for h in g.get("hooks", []))
            ]
            if not hooks[event]:
                hooks.pop(event)
        settings_file.write_text(json.dumps(doc, indent=2))
    return await get_install_status()


async def apply_codex_install() -> AgentMailInstallStatus:
    if not codex_cli_available():
        raise ValueError("Codex CLI is not available on this machine")
    codex_hooks.install_codex_hooks()
    return await get_install_status()


async def uninstall_codex() -> AgentMailInstallStatus:
    codex_hooks.uninstall_codex_hooks()
    return await get_install_status()


def get_snippets() -> AgentMailSnippets:
    hooks_snippet = (
        f'{{\n'
        f'  "hooks": {{\n'
        f'    "SessionStart": [{{"matcher": "startup|resume|clear|compact", "hooks": '
        f'[{{"type": "command", "command": "{sys.executable} {codex_hooks.hook_shim_path()} '
        f'--cockpit-url {cockpit_base_url()} --provider codex-cli --event session-start"}}]}}]\n'
        f'  }}\n'
        f'}}\n'
    )
    agents_md = (
        "## Claude Cockpit Agent Mail\n"
        "You are part of a local agent team coordinated through Claude Cockpit.\n"
        "- Call `agent_mail_whoami` once when you start working to register and learn your role.\n"
        "- Call `agent_mail_check_inbox` before starting major tasks and after finishing one.\n"
        "- Use `agent_mail_request_context` to ask another repo's agent a question, and\n"
        "  `agent_mail_create_handoff` to hand work over.\n"
    )
    return AgentMailSnippets(codex_hooks_snippet=hooks_snippet, agents_md_snippet=agents_md)
