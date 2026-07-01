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
  platform?: 'anthropic' | 'bedrock'
  aws_region?: string
  aws_profile?: string
  bedrock_model?: string
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
  platform?: 'anthropic' | 'bedrock'
  aws_region?: string
  aws_profile?: string
  bedrock_model?: string
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

export interface KillSessionResponse {
  killed: boolean
  error?: string
}

export interface RenameSessionResponse {
  renamed: boolean
  session_name: string
  tmux_target: string
}
