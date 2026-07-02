import { apiClient, buildEndpoint } from '@/lib/api'
import type { AgentProviderId } from '@/types/providers'
import type { CCSessionsResponse, CCPreviewResponse, CCTokenResponse, GitStatusResponse, SpawnSessionRequest, SpawnSessionResponse, KillSessionResponse, RenameSessionResponse, BulkResumeRequest, BulkResumeResponse } from './types'
import type { ResumableSessionListResponse } from '@/types/sessions'

const BASE = 'agent-bridge'

export async function fetchCCSessions(provider?: AgentProviderId): Promise<CCSessionsResponse> {
  return apiClient<CCSessionsResponse>(buildEndpoint(BASE + '/sessions', { provider }))
}

export async function fetchResumableSessions(
  directory: string,
  limit = 20,
): Promise<ResumableSessionListResponse> {
  return apiClient<ResumableSessionListResponse>(
    buildEndpoint(BASE + '/resumable-sessions', { directory, limit }),
  )
}

export async function fetchSessionPreview(target: string): Promise<CCPreviewResponse> {
  return apiClient<CCPreviewResponse>(`${BASE}/sessions/${encodeURIComponent(target)}/preview`)
}

export async function fetchSessionGitStatus(target: string): Promise<GitStatusResponse> {
  return apiClient<GitStatusResponse>(`${BASE}/sessions/${encodeURIComponent(target)}/git-status`)
}

export async function fetchTerminalToken(): Promise<CCTokenResponse> {
  return apiClient<CCTokenResponse>(BASE + '/token')
}

export function buildTerminalWsUrl(target: string, token: string, mode: 'readonly' | 'interactive' = 'readonly'): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}/api/v1/${BASE}/sessions/${encodeURIComponent(target)}/terminal?token=${token}&mode=${mode}`
}

export async function spawnSession(request: SpawnSessionRequest): Promise<SpawnSessionResponse> {
  return apiClient<SpawnSessionResponse>(BASE + '/sessions', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function bulkResumeSessions(request: BulkResumeRequest): Promise<BulkResumeResponse> {
  return apiClient<BulkResumeResponse>(BASE + '/sessions/bulk-resume', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function killSession(target: string, cleanupWorktree: boolean = false): Promise<KillSessionResponse> {
  const params = cleanupWorktree ? '?cleanup_worktree=true' : ''
  return apiClient<KillSessionResponse>(`${BASE}/sessions/${encodeURIComponent(target)}${params}`, {
    method: 'DELETE',
  })
}

export async function renameSession(sessionName: string, name: string): Promise<RenameSessionResponse> {
  return apiClient<RenameSessionResponse>(`${BASE}/sessions/${encodeURIComponent(sessionName)}/rename`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

// ── Team API ──────────────────────────────────────────────────────────────

import type { AgentTeamsResponse, CreateTeamRequest, CreateTeamResponse } from './types'

export async function fetchTeams(): Promise<AgentTeamsResponse> {
  return apiClient<AgentTeamsResponse>(BASE + '/teams')
}

export async function createTeam(request: CreateTeamRequest): Promise<CreateTeamResponse> {
  return apiClient<CreateTeamResponse>(BASE + '/teams', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function deleteTeam(teamId: number): Promise<{ deleted: boolean }> {
  return apiClient<{ deleted: boolean }>(`${BASE}/teams/${teamId}`, {
    method: 'DELETE',
  })
}

export async function addTeamMember(
  teamId: number,
  sessionName: string,
  tmuxTarget: string,
  paneId?: string,
): Promise<{ added: boolean }> {
  return apiClient<{ added: boolean }>(`${BASE}/teams/${teamId}/members`, {
    method: 'POST',
    body: JSON.stringify({
      session_name: sessionName,
      tmux_target: tmuxTarget,
      pane_id: paneId ?? null,
    }),
  })
}

export async function removeTeamMember(teamId: number, memberId: number): Promise<{ removed: boolean }> {
  return apiClient<{ removed: boolean }>(`${BASE}/teams/${teamId}/members/${memberId}`, {
    method: 'DELETE',
  })
}
