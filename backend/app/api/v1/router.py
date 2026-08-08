"""Main API router for v1 endpoints."""
from fastapi import APIRouter

from .agent_mail import router as agent_mail_router
from .agents import router as agents_router
from .apm import router as apm_router
from .app_runs.router import router as app_runs_router
from .backup import router as backup_router
from .blueprints import router as blueprints_router
from .cc_bridge.router import router as cc_bridge_router
from .ci import router as ci_router
from .cli import router as cli_router
from .codex_config import router as codex_config_router
from .commands import router as commands_router
from .config import router as config_router
from .context import router as context_router
from .deploy import router as deploy_router
from .external_agent_mail import router as external_agent_mail_router
from .files import router as files_router
from .hooks import router as hooks_router
from .hosts.router import router as hosts_router
from .kanban.router import router as kanban_router
from .mcp import router as mcp_router
from .mcp_server import router as mcp_server_router
from .memory import router as memory_router
from .output_styles import router as output_styles_router
from .permissions import router as permissions_router
from .plans import router as plans_router
from .plugins import router as plugins_router
from .portfolio import router as portfolio_router
from .presence import router as presence_router
from .projects import router as projects_router
from .providers import router as providers_router
from .recurring_triggers.router import router as recurring_triggers_router
from .run_activity import router as run_activity_router
from .runs.router import router as runs_router
from .sandcastle.router import router as sandcastle_router
from .session_hooks.router import router as session_hooks_router
from .secrets import router as secrets_router
from .security import router as security_router
from .sessions import router as sessions_router
from .status import router as status_router
from .statusline import router as statusline_router
from .subscriptions import router as subscriptions_router
from .update.router import router as update_router
from .usage import router as usage_router
from .webhooks.router import router as webhooks_router

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Health check endpoint.

    Returns:
        dict: Status information
    """
    return {"status": "ok"}


# Include sub-routers
router.include_router(config_router)
router.include_router(projects_router)
router.include_router(cli_router)
router.include_router(mcp_router, prefix="/mcp", tags=["MCP Servers"])
router.include_router(commands_router)
router.include_router(plugins_router, tags=["Plugins"])
router.include_router(hooks_router, tags=["Hooks"])
router.include_router(permissions_router, tags=["Permissions"])
router.include_router(agents_router, tags=["Agents"])
router.include_router(agent_mail_router, prefix="/agent-mail", tags=["Agent Mail"])
router.include_router(external_agent_mail_router, prefix="/external/agent-mail", tags=["External Agent Mail"])
router.include_router(backup_router, tags=["Backup"])
router.include_router(output_styles_router, tags=["Output Styles"])
router.include_router(blueprints_router, tags=["Blueprints"])
router.include_router(ci_router, tags=["CI Templates"])
router.include_router(statusline_router, tags=["Status Line"])
router.include_router(sessions_router, tags=["Sessions"])
router.include_router(usage_router, tags=["Usage"])
router.include_router(memory_router, tags=["Memory"])
router.include_router(context_router, tags=["Context"])
router.include_router(plans_router, tags=["Plans"])
router.include_router(presence_router, prefix="/presence", tags=["Presence"])
router.include_router(portfolio_router)
router.include_router(cc_bridge_router, prefix="/cc-bridge", tags=["CC Bridge"])
router.include_router(runs_router, prefix="/agent-bridge", tags=["Agent Bridge"])
router.include_router(providers_router, tags=["Providers"])
router.include_router(subscriptions_router)
router.include_router(codex_config_router, tags=["Codex Config"])
router.include_router(status_router, tags=["Status"])
router.include_router(session_hooks_router)
router.include_router(webhooks_router)
router.include_router(recurring_triggers_router)
router.include_router(security_router)
router.include_router(kanban_router)
router.include_router(run_activity_router)
router.include_router(app_runs_router)
router.include_router(apm_router)
router.include_router(files_router)
router.include_router(mcp_server_router, tags=["MCP Server"])
router.include_router(sandcastle_router, tags=["Sandcastle"])
router.include_router(hosts_router, tags=["Hosts"])
router.include_router(update_router)
router.include_router(security_router)
router.include_router(secrets_router, tags=["Secrets"])
router.include_router(deploy_router)
