# Changelog

All notable changes to Agent Cockpit will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **BREAKING — MCP server rename**: the management MCP server is now registered as `agent-cockpit` (was `claude-cockpit`) as part of the rebrand to Agent Cockpit. Migration: rename the `claude-cockpit` key to `agent-cockpit` in any `.mcp.json` that references this server (the separate `cockpit-kanban` server is unchanged).

### Added
- **Models**: Claude Sonnet 5 is now selectable as a model, with correct context-window and usage-cost calculation
- **Kanban**: Dispatch board for handing work to Claude Code/Codex agents — cards move through columns as agents claim, work, and hand them off, with per-agent performance stats, impediment reporting, and scheduling a card for a future time
- **Scheduled Messages**: Schedule a message for future delivery into a running or resumable tmux session, including auto-resume of sessions that hit their rate limit
- **Context**: Session context-window visualizer showing usage over time, cache efficiency, and content breakdown
- **Presence**: At-a-glance view of which tmux panes are active across agent sessions
- **Agent Performance (APM)**: Per-agent throughput and reliability tracking

### Fixed
- **Security**: Session transcript endpoints (`/api/v1/sessions/...`) now reject path-traversal sequences in `project_folder`/`session_id` instead of resolving them against the filesystem

## [1.3.0] - 2026-06-08

### Added
- **Codex CLI support**: Provider-aware Codex support is now stable enough for everyday use
  - Agent Bridge discovers mixed Claude Code and Codex tmux sessions
  - Codex sessions can be spawned, resumed, forked, attached to, and killed from the UI
  - Provider switcher keeps Claude Code and Codex surfaces separate instead of showing unsupported pages
- **Codex Config**: Safe TOML editor for Codex settings
  - Structured General and Runtime cards for model, profile, reasoning effort, sandbox mode, approval policy, search, strict config, and alternate screen behavior
  - Dropdowns for known Codex enum values while keeping open-ended fields editable
  - Help tooltips for documented settings and feature flags
  - Feature flag inventory from `codex features list`, including editable overrides for flags such as goals, memories, hooks, multi-agent, shell tool, and network proxy
  - Profile diagnostics for active/default profile resolution, profile files, overrides, missing references, and malformed profiles
- **Codex MCP and Plugins**: Provider inventory and safe CLI-backed mutations
  - MCP inventory from `codex mcp list --json`
  - MCP add/remove through the Codex CLI with validation
  - Plugin inventory from `codex plugin list`
  - Plugin install/remove where the installed Codex CLI exposes safe commands
- **Codex Backup Export**: Redacted export-only backups for Codex config, profile files, rules, and provider inventory metadata
- **Projects**: Project discovery is easier, with directory browsing support when adding project paths

### Changed
- **Provider model**: Provider status, capabilities, diagnostics, and normalized errors now drive the UI for Claude Code and Codex CLI.
- **Documentation**: README and VitePress docs now describe the stable Codex support surface, the remaining provider boundaries, and the release-ready dependency updates.
- **Frontend toolchain**: Updated TypeScript to 6.0.3, `@vitejs/plugin-react` to 5.1.4, ESLint tooling, React DOM, PostCSS, Tailwind Merge, and Node types.

### Security
- **Codex privacy boundary**: Codex auth, history, model cache, SQLite state, prompt text, and raw cache payloads remain excluded from raw viewers and backups.
- **Codex restore policy**: Automatic Codex restore is refused because exports intentionally omit provider-owned local state.

## [1.2.0] - 2026-04-22

### Added
- **CC Bridge**: Live terminal bridge to Claude Code sessions running in tmux
  - Multi-terminal grid layout supporting up to 4 simultaneous panes (auto-layout: 1, 2-column, or 2x2 grid)
  - Per-pane read-only/interactive mode toggle, fullscreen, attach/detach, and close controls
  - Active terminal focus indicator — green glow on the focused pane
  - Session discovery via `tmux list-panes` with auto-refresh polling
  - Spawn new Claude Code sessions (plain, worktree, or resume mode) from the UI
  - Kill sessions with optional worktree cleanup
  - WebSocket-based PTY relay with xterm.js (WebGL rendering, web links)
- **Projects**: Discover projects from `~/.claude/projects/` session history
- **Dashboard**: Cache stats in context to avoid re-fetching on navigation
- **Documentation**: VitePress documentation site with guide, features, and API reference

### Fixed
- **CC Bridge**: Prevent orphaned `tmux attach-session` processes from accumulating on server reload/crash via `PR_SET_PDEATHSIG` and startup cleanup
- **CC Bridge**: Fix terminal not rendering in React StrictMode due to race condition in async attach flow

## [1.1.0] - 2026-03-03

### Added
- **CC Bridge**: Live terminal bridge to Claude Code sessions running in tmux
  - Multi-terminal grid layout supporting up to 4 simultaneous panes (auto-layout: 1, 2-column, or 2x2 grid)
  - Per-pane read-only/interactive mode toggle, fullscreen, attach/detach, and close controls
  - Active terminal focus indicator — green glow on the focused pane
  - Session discovery via `tmux list-panes` with auto-refresh polling
  - Spawn new Claude Code sessions (plain, worktree, or resume mode) from the UI
  - Kill sessions with optional worktree cleanup
  - WebSocket-based PTY relay with xterm.js (WebGL rendering, web links)
- **Projects**: Discover projects from `~/.claude/projects/` session history
- **Dashboard**: Cache stats in context to avoid re-fetching on navigation
- **Documentation**: VitePress documentation site with guide, features, and API reference

### Fixed
- **CC Bridge**: Prevent orphaned `tmux attach-session` processes from accumulating on server reload/crash via `PR_SET_PDEATHSIG` and startup cleanup
- **CC Bridge**: Fix terminal not rendering in React StrictMode due to race condition in async attach flow

## [1.0.0] - 2026-01-22

### Added
- Initial release of Claude Deck
- **Dashboard**: Overview of Claude Code configuration status and usage statistics
- **MCP Server Management**: Add, edit, remove, and configure MCP servers (global and project-scoped)
- **Commands Management**: Create and manage custom slash commands with argument support
- **Plugins Management**: Install, configure, and manage Claude Code plugins
- **Hooks Management**: Configure pre/post hooks for various Claude Code events
- **Permissions Management**: Manage allowed and denied permissions for tools
- **Backup & Restore**: Full backup and restore functionality for all configurations
- **Project Management**: Support for project-specific configurations
- **CLI Executor**: Execute Claude CLI commands from the web interface
- **Usage Tracking**: Track and visualize API usage and costs

### Technical
- FastAPI backend with async SQLAlchemy and SQLite
- React 18 frontend with TypeScript, Vite, and shadcn/ui
- RESTful API at `/api/v1/`
- CORS configured for local development

[Unreleased]: https://github.com/adrirubio/claude-deck/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/adrirubio/claude-deck/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/adrirubio/claude-deck/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/adrirubio/claude-deck/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/adrirubio/claude-deck/releases/tag/v1.0.0
