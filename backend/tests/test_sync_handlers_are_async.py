"""Regression test: every route handler in these modules must be `async def`.

These modules previously mixed blocking `def` handlers in with the rest of the
(async) FastAPI app. See kanban card "Maak sync route handlers async".
"""
import inspect

import pytest

from app.api.v1 import codex_config, permissions, plugins, providers, run_activity

HANDLERS = [
    (run_activity, "get_live_agents"),
    (run_activity, "get_activity_summary"),
    (codex_config, "get_codex_config"),
    (codex_config, "list_codex_config_files"),
    (codex_config, "get_codex_config_file"),
    (codex_config, "get_codex_config_raw"),
    (codex_config, "update_codex_config"),
    (codex_config, "replace_codex_config_safe_settings"),
    (permissions, "list_permissions"),
    (permissions, "list_permissions_by_scope"),
    (plugins, "list_plugins"),
    (plugins, "list_marketplaces"),
    (plugins, "add_marketplace"),
    (plugins, "remove_marketplace"),
    (plugins, "browse_marketplace"),
    (plugins, "get_marketplace_plugin_details"),
    (plugins, "update_marketplace"),
    (plugins, "check_plugin_updates"),
    (plugins, "get_all_available_plugins"),
    (plugins, "validate_plugin"),
    (plugins, "update_all_plugins"),
    (plugins, "update_plugin"),
    (plugins, "install_plugin"),
    (plugins, "get_plugin"),
    (providers, "list_providers"),
    (providers, "get_provider_status"),
    (providers, "get_provider_capabilities"),
    (providers, "execute_provider_cli"),
    (providers, "get_provider_doctor"),
    (providers, "get_provider_mcp_inventory"),
    (providers, "add_provider_mcp_server"),
    (providers, "remove_provider_mcp_server"),
    (providers, "get_provider_plugin_inventory"),
    (providers, "get_provider_feature_inventory"),
    (providers, "install_provider_plugin"),
    (providers, "remove_provider_plugin"),
    (providers, "enable_provider_plugin"),
    (providers, "disable_provider_plugin"),
    (providers, "get_provider_history_diagnostics"),
    (providers, "get_provider_usage_context_diagnostics"),
]


@pytest.mark.parametrize("module, name", HANDLERS, ids=[f"{m.__name__}.{n}" for m, n in HANDLERS])
def test_handler_is_coroutine_function(module, name):
    handler = getattr(module, name)
    assert inspect.iscoroutinefunction(handler), f"{module.__name__}.{name} must be `async def`"
