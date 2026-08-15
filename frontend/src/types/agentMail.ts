export type MailMemberStatus = 'connected' | 'observed' | 'offline'
export type MailSessionSource = 'hook' | 'mcp' | 'observed' | string

export interface MailSessionResponse {
  id: number
  provider: string
  source: MailSessionSource
  session_key: string
  cwd?: string | null
  tmux_target?: string | null
  mailbox_status: MailMemberStatus | string
  activity?: string | null
  last_seen_at?: string | null
}

export interface MailMemberResponse {
  id: number
  identity_key: string
  repo_id: string
  repo_path: string
  repo_name: string
  display_name: string
  role?: string | null
  charter?: string | null
  status: MailMemberStatus
  last_inbox_checked_at?: string | null
  sessions: MailSessionResponse[]
}

export interface TeamListResponse {
  members: MailMemberResponse[]
}

export interface MailMemberUpdate {
  display_name?: string
  role?: string | null
  charter?: string | null
}

export interface AgentMailInstallStatus {
  claude_code_hooks: string[]
  claude_code_hooks_missing: string[]
  codex_cli_available: boolean
  codex_hooks: string[]
  codex_hooks_missing: string[]
  curl_available: boolean
  codex_hook_shim_path: string
  python_path: string
  cockpit_url: string
  claude_settings_path?: string | null
  codex_hooks_path?: string | null
  mcp_server_hint: string
}

export interface AgentMailSnippets {
  codex_hooks_snippet: string
  agents_md_snippet: string
}
