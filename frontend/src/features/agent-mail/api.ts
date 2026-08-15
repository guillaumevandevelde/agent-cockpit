import { apiClient, buildEndpoint } from '@/lib/api'
import type {
  AgentMailInstallStatus,
  AgentMailSnippets,
  MailMemberResponse,
  MailMemberUpdate,
  TeamListResponse,
} from '@/types/agentMail'

export function fetchAgentMailTeam(sync = true): Promise<TeamListResponse> {
  return apiClient<TeamListResponse>(buildEndpoint('agent-mail/team', { sync }))
}

export function updateAgentMailMember(memberId: number, update: MailMemberUpdate): Promise<MailMemberResponse> {
  return apiClient<MailMemberResponse>(`agent-mail/members/${memberId}`, {
    method: 'PATCH',
    body: JSON.stringify(update),
  })
}

export function fetchAgentMailInstallStatus(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/status')
}

export function applyClaudeCodeAgentMailInstall(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/claude-code/apply', {
    method: 'POST',
    body: JSON.stringify({ confirmed: true }),
  })
}

export function uninstallClaudeCodeAgentMail(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/claude-code/uninstall', {
    method: 'POST',
    body: JSON.stringify({ confirmed: true }),
  })
}

export function applyCodexAgentMailInstall(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/codex/apply', {
    method: 'POST',
    body: JSON.stringify({ confirmed: true }),
  })
}

export function uninstallCodexAgentMail(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/codex/uninstall', {
    method: 'POST',
    body: JSON.stringify({ confirmed: true }),
  })
}

export function fetchAgentMailSnippets(): Promise<AgentMailSnippets> {
  return apiClient<AgentMailSnippets>('agent-mail/install/snippets')
}
