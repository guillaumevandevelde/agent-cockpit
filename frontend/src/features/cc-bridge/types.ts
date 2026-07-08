import type { AgentProviderId } from '@/types/providers'

export interface AgentSession {
  provider: AgentProviderId
  provider_display_name: string
  tmux_target: string
  session_name: string
  window_name: string
  pane_id: string
  cwd: string
  pid: string
  status: string
}

export type CCSession = AgentSession

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
  provider?: AgentProviderId
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
  platform?: 'anthropic' | 'bedrock' | 'minimax'
  aws_region?: string
  aws_profile?: string
  bedrock_model?: string
  minimax_base_url?: string
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
  provider?: AgentProviderId
  directory?: string
  sessions: { session_id: string; project_folder: string }[]
  skip_permissions?: boolean
  platform?: 'anthropic' | 'bedrock' | 'minimax'
  aws_region?: string
  aws_profile?: string
  bedrock_model?: string
  minimax_base_url?: string
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

export interface PlatformStatusResponse {
  configured: boolean
}

export interface KillSessionResponse {
  killed: boolean
  error?: string
}

export interface BridgeAttachment {
  id: number
  target: string
  session_name?: string | null
  provider?: string | null
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

// ── Agent Team types ─────────────────────────────────────────────────────

export interface AgentTeamMember {
  session_name: string
  pane_id?: string | null
  tmux_target: string
}

export interface AgentTeam {
  team_id: string
  name: string
  provider: string
  provider_display_name?: string
  cwd: string
  is_auto_detected: boolean
  lead: AgentSession | null
  members: AgentSession[]
}

export interface AgentTeamsResponse {
  teams: AgentTeam[]
  ungrouped: AgentSession[]
  total_teams: number
  total_sessions: number
}

export interface CreateTeamRequest {
  name: string
  provider?: string
  cwd?: string
  lead_session_name?: string | null
  members: { session_name: string; pane_id?: string | null; tmux_target: string }[]
}

export interface CreateTeamResponse {
  team_id: string
  name: string
  provider: string
  cwd: string
  is_auto_detected: boolean
  members: AgentTeamMember[]
}
