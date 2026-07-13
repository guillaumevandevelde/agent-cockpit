"""Pydantic schemas for API models."""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Blueprint models re-exported so the API layer can hand them straight back
# to the client. The canonical definitions live in
# `app.services.blueprint`; the API layer must not duplicate them.
from app.services.blueprint import (
    Blueprint,
    BlueprintAgent,
    BlueprintSettings,
    BlueprintSkill,
)


class ConfigFile(BaseModel):
    """Represents a configuration file."""

    path: str
    scope: str  # "user" or "project"
    exists: bool
    content: dict[str, Any] | None = None


class ConfigFileListResponse(BaseModel):
    """List of configuration files."""

    files: list[ConfigFile]


class MergedConfig(BaseModel):
    """Merged configuration from all scopes."""

    settings: dict[str, Any]
    mcp_servers: dict[str, Any]
    hooks: dict[str, list[Any]]
    permissions: dict[str, Any]
    commands: list[str]
    agents: list[str]


class RawFileContent(BaseModel):
    """Raw file content."""

    path: str
    content: str
    exists: bool


# Project Management Schemas


# Portfolio tag: meta-work (claude-cockpit itself) vs product-work (apps built
# by Cockpit). Enforced as an enum at the schema boundary — invalid values 422.
ProjectKind = Literal["meta", "product", "archived"]


class ProjectBase(BaseModel):
    """Base project schema."""

    name: str
    path: str
    source: str | None = None
    kind: ProjectKind = "product"
    priority: int | None = None


class ProjectCreate(ProjectBase):
    """Schema for creating a new project."""

    pass


class ProjectUpdate(BaseModel):
    """Schema for updating a project."""

    name: str | None = None
    is_active: bool | None = None
    kind: ProjectKind | None = None
    priority: int | None = None


class ProjectResponse(ProjectBase):
    """Schema for project response."""

    id: int
    is_active: bool
    last_accessed: str
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class ProjectListResponse(BaseModel):
    """List of projects."""

    projects: list[ProjectResponse]


class ProjectDiscoveryRequest(BaseModel):
    """Schema for project discovery request."""

    base_path: str


class ProjectDiscoveryResponse(BaseModel):
    """Schema for project discovery response."""

    discovered: list[ProjectBase]


class SetActiveProjectRequest(BaseModel):
    """Schema for setting active project."""

    project_id: int


# CLI Execution Schemas


class CLIExecuteRequest(BaseModel):
    """Schema for CLI execution request."""

    command: str
    args: list[str] = []
    cli: str = "claude-code"


class CLIResult(BaseModel):
    """Schema for CLI execution result."""

    stdout: str
    stderr: str
    exit_code: int


# MCP Server Schemas


class MCPServer(BaseModel):
    """MCP Server configuration."""

    name: str
    type: str  # "stdio", "http", or "sse"
    scope: str  # "user", "project", "plugin", or "managed"
    source: str | None = None  # Original source for display (e.g., plugin name)
    disabled: bool | None = None  # Whether server is disabled
    command: str | None = None  # For stdio type
    args: list[str] | None = None  # For stdio type
    url: str | None = None  # For http/sse type
    headers: dict[str, str] | None = None  # For http/sse type
    env: dict[str, str] | None = None  # Environment variables
    # Cache fields
    is_connected: bool | None = None
    last_tested_at: str | None = None
    last_error: str | None = None
    mcp_server_name: str | None = None
    mcp_server_version: str | None = None
    tools: list["MCPTool"] | None = None
    tool_count: int | None = None
    resources: list["MCPResource"] | None = None
    prompts: list["MCPPrompt"] | None = None
    resource_count: int | None = None
    prompt_count: int | None = None
    capabilities: dict[str, Any] | None = None


class MCPServerCreate(BaseModel):
    """Schema for creating an MCP server."""

    name: str
    type: str  # "stdio", "http", or "sse"
    scope: str  # "user" or "project"
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None


class MCPServerUpdate(BaseModel):
    """Schema for updating an MCP server."""

    type: str | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None


# MCP Server Approval Settings Schemas


class MCPServerApprovalMode(BaseModel):
    """Server-level approval mode configuration."""

    server_name: str
    mode: str  # "always-allow", "always-deny", "ask-every-time"


class MCPServerApprovalSettings(BaseModel):
    """MCP server approval settings for automatic tool permissions."""

    default_mode: str = "ask-every-time"  # "always-allow", "always-deny", "ask-every-time"
    server_overrides: list[MCPServerApprovalMode] = []


class MCPServerApprovalSettingsUpdate(BaseModel):
    """Schema for updating MCP server approval settings."""

    default_mode: str | None = None
    server_overrides: list[MCPServerApprovalMode] | None = None


class MCPServerToggleRequest(BaseModel):
    """Schema for toggling an MCP server's disabled state."""

    disabled: bool


class MCPServerToggleResponse(BaseModel):
    """Response from toggling an MCP server."""

    success: bool
    message: str
    server_name: str
    disabled: bool


class MCPServerListResponse(BaseModel):
    """List of MCP servers."""

    servers: list[MCPServer]


class MCPTestConnectionRequest(BaseModel):
    """Schema for testing MCP server connection."""

    name: str
    scope: str


class MCPTool(BaseModel):
    """MCP tool information."""

    name: str
    description: str | None = None
    inputSchema: dict[str, Any] | None = None


class MCPResource(BaseModel):
    """MCP resource information."""

    uri: str
    name: str
    description: str | None = None
    mimeType: str | None = None


class MCPPromptArgument(BaseModel):
    """MCP prompt argument."""

    name: str
    description: str | None = None
    required: bool | None = None


class MCPPrompt(BaseModel):
    """MCP prompt information."""

    name: str
    description: str | None = None
    arguments: list[MCPPromptArgument] | None = None


class MCPAuthStatus(BaseModel):
    """OAuth authentication status for an MCP server."""

    has_token: bool
    expired: bool
    server_url: str | None = None
    has_client_registration: bool | None = None


class MCPAuthStartResponse(BaseModel):
    """Response from starting an OAuth flow."""

    auth_url: str
    state: str


class MCPTestConnectionResponse(BaseModel):
    """Response from testing MCP server connection."""

    success: bool
    message: str
    server_name: str | None = None
    server_version: str | None = None
    tools: list[MCPTool] | None = None
    resources: list[MCPResource] | None = None
    prompts: list[MCPPrompt] | None = None
    resource_count: int | None = None
    prompt_count: int | None = None
    capabilities: dict[str, Any] | None = None


class MCPTestAllResult(BaseModel):
    """Result for a single server from test-all."""

    server_name: str
    scope: str
    success: bool
    message: str
    tool_count: int | None = None
    resource_count: int | None = None
    prompt_count: int | None = None


class MCPTestAllResponse(BaseModel):
    """Response from testing all MCP servers."""

    results: list[MCPTestAllResult]


# Slash Command Schemas


class SlashCommand(BaseModel):
    """Slash command configuration."""

    name: str
    path: str  # File path relative to commands directory
    scope: str  # "user" or "project"
    description: str | None = None
    allowed_tools: list[str] | None = None
    content: str  # Markdown content (without frontmatter)


class SlashCommandCreate(BaseModel):
    """Schema for creating a slash command."""

    name: str  # Can include namespace (e.g., "tools:analyze")
    scope: str  # "user" or "project"
    description: str | None = None
    allowed_tools: list[str] | None = None
    content: str


class SlashCommandUpdate(BaseModel):
    """Schema for updating a slash command."""

    description: str | None = None
    allowed_tools: list[str] | None = None
    content: str | None = None


class SlashCommandListResponse(BaseModel):
    """List of slash commands."""

    commands: list[SlashCommand]


# Plugin Schemas


class PluginComponent(BaseModel):
    """Plugin component (command, agent, hook, mcp, lsp, or skill)."""

    type: str  # "command", "agent", "hook", "mcp", "lsp", "skill"
    name: str
    description: str | None = None


class PluginHook(BaseModel):
    """Plugin-defined hook."""

    event: str  # PreToolUse, PostToolUse, etc.
    type: str = "command"  # "command", "prompt", "agent"
    matcher: str | None = None
    command: str | None = None
    prompt: str | None = None


class PluginLSPConfig(BaseModel):
    """Plugin LSP server configuration."""

    name: str
    language: str
    command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None


class Plugin(BaseModel):
    """Installed plugin configuration."""

    name: str
    version: str | None = None
    description: str | None = None
    author: str | None = None
    category: str | None = None
    source: str | None = None  # e.g., "anthropic-agent-skills", "claude-plugins-official", "local"
    enabled: bool = True
    scope: str | None = None  # "user", "project", "local"
    components: list[PluginComponent] = []
    # Component counts for quick display
    skill_count: int = 0
    agent_count: int = 0
    hook_count: int = 0
    mcp_count: int = 0
    lsp_count: int = 0
    # Extended information for plugin details
    usage: str | None = None  # Usage instructions
    examples: list[str] | None = None  # Example use cases
    readme: str | None = None  # README content (for local plugins)
    # Plugin-defined hooks (read-only)
    hooks: list[PluginHook] | None = None
    # LSP configurations
    lsp_configs: list[PluginLSPConfig] | None = None


class PluginListResponse(BaseModel):
    """List of installed plugins."""

    plugins: list[Plugin]


class MarketplacePlugin(BaseModel):
    """Plugin available in a marketplace."""

    name: str
    description: str | None = None
    version: str | None = None
    install_command: str


class MarketplacePluginListResponse(BaseModel):
    """List of plugins in a marketplace."""

    plugins: list[MarketplacePlugin]


class MarketplaceCreate(BaseModel):
    """Schema for adding a marketplace.

    Supports two input modes:
    1. Direct: Provide name and url directly
    2. Smart: Provide input field with "owner/repo" or full URL
    """

    name: str | None = None  # Optional - derived from input if not provided
    url: str | None = None   # Optional - derived from input if not provided
    input: str | None = None  # Accepts "owner/repo" or full URL


class MarketplaceResponse(BaseModel):
    """Marketplace configuration from Claude's known_marketplaces.json."""

    name: str
    repo: str
    install_location: str
    last_updated: str | None = None
    plugin_count: int = 0
    auto_update: bool = False  # Per-marketplace auto-update setting


class MarketplaceListResponse(BaseModel):
    """List of configured marketplaces."""

    marketplaces: list[MarketplaceResponse]


class PluginInstallRequest(BaseModel):
    """Schema for installing a plugin."""

    name: str
    marketplace_name: str | None = None
    scope: str = "user"  # "user", "project", "local"


class PluginInstallResponse(BaseModel):
    """Response from plugin installation."""

    success: bool
    message: str
    stdout: str | None = None
    stderr: str | None = None


class PluginToggleRequest(BaseModel):
    """Schema for toggling a plugin's enabled state."""

    enabled: bool
    source: str | None = None


class PluginToggleResponse(BaseModel):
    """Response from toggling a plugin."""

    success: bool
    message: str
    plugin: Optional["Plugin"] = None


# Plugin Update Schemas


class PluginUpdateInfo(BaseModel):
    """Information about a plugin update."""

    name: str
    installed_version: str | None = None
    latest_version: str | None = None
    has_update: bool = False
    source: str | None = None


class PluginUpdatesResponse(BaseModel):
    """Response containing plugins with available updates."""

    plugins: list[PluginUpdateInfo]
    outdated_count: int


class PluginValidationResult(BaseModel):
    """Result of validating a plugin."""

    valid: bool
    errors: list[str] = []
    warnings: list[str] = []


class AvailablePluginsResponse(BaseModel):
    """Response containing all available plugins from all marketplaces."""

    plugins: list[MarketplacePlugin]


class PluginValidateRequest(BaseModel):
    """Request to validate a plugin."""

    path: str


class PluginUpdateResponse(BaseModel):
    """Response from updating a plugin."""

    success: bool
    message: str
    stdout: str | None = None
    stderr: str | None = None


class PluginUpdateAllResponse(BaseModel):
    """Response from updating all plugins."""

    success: bool
    message: str
    updated_count: int
    failed_count: int
    results: list[PluginUpdateResponse] = []


# Hook Schemas

# Valid hook event types
VALID_HOOK_EVENTS = [
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PermissionRequest",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
]


class Hook(BaseModel):
    """Hook configuration."""

    id: str
    event: str  # PreToolUse, PostToolUse, PostToolUseFailure, Stop, SessionStart, SessionEnd, UserPromptSubmit, PermissionRequest, Notification, SubagentStart, SubagentStop, PreCompact
    matcher: str | None = None  # Tool matcher pattern (e.g., "Write(*.py)")
    type: str = "command"  # "command", "prompt", "agent", or "http"
    command: str | None = None  # Shell command to execute (for command type)
    prompt: str | None = None  # Prompt to append (for prompt/agent type)
    model: str | None = None  # Model to use (for agent type, e.g., "haiku")
    async_: bool | None = None  # Run in background (JSON field name: "async")
    statusMessage: str | None = None  # Custom spinner message
    once: bool | None = None  # Run only once per session
    timeout: int | None = None  # Timeout in seconds
    url: str | None = None  # URL for http-type hooks
    headers: dict[str, str] | None = None  # Headers for http-type hooks
    allowedEnvVars: list[str] | None = None  # Env vars for http-type hooks
    scope: str  # "user" or "project"

    model_config = ConfigDict(populate_by_name=True)


class HookCreate(BaseModel):
    """Schema for creating a hook."""

    event: str
    matcher: str | None = None
    type: str = "command"  # "command", "prompt", "agent", or "http"
    command: str | None = None
    prompt: str | None = None
    model: str | None = None  # For agent hooks
    async_: bool | None = None  # Run in background
    statusMessage: str | None = None  # Custom spinner message
    once: bool | None = None  # Run only once per session
    timeout: int | None = None
    url: str | None = None  # URL for http-type hooks
    headers: dict[str, str] | None = None  # Headers for http-type hooks
    allowedEnvVars: list[str] | None = None  # Env vars for http-type hooks
    scope: str  # "user" or "project"


class HookUpdate(BaseModel):
    """Schema for updating a hook."""

    event: str | None = None
    matcher: str | None = None
    type: str | None = None
    command: str | None = None
    prompt: str | None = None
    model: str | None = None
    async_: bool | None = None
    statusMessage: str | None = None
    once: bool | None = None
    timeout: int | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    allowedEnvVars: list[str] | None = None


class HookListResponse(BaseModel):
    """List of hooks."""

    hooks: list[Hook]


# Permission Schemas

# Valid permission modes
VALID_PERMISSION_MODES = [
    "default",
    "acceptEdits",
    "dontAsk",
    "plan",
]


class PermissionRule(BaseModel):
    """Permission rule configuration."""

    id: str
    type: str  # "allow", "deny", or "ask"
    pattern: str  # Tool(pattern), Tool:subcommand, WebFetch(domain:...), MCP(server:tool), Task(*), Skill(skill-name)
    scope: str  # "user" or "project"


class PermissionRuleCreate(BaseModel):
    """Schema for creating a permission rule."""

    type: str  # "allow", "deny", or "ask"
    pattern: str  # Tool(pattern), Tool:subcommand, WebFetch(domain:...), MCP(server:tool), Task(*), Skill(skill-name)
    scope: str  # "user" or "project"


class PermissionRuleUpdate(BaseModel):
    """Schema for updating a permission rule."""

    type: str | None = None
    pattern: str | None = None


class PermissionSettings(BaseModel):
    """Full permission settings including mode and directories."""

    defaultMode: str | None = "default"  # default/acceptEdits/dontAsk/plan
    additionalDirectories: list[str] | None = None  # Additional allowed directories
    disableBypassPermissionsMode: bool | None = False  # Disable bypass mode


class PermissionListResponse(BaseModel):
    """List of permission rules with settings."""

    rules: list[PermissionRule]
    settings: PermissionSettings | None = None


class PermissionSettingsUpdate(BaseModel):
    """Schema for updating permission settings."""

    defaultMode: str | None = None
    additionalDirectories: list[str] | None = None
    disableBypassPermissionsMode: bool | None = None


# Agent and Skill Schemas


class AgentHook(BaseModel):
    """Agent lifecycle hook."""

    type: str  # "command" or "prompt"
    command: str | None = None
    prompt: str | None = None


class Agent(BaseModel):
    """Agent configuration."""

    name: str
    scope: str  # "user" or "project"
    description: str | None = None
    tools: list[str] | None = None
    model: str | None = None
    prompt: str  # Full prompt content
    # Subagent management fields
    disallowed_tools: list[str] | None = None  # Tools to deny
    permission_mode: str | None = None  # default/acceptEdits/dontAsk/bypassPermissions/plan
    skills: list[str] | None = None  # Preload skills into context
    hooks: dict[str, list[AgentHook]] | None = None  # Lifecycle hooks scoped to subagent
    memory: str | None = None  # Persistent memory scope (user/project/local/none)


class AgentCreate(BaseModel):
    """Schema for creating an agent."""

    name: str
    scope: str  # "user" or "project"
    description: str | None = None
    tools: list[str] | None = None
    model: str | None = None
    prompt: str
    # Subagent management fields
    disallowed_tools: list[str] | None = None
    permission_mode: str | None = None
    skills: list[str] | None = None
    hooks: dict[str, list[AgentHook]] | None = None
    memory: str | None = None


class AgentUpdate(BaseModel):
    """Schema for updating an agent."""

    description: str | None = None
    tools: list[str] | None = None
    model: str | None = None
    prompt: str | None = None
    # Subagent management fields
    disallowed_tools: list[str] | None = None
    permission_mode: str | None = None
    skills: list[str] | None = None
    hooks: dict[str, list[AgentHook]] | None = None
    memory: str | None = None


class AgentListResponse(BaseModel):
    """List of agents."""

    agents: list[Agent]


class SkillDependency(BaseModel):
    """A single skill dependency."""

    kind: str  # "bin", "npm", "pip", "script"
    name: str  # Binary name, package name, or script path
    installed: bool = False  # Whether the dependency is currently satisfied
    version: str | None = None  # Required version (if specified)
    installed_version: str | None = None  # Currently installed version


class SkillDependencyStatus(BaseModel):
    """Dependency status report for a skill."""

    skill_name: str
    all_satisfied: bool
    dependencies: list[SkillDependency]
    has_install_script: bool = False
    install_script_path: str | None = None


class SkillInstallResult(BaseModel):
    """Result of installing skill dependencies."""

    success: bool
    message: str
    installed: list[str] = []  # Successfully installed deps
    failed: list[str] = []  # Failed deps
    logs: str = ""  # Combined stdout/stderr


class SkillSupportingFile(BaseModel):
    """A supporting file in a skill directory."""

    name: str
    path: str
    size_bytes: int
    is_script: bool = False


class SkillFrontmatter(BaseModel):
    """All known skill frontmatter fields."""

    # Identity
    name: str | None = None
    description: str | None = None
    version: str | None = None
    license: str | None = None

    # Execution context
    context: str | None = None  # "fork" to run in a subagent
    agent: str | None = None  # Subagent type: "Explore", "Plan", custom
    model: str | None = None  # Override model for this skill

    # Tool control
    allowed_tools: list[str] | None = None  # Tools available without permission

    # Visibility & invocability
    user_invocable: bool | None = None  # Show in / menu (default true)
    disable_model_invocation: bool | None = None  # Prevent auto-loading

    # UX
    argument_hint: str | None = None  # Autocomplete hint e.g. "[issue-number]"

    # Hooks
    hooks: dict | None = None  # Lifecycle hooks scoped to skill

    # Metadata (author, version, etc.)
    metadata: dict | None = None


class Skill(BaseModel):
    """Skill definition."""

    name: str
    description: str | None = None
    location: str  # "user", "project", or plugin path
    content: str | None = None  # Full markdown content (optional)
    # Full frontmatter (populated on detail view)
    frontmatter: SkillFrontmatter | None = None
    # Dependency info (populated on detail view)
    dependency_status: SkillDependencyStatus | None = None
    supporting_files: list[SkillSupportingFile] | None = None


class SkillListResponse(BaseModel):
    """List of skills."""

    skills: list[Skill]


class SkillUsageStat(BaseModel):
    """Usage count for a single skill."""

    skill: str
    count: int


class SkillStatsResponse(BaseModel):
    """Aggregated skill usage stats for a project."""

    stats: list[SkillUsageStat]


# Registry Skills (skills.sh)


class RegistrySkillResponse(BaseModel):
    """A skill from the skills.sh registry."""

    skill_id: str
    name: str
    source: str  # GitHub repo path (e.g. "vercel-labs/agent-skills")
    installs: int
    registry_id: str
    url: str  # skills.sh detail page URL
    github_url: str  # GitHub repo URL
    installed: bool = False  # Whether this skill is installed locally


class RegistrySearchResponse(BaseModel):
    """Response from registry search/browse."""

    skills: list[RegistrySkillResponse]
    total: int
    cached: bool = False


class RegistryInstallRequest(BaseModel):
    """Request to install a skill from the registry."""

    source: str  # GitHub repo path
    skill_names: list[str] | None = None  # Specific skills to install (None = all)
    global_install: bool = True  # User-level vs project-level


class RegistryInstallResponse(BaseModel):
    """Response from registry install."""

    success: bool
    message: str
    logs: str
    source: str
    skill_names: list[str] | None = None


# Backup Schemas


class BackupBase(BaseModel):
    """Base backup schema."""

    name: str
    description: str | None = None
    scope: str  # "full", "user", "project", "codex"


class BackupCreate(BackupBase):
    """Schema for creating a backup."""

    project_path: str | None = None  # Required for project/full scope
    project_id: int | None = None


class BackupResponse(BackupBase):
    """Schema for backup response."""

    id: int
    file_path: str
    project_id: int | None = None
    created_at: str
    size_bytes: int
    is_automatic: bool = False

    model_config = ConfigDict(from_attributes=True)


class BackupListResponse(BaseModel):
    """List of backups."""

    backups: list[BackupResponse]


class AutoBackupSettingsResponse(BaseModel):
    """Current automatic-backup schedule settings."""

    enabled: bool
    scope: str  # "user" or "full"
    project_path: str | None = None
    time_of_day: str  # "HH:MM"
    timezone: str
    retention_days: int
    last_run_at: str | None = None
    last_status: str | None = None
    last_backup_id: int | None = None


class AutoBackupSettingsUpdate(BaseModel):
    """Schema for updating automatic-backup settings."""

    enabled: bool | None = None
    scope: str | None = None  # "user" or "full"
    project_path: str | None = None
    time_of_day: str | None = None  # "HH:MM"
    timezone: str | None = None
    retention_days: int | None = None


class AutoBackupRunResult(BaseModel):
    """Result of triggering an automatic backup run."""

    success: bool
    message: str
    backup_id: int | None = None
    deleted_count: int = 0


class BackupContentsResponse(BaseModel):
    """Backup contents response."""

    files: list[str]


class RestoreRequest(BaseModel):
    """Schema for restore request."""

    project_path: str | None = None


class ExportRequest(BaseModel):
    """Schema for export request."""

    paths: list[str]
    name: str | None = "export"


class ExportResponse(BaseModel):
    """Schema for export response."""

    file_path: str
    size_bytes: int


# Output Style Schemas


class OutputStyle(BaseModel):
    """Output style configuration."""

    name: str
    scope: str  # "user" or "project"
    description: str | None = None
    keep_coding_instructions: bool = False
    content: str  # Markdown instructions


class OutputStyleCreate(BaseModel):
    """Schema for creating an output style."""

    name: str
    scope: str  # "user" or "project"
    description: str | None = None
    keep_coding_instructions: bool = False
    content: str


class OutputStyleUpdate(BaseModel):
    """Schema for updating an output style."""

    description: str | None = None
    keep_coding_instructions: bool | None = None
    content: str | None = None


class OutputStyleListResponse(BaseModel):
    """List of output styles."""

    output_styles: list[OutputStyle]


# Status Line Schemas


class StatusLineConfig(BaseModel):
    """Status line configuration."""

    type: str = "command"  # Currently only "command" is supported
    command: str | None = None  # Path to script
    padding: int | None = None  # Optional padding (0 = edge)
    enabled: bool = True
    script_content: str | None = None  # Current script file content


class StatusLineUpdate(BaseModel):
    """Schema for updating status line config."""

    type: str | None = None
    command: str | None = None
    padding: int | None = None
    enabled: bool | None = None


class StatusLinePreset(BaseModel):
    """Preset status line script."""

    id: str
    name: str
    description: str
    script: str


class StatusLinePresetsResponse(BaseModel):
    """List of available presets."""

    presets: list[StatusLinePreset]


class StatusLineApplyPresetRequest(BaseModel):
    """Request to apply a preset."""

    preset_id: str


class PowerlinePreset(BaseModel):
    """Powerline theme preset (uses npx command)."""

    id: str
    name: str
    description: str
    theme: str
    style: str
    command: str


class PowerlinePresetsResponse(BaseModel):
    """List of available powerline presets."""

    presets: list[PowerlinePreset]


class NodejsCheckResponse(BaseModel):
    """Response from Node.js availability check."""

    available: bool
    version: str | None = None


# Session Transcript Schemas


class ContentBlock(BaseModel):
    """A content block within a message."""

    type: str  # "text", "thinking", "tool_use", "tool_result", "image"
    text: str | None = None
    thinking: str | None = None
    name: str | None = None  # tool name for tool_use
    id: str | None = None
    input: dict[str, Any] | None = None
    content: Any | None = None  # tool_result content
    is_error: bool | None = None
    source: dict[str, str] | None = None  # for images


class SessionMessage(BaseModel):
    """A message in a conversation (user or assistant)."""

    type: str  # "user" or "assistant"
    timestamp: str
    content: list[ContentBlock]
    model: str | None = None  # Model used for this message
    usage: dict[str, Any] | None = None  # Token usage (can have nested structures)


class SessionConversation(BaseModel):
    """A conversation (user prompt + assistant responses)."""

    user_text: str  # Preview text from user prompt
    timestamp: str
    messages: list[SessionMessage]
    is_continuation: bool = False
    token_count: int | None = None


class SessionSummary(BaseModel):
    """Session metadata for list view."""

    id: str
    project_folder: str
    project_name: str
    summary: str
    modified_at: str
    size_bytes: int
    total_messages: int
    total_tool_calls: int


class ResumableSession(SessionSummary):
    """A session summary tagged with the worktree it belongs to, for the resume picker."""

    worktree_label: str


class ResumableSessionListResponse(BaseModel):
    """Aggregated resumable sessions across a project and its worktrees."""

    sessions: list[ResumableSession]


class SessionDetail(BaseModel):
    """Full session data with conversations."""

    id: str
    project_folder: str
    project_name: str
    conversations: list[SessionConversation]
    total_messages: int
    total_tool_calls: int
    total_tokens: int | None = None
    models_used: list[str] = []


class SessionProject(BaseModel):
    """Project grouping with session count."""

    folder: str
    name: str
    session_count: int
    most_recent: str


class SessionListResponse(BaseModel):
    """List of session summaries."""

    sessions: list[SessionSummary]
    total: int


class SessionProjectListResponse(BaseModel):
    """List of projects with session counts."""

    projects: list[SessionProject]
    total_sessions: int


class SessionDetailResponse(BaseModel):
    """Full session detail with pagination."""

    session: SessionDetail
    current_page: int
    total_pages: int
    prompts_per_page: int = 5


class SessionStatsResponse(BaseModel):
    """Dashboard session statistics."""

    total_sessions: int
    sessions_today: int
    sessions_this_week: int
    most_active_project: str | None = None
    total_messages: int


# Usage Tracking Schemas


class TokenCounts(BaseModel):
    """Token counts by type."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class ModelBreakdown(BaseModel):
    """Model-specific usage breakdown."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost: float = 0.0


class DailyUsage(BaseModel):
    """Daily usage aggregation."""

    date: str  # YYYY-MM-DD
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_cost: float
    models_used: list[str]
    model_breakdowns: list[ModelBreakdown]
    project: str | None = None


class SessionUsage(BaseModel):
    """Session-based usage aggregation."""

    session_id: str
    project_path: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_cost: float
    last_activity: str  # YYYY-MM-DD
    versions: list[str]
    models_used: list[str]
    model_breakdowns: list[ModelBreakdown]


class MonthlyUsage(BaseModel):
    """Monthly usage aggregation."""

    month: str  # YYYY-MM
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_cost: float
    models_used: list[str]
    model_breakdowns: list[ModelBreakdown]
    project: str | None = None


class SessionBlock(BaseModel):
    """5-hour billing block usage."""

    id: str  # ISO timestamp of block start
    start_time: str  # ISO timestamp
    end_time: str  # ISO timestamp (start + 5 hours)
    actual_end_time: str | None = None  # Last activity in block
    is_active: bool
    is_gap: bool = False
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float
    models: list[str]
    # Projections for active blocks
    burn_rate_tokens_per_minute: float | None = None
    burn_rate_cost_per_hour: float | None = None
    projected_total_tokens: int | None = None
    projected_total_cost: float | None = None
    remaining_minutes: int | None = None


class UsageSummary(BaseModel):
    """Overall usage statistics."""

    total_cost: float
    total_input_tokens: int
    total_output_tokens: int
    total_cache_creation_tokens: int
    total_cache_read_tokens: int
    total_tokens: int
    project_count: int
    session_count: int
    models_used: list[str]
    date_range_start: str | None = None
    date_range_end: str | None = None


class DailyUsageListResponse(BaseModel):
    """List of daily usage data."""

    data: list[DailyUsage]
    totals: TokenCounts
    total_cost: float


class SessionUsageListResponse(BaseModel):
    """List of session usage data."""

    data: list[SessionUsage]
    totals: TokenCounts
    total_cost: float
    total: int


class MonthlyUsageListResponse(BaseModel):
    """List of monthly usage data."""

    data: list[MonthlyUsage]
    totals: TokenCounts
    total_cost: float


class BlockUsageListResponse(BaseModel):
    """List of billing block usage data."""

    data: list[SessionBlock]
    active_block: SessionBlock | None = None
    totals: TokenCounts
    total_cost: float


class UsageSummaryResponse(BaseModel):
    """Usage summary response."""

    summary: UsageSummary


# Settings Update Schemas


class SettingsUpdateRequest(BaseModel):
    """Schema for updating settings."""

    scope: str  # "user", "project", or "local"
    settings: dict[str, Any]
    project_path: str | None = None  # Required for project/local scope


class SettingsUpdateResponse(BaseModel):
    """Response from settings update."""

    success: bool
    message: str
    path: str  # File path that was updated
    migrated_patterns: list[dict[str, str]] | None = None
    removed_patterns: list[dict[str, str]] | None = None


class SettingsValidationRequest(BaseModel):
    """Schema for validating settings without saving."""

    settings: dict[str, Any]


class PatternIssue(BaseModel):
    """A single pattern validation issue."""

    pattern: str
    category: str
    error: str
    suggestion: str | None = None


class SettingsValidationResponse(BaseModel):
    """Response from settings validation."""

    valid: bool
    issues: list[PatternIssue] = []


# Backup Manifest & Dependency Schemas


class BackupSkillDependency(BaseModel):
    """Dependency detected in a skill."""

    kind: str  # "npm", "pip", "bin", "script"
    name: str
    version: str | None = None


class BackupSkillInfo(BaseModel):
    """Skill information in backup manifest."""

    name: str
    path: str
    has_package_json: bool = False
    has_requirements_txt: bool = False
    has_install_script: bool = False
    dependencies: list[BackupSkillDependency] = []


class BackupPluginInfo(BaseModel):
    """Plugin information in backup manifest."""

    name: str
    version: str | None = None
    source: str | None = None
    install_command: str | None = None
    marketplace: str | None = None


class BackupMCPServerInfo(BaseModel):
    """MCP server information in backup manifest."""

    name: str
    type: str  # "stdio", "http", "sse"
    scope: str
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    requires_npm_install: bool = False


class BackupManifestContents(BaseModel):
    """Contents tracked in backup manifest."""

    files: list[str] = []
    skills: list[BackupSkillInfo] = []
    plugins: list[BackupPluginInfo] = []
    mcp_servers: list[BackupMCPServerInfo] = []
    agents: list[str] = []
    commands: list[str] = []
    provider_inventory: dict[str, Any] = {}
    backup_policy: dict[str, Any] = {}


class BackupManifest(BaseModel):
    """Full backup manifest stored in the backup zip."""

    version: str = "1.0"
    created_at: str
    claude_code_version: str | None = None
    platform: str  # "linux", "darwin", "win32"
    scope: str  # "full", "user", "project"
    contents: BackupManifestContents


class RestoreOptions(BaseModel):
    """Options for restore operation."""

    selective_restore: list[str] | None = None  # Specific paths to restore
    install_dependencies: bool = False  # Auto-install deps after restore
    dry_run: bool = False  # Preview only, don't actually restore
    skip_plugins: bool = False
    skip_skills: bool = False
    skip_mcp_servers: bool = False


class DependencyInstallStatus(BaseModel):
    """Status of a single dependency installation."""

    name: str
    kind: str  # "npm", "pip", "plugin", "skill"
    success: bool
    message: str | None = None


class RestorePlanDependency(BaseModel):
    """A dependency that needs to be installed during restore."""

    kind: str  # "npm", "pip", "plugin", "mcp_npm"
    name: str
    version: str | None = None
    source: str | None = None  # Skill/plugin name requiring this
    install_command: str | None = None


class RestorePlanWarning(BaseModel):
    """Warning about restore compatibility."""

    type: str  # "platform", "version", "missing_tool"
    message: str
    severity: str = "warning"  # "warning", "error"


class RestorePlan(BaseModel):
    """Plan showing what will be restored and dependencies needed."""

    backup_id: int
    backup_name: str
    created_at: str
    scope: str
    platform_current: str
    platform_backup: str
    platform_compatible: bool

    # What will be restored
    files_to_restore: list[str] = []
    skills_to_restore: list[BackupSkillInfo] = []
    plugins_to_restore: list[BackupPluginInfo] = []
    mcp_servers_to_restore: list[BackupMCPServerInfo] = []

    # Dependencies needed
    dependencies: list[RestorePlanDependency] = []
    has_dependencies: bool = False

    # Warnings
    warnings: list[RestorePlanWarning] = []

    # Manual steps
    manual_steps: list[str] = []


class RestoreResult(BaseModel):
    """Result of restore operation."""

    success: bool
    message: str
    files_restored: int = 0
    files_skipped: int = 0
    dry_run: bool = False
    dependency_results: list[DependencyInstallStatus] = []
    manual_steps: list[str] = []


class DependencyInstallRequest(BaseModel):
    """Request to install dependencies from a backup."""

    install_npm: bool = True
    install_pip: bool = True
    install_plugins: bool = True
    skill_names: list[str] | None = None  # Specific skills to install deps for
    plugin_names: list[str] | None = None  # Specific plugins to reinstall


class DependencyInstallResult(BaseModel):
    """Result of dependency installation."""

    success: bool
    message: str
    installed: list[DependencyInstallStatus] = []
    failed: list[DependencyInstallStatus] = []
    logs: str = ""


# Context Window Analysis Schemas


class ContextSnapshot(BaseModel):
    """One turn's context window state."""

    turn_number: int
    timestamp: str
    total_context_tokens: int
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    model: str
    context_percentage: float  # 0-100


class ContentCategory(BaseModel):
    """Content type breakdown."""

    category: str  # "user_messages", "assistant_messages", "tool_results", "tool_calls", "thinking"
    estimated_chars: int
    estimated_tokens: int
    percentage: float


class FileConsumption(BaseModel):
    """File read consumption data."""

    file_path: str
    read_count: int
    total_chars: int
    estimated_tokens: int


class ToolConsumption(BaseModel):
    """Per-tool aggregate usage within a session."""

    tool_name: str
    call_count: int
    total_result_chars: int
    total_result_tokens: int
    avg_result_tokens: int


class CacheEfficiency(BaseModel):
    """Cache hit/miss breakdown."""

    total_cache_read: int
    total_cache_creation: int
    total_uncached: int
    hit_ratio: float  # 0-1


class ContextCategoryItem(BaseModel):
    """Single item within a category (e.g., one MCP tool, one memory file)."""

    name: str
    estimated_tokens: int


class ContextCompositionCategory(BaseModel):
    """One category in the context composition breakdown."""

    category: str  # "System Prompt", "MCP Tools", etc.
    estimated_tokens: int
    percentage: float
    color: str  # Hex color for chart
    items: list[ContextCategoryItem] | None = None


class ContextComposition(BaseModel):
    """Full context composition matching /context CLI output."""

    categories: list[ContextCompositionCategory]
    total_tokens: int
    context_limit: int
    model: str


class ContextAnalysis(BaseModel):
    """Full context analysis for a session."""

    session_id: str
    project_folder: str
    project_name: str
    model: str
    current_context_tokens: int
    max_context_tokens: int
    context_percentage: float
    snapshots: list[ContextSnapshot]
    content_categories: list[ContentCategory]
    file_consumptions: list[FileConsumption]
    tool_consumptions: list[ToolConsumption]
    cache_efficiency: CacheEfficiency
    avg_tokens_per_turn: int
    estimated_turns_remaining: int
    context_zone: str  # "green", "yellow", "orange", "red"
    total_turns: int
    composition: ContextComposition | None = None


class ContextAnalysisResponse(BaseModel):
    """Response wrapper for context analysis."""

    analysis: ContextAnalysis


class ActiveSessionContext(BaseModel):
    """Lightweight context info for an active/recent session."""

    session_id: str
    project_folder: str
    project_name: str
    model: str
    context_percentage: float
    current_context_tokens: int
    max_context_tokens: int
    is_active: bool
    last_activity: str


class ActiveSessionsResponse(BaseModel):
    """List of active sessions with context info."""

    sessions: list[ActiveSessionContext]


# Plan History Browser Schemas


class PlanSummary(BaseModel):
    """Summary of a plan for list view."""

    filename: str
    slug: str
    project_key: str
    title: str
    excerpt: str
    modified_at: str
    size_bytes: int


class PlanLinkedSession(BaseModel):
    """Session linked to a plan via slug."""

    session_id: str
    project_folder: str
    project_name: str
    git_branch: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None


class PlanDetail(BaseModel):
    """Full plan detail including content and linked sessions."""

    filename: str
    slug: str
    project_key: str
    title: str
    content: str
    created_at: str | None = None
    modified_at: str
    size_bytes: int
    headings: list[str]
    code_block_count: int
    table_count: int
    linked_sessions: list[PlanLinkedSession]


class PlanCreate(BaseModel):
    """Payload for creating a new plan."""

    filename: str = Field(..., min_length=1)
    content: str


class PlanUpdate(BaseModel):
    """Payload for updating an existing plan's content."""

    content: str


class PlanSearchResult(BaseModel):
    """Plan matching a search query."""

    filename: str
    slug: str
    title: str
    matches: list[str]
    modified_at: str


class PlanListResponse(BaseModel):
    """List of plan summaries."""

    plans: list[PlanSummary]
    total: int


class PlanDetailResponse(BaseModel):
    """Single plan detail response."""

    plan: PlanDetail


class PlanSearchResponse(BaseModel):
    """Plan search results."""

    results: list[PlanSearchResult]
    query: str
    total: int


class PlanStatsResponse(BaseModel):
    """Plan statistics for dashboard."""

    total_plans: int
    oldest_date: str | None = None
    newest_date: str | None = None
    total_size_bytes: int


# MCP Registry Schemas


class MCPRegistryInstallRequest(BaseModel):
    """Request to install an MCP server from the registry."""

    server_name: str  # User-chosen config name (e.g., "github")
    scope: str  # "user" or "project"
    # Package install fields (mutually exclusive with remote_*)
    package_registry_type: str | None = None  # "npm", "pypi", "oci"
    package_identifier: str | None = None
    package_version: str | None = None
    package_runtime_hint: str | None = None
    package_arguments: dict[str, str] | None = None
    # Remote install fields
    remote_type: str | None = None  # "streamable-http", "sse"
    remote_url: str | None = None
    remote_headers: dict[str, str] | None = None
    # Shared
    env_values: dict[str, str] | None = None


class MCPRegistryInstallResponse(BaseModel):
    """Response from MCP registry install."""

    success: bool
    server_name: str
    config: dict[str, Any]
    scope: str


# Presence Dashboard Schemas


class PresenceEventIn(BaseModel):
    """Incoming webhook payload from Claude Code HTTP hooks."""

    session_id: str
    hook_event_name: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    message: str | None = None
    user_prompt: str | None = None
    cwd: str | None = None
    transcript_path: str | None = None
    permission_mode: str | None = None
    tmux_pane: str | None = None


class PresenceSessionResponse(BaseModel):
    """Single session state for API/WebSocket."""

    session_id: str
    label: str | None = None
    project_path: str | None = None
    tmux_pane: str | None = None
    status: str = "active"
    status_text: str | None = None
    last_narrative: str | None = None
    last_narrative_at: str | None = None
    modified_files: list[str | dict] | None = None
    last_user_prompt: str | None = None
    last_command: str | None = None
    last_command_exit: int | None = None
    activity_buckets: list[int] | None = None
    total_events: int = 0
    error_count: int = 0
    started_at: str
    last_event_at: str
    ended_at: str | None = None


class PresenceSessionListResponse(BaseModel):
    """List of presence sessions with totals."""

    sessions: list[PresenceSessionResponse]
    total: int = 0
    active: int = 0
    error: int = 0


class PresenceSessionUpdate(BaseModel):
    """Label update request."""

    label: str


class PresenceConfigSnippet(BaseModel):
    """Generated setup snippet."""

    snippet: dict[str, Any]
    instructions: str


InstanceAccent = Literal["blue", "green", "purple", "orange", "red", "pink", "cyan", "slate"]


class InstanceIdentity(BaseModel):
    """Runtime identity for the Claude Cockpit backend instance."""

    id: str
    name: str
    hostname: str
    short_hostname: str
    accent: InstanceAccent
    started_at: datetime


class SystemStatusResponse(BaseModel):
    """System status for header indicators."""

    claude_code_version: str | None = None
    active_sessions: int = 0
    providers: dict[str, Any] = Field(default_factory=dict)
    scheduling_hooks_installed: bool = False
    instance: InstanceIdentity | None = None


# Shared generic response models


class MessageResponse(BaseModel):
    """Generic message-only response for simple success confirmations."""

    message: str


class ScopedSettingsResponse(BaseModel):
    """Settings for a single, non-merged scope."""

    settings: dict[str, Any]
    scope: str


class AllScopedSettingsResponse(BaseModel):
    """Settings from every scope, kept separate (not merged)."""

    scopes: dict[str, dict[str, Any]]


class ResolvedConfigEntry(BaseModel):
    """Effective value of one settings key plus its source scope."""

    effective_value: Any
    source_scope: str | None
    values_by_scope: dict[str, Any]


class ScopeConfigDetail(BaseModel):
    """Per-scope settings plus file metadata."""

    settings: dict[str, Any]
    path: str | None
    exists: bool
    readonly: bool


class ResolvedConfigScopes(BaseModel):
    """File metadata for each settings scope."""

    managed: ScopeConfigDetail
    user: ScopeConfigDetail
    project: ScopeConfigDetail
    local: ScopeConfigDetail


class ResolvedConfigResponse(BaseModel):
    """Resolved configuration with effective values and their source scopes."""

    resolved: dict[str, ResolvedConfigEntry]
    scopes: ResolvedConfigScopes


class DirectoryBrowseResponse(BaseModel):
    """Subdirectories of a browsed path."""

    path: str
    parent: str | None
    directories: list[str]


class ProjectConfigResponse(BaseModel):
    """A project's details plus its merged configuration."""

    project: ProjectResponse
    config: MergedConfig


class BridgeAttachmentResponse(BaseModel):
    id: int
    target: str
    session_name: str | None = None
    cli: str | None = None
    original_filename: str | None = None
    mime_type: str
    size_bytes: int
    sha256: str
    agent_path: str
    prompt_text: str
    created_by: str | None = None
    created_at: datetime
    expires_at: datetime | None = None


class BridgeAttachmentListResponse(BaseModel):
    attachments: list[BridgeAttachmentResponse] = Field(default_factory=list)


class BridgeAttachmentPasteRequest(BaseModel):
    submit: bool = False
    prefix: str = ""
    suffix: str = ""
    require_interactive_relay: bool = False


class BridgeAttachmentPasteResponse(BaseModel):
    pasted: bool
    submitted: bool
    target: str


class BridgeAttachmentDeleteResponse(BaseModel):
    deleted: bool
    target: str
    attachment_id: int


# ---------------------------------------------------------------------------
# Blueprint CRUD schemas
# ---------------------------------------------------------------------------
#
# The Blueprint / BlueprintSettings / BlueprintSkill / BlueprintAgent models
# are imported from app.services.blueprint above so the API contract stays
# in lock-step with the service model. The wrappers below only add the
# request/response shapes the API needs on top of those.


class BlueprintCreate(BaseModel):
    """Request body for `POST /api/v1/blueprints`.

    The `name` is the storage key — it must be unique across the store and
    follow the slug rules enforced by `BlueprintStore.validate_name`. The
    service layer re-runs validation so an API client cannot sneak past it.
    """

    name: str
    description: str | None = None
    settings: BlueprintSettings | None = None
    skills: list[BlueprintSkill] = Field(default_factory=list)
    agents: list[BlueprintAgent] = Field(default_factory=list)
    statusline: str | None = None
    output_style: str | None = None
    claudemd: str | None = None


class BlueprintUpdate(BaseModel):
    """Request body for `PUT /api/v1/blueprints/{name}`.

    Every field except `name` is optional; omitted fields are left as-is on
    the stored blueprint. Pass `null` to clear a nullable field (e.g.
    `claudemd`), and `[]` to clear a list field (e.g. `skills`).
    """

    description: str | None = None
    settings: BlueprintSettings | None = None
    skills: list[BlueprintSkill] | None = None
    agents: list[BlueprintAgent] | None = None
    statusline: str | None = None
    output_style: str | None = None
    claudemd: str | None = None
    # `null` = "leave alone", explicit value = "set to this"
    # `subdirs` is omitted from update — it's a structural choice made at
    # create time. Editing it post-hoc has surprising blast radius
    # (rewriting settings.json's subdirs list would invalidate any files
    # the user dropped in), so we make it immutable on the wire.
    model_config = ConfigDict(extra="forbid")


class BlueprintListResponse(BaseModel):
    """`GET /api/v1/blueprints` response."""

    blueprints: list[Blueprint]


class BlueprintApplyRequest(BaseModel):
    """`POST /api/v1/blueprints/{name}/apply` request body."""

    project_path: str
    force: bool = False


class BlueprintAuditWrittenFile(BaseModel):
    """One entry in `BlueprintApplyResponse.written_files`."""

    path: str  # relative to project root, e.g. ".claude/skills/foo/SKILL.md"


class BlueprintApplyResponse(BaseModel):
    """`POST /api/v1/blueprints/{name}/apply` response."""

    blueprint_name: str
    project_path: str
    written_files: list[str] = Field(default_factory=list)
    created_dirs: list[str] = Field(default_factory=list)
    applied_skills: list[str] = Field(default_factory=list)
    applied_agents: list[str] = Field(default_factory=list)
    skipped_existing: bool = False


# ---------------------------------------------------------------------------
# CI-template schemas
# ---------------------------------------------------------------------------


class CITemplateParameterInfo(BaseModel):
    """One parametric knob exposed by a CI profile.

    Surfaces the parameters a template declares so the UI / docs / REST client
    can render a form before invoking `POST /api/v1/ci/templates/{profile}/apply`.
    """

    name: str
    default: str | None = None


class CITemplateInfo(BaseModel):
    """`GET /api/v1/ci/templates` — one entry in the profile catalog.

    Mirrors `app.services.ci_templates.CITemplateInfo` (a frozen dataclass)
    with Pydantic-typed fields so FastAPI can serialise it directly. The
    dataclass stays the source of truth; the API layer maps it.
    """

    name: str
    description: str
    filename: str
    parameters: list[CITemplateParameterInfo] = Field(default_factory=list)


class CITemplateListResponse(BaseModel):
    """`GET /api/v1/ci/templates` response."""

    templates: list[CITemplateInfo]


class CITemplateApplyRequest(BaseModel):
    """`POST /api/v1/ci/templates/{profile}/apply` request body.

    `parameters` is a free-form `key=value` map (the wire shape is JSON object
    with string keys); missing keys fall back to the profile's declared
    defaults — same convention as the in-process `CITemplateService.apply`.
    """

    project_path: str
    force: bool = False
    parameters: dict[str, str] = Field(default_factory=dict)


class CITemplateApplyResponse(BaseModel):
    """`POST /api/v1/ci/templates/{profile}/apply` response."""

    profile: str
    project_path: str
    written_file: str | None = None
    skipped_existing: bool = False
    force: bool = False
