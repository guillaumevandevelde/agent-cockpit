import type { AgenticCliId, VendorId } from '@/types/providers'

export interface AgentSession {
  /** CLI tool running the session (claude-code, codex-cli, …). */
  cli: AgenticCliId
  /** Human-readable CLI label (e.g. "Claude Code", "Codex"). */
  cli_display_name: string
  /** Vendor / subscription the session was started with (anthropic, minimax, …). */
  provider: VendorId
  /** Human-readable vendor label (e.g. "Anthropic", "MiniMax"). */
  provider_display_name: string
  tmux_target: string
  session_name: string
  window_name: string
  pane_id: string
  cwd: string
  pid: string
  status: string
  /** Kanban card id that dispatched this session, if any. Lets the Agent Bridge
   *  surface a "view kanban card" affordance that deep-links to
   *  `/kanban?card=<id>`. Populated by the backend enrichment in
   *  `app.services.runs.discovery.enrich_sessions_with_cards`. */
  card_id?: string
  /** Project key the card above lives on; needed so the deep-link can pick
   *  the right kanban board. Mirrors the kanban-card API's `project_key`. */
  card_project_key?: string
}

export type CCSession = AgentSession

export type LeaderNavigationDirection = 'prev' | 'next' | number

export interface AgentSessionsResponse {
  sessions: AgentSession[]
  count: number
}

export type CCSessionsResponse = AgentSessionsResponse

export interface CCPreviewResponse {
  target: string
  content: string
}

export interface GitStatusResponse {
  is_git_repo: boolean
  branch: string | null
  detached: boolean
  upstream: string | null
  ahead: number
  behind: number
  dirty: boolean
}

export interface CCTokenResponse {
  token: string
}

export interface SpawnSessionRequest {
  cli?: AgenticCliId
  directory: string
  session_name?: string
  mode: 'plain' | 'worktree' | 'resume' | 'fork'
  worktree_name?: string
  session_id?: string
  project_folder?: string
  skip_permissions?: boolean
  prompt?: string
  model?: string
  profile?: string
  profile_v2?: string
  sandbox?: string
  approval_policy?: string
  search?: boolean
  no_alt_screen?: boolean
  dangerously_bypass_approvals_and_sandbox?: boolean
  use_last?: boolean
  provider?: 'anthropic' | 'bedrock' | 'minimax' | 'anthropic-compatible'
  aws_region?: string
  aws_profile?: string
  bedrock_model?: string
  minimax_base_url?: string
  endpoint_name?: string
  host_id?: number
  agent?: string
  context_tier?: string
  reasoning_effort?: string
  plan?: boolean
  remote?: boolean
  allow_all?: boolean
  no_ask_user?: boolean
}

export interface SpawnSessionResponse {
  tmux_target: string
  session_name: string
  /** The git branch name of the worktree, after sanitization (worktree mode). */
  worktree_name?: string | null
  /** True when the supplied worktree name had to be changed to be a valid git ref. */
  worktree_name_adjusted?: boolean
}

export interface BulkResumeRequest {
  cli?: AgenticCliId
  directory?: string
  sessions: { session_id: string; project_folder: string }[]
  skip_permissions?: boolean
  // Mirrors ``SpawnSessionRequest``: ``'anthropic-compatible'`` was missing,
  // which made the resume batch silently fall back to plain Anthropic when
  // the user picked "Custom endpoint" in the NewSessionDialog
  // (kaart 7ab0fc0038c…).
  provider?: 'anthropic' | 'bedrock' | 'minimax' | 'anthropic-compatible'
  aws_region?: string
  aws_profile?: string
  bedrock_model?: string
  minimax_base_url?: string
  endpoint_name?: string
}

export interface BulkResumeResult {
  session_id: string
  project_folder: string
  ok: boolean
  tmux_target?: string | null
  session_name?: string | null
  error?: string | null
}

export interface BulkResumeResponse {
  results: BulkResumeResult[]
  spawned: number
  failed: number
}

export interface ProviderStatusResponse {
  configured: boolean
}

export interface EndpointResponse {
  name: string
  base_url: string
  model: string
  credential_name: string | null
  credential_configured: boolean
}

export interface EndpointListResponse {
  endpoints: EndpointResponse[]
}

export interface EndpointUpsertRequest {
  name: string
  base_url: string
  model: string
  credential_name?: string | null
}

export interface KillSessionResponse {
  killed: boolean
  error?: string
}

export interface BridgeAttachment {
  id: number
  target: string
  session_name?: string | null
  provider?: VendorId | null
  original_filename?: string | null
  mime_type: string
  size_bytes: number
  sha256: string
  agent_path: string
  prompt_text: string
  created_by?: string | null
  created_at: string
  expires_at?: string | null
}

export interface BridgeAttachmentListResponse {
  attachments: BridgeAttachment[]
}

export interface BridgeAttachmentPasteRequest {
  submit?: boolean
  prefix?: string
  suffix?: string
  require_interactive_relay?: boolean
}

export interface BridgeAttachmentPasteResponse {
  pasted: boolean
  submitted: boolean
  target: string
}

export interface BridgeAttachmentDeleteResponse {
  deleted: boolean
  target: string
  attachment_id: number
}

export interface RenameSessionResponse {
  renamed: boolean
  session_name: string
  tmux_target: string
}

// ── Run Group types ──────────────────────────────────────────────────────

export interface RunGroupMember {
  session_name: string
  pane_id?: string | null
  tmux_target: string
}

export interface RunGroup {
  team_id: string
  name: string
  cli: AgenticCliId
  cli_display_name?: string
  /** Vendor / subscription inherited from the group's lead session. */
  provider?: VendorId
  /** Human-readable vendor label inherited from the lead. */
  provider_display_name?: string
  cwd: string
  is_auto_detected: boolean
  lead: AgentSession | null
  members: AgentSession[]
}

export interface RunGroupsResponse {
  teams: RunGroup[]
  ungrouped: AgentSession[]
  total_teams: number
  total_sessions: number
}

export interface CreateGroupRequest {
  name: string
  cli?: AgenticCliId
  cwd?: string
  lead_session_name?: string | null
  members: { session_name: string; pane_id?: string | null; tmux_target: string }[]
}

export interface CreateGroupResponse {
  team_id: string
  name: string
  cli: AgenticCliId
  cwd: string
  is_auto_detected: boolean
  members: RunGroupMember[]
}
