import { apiClient, buildEndpoint, type ApiError } from '@/lib/api'
import { API_BASE_URL } from '@/lib/constants'
import type { AgenticCliId } from '@/types/providers'
import type {
  BridgeAttachment,
  BridgeAttachmentDeleteResponse,
  BridgeAttachmentListResponse,
  BridgeAttachmentPasteRequest,
  BridgeAttachmentPasteResponse,
  CCSessionsResponse,
  CCPreviewResponse,
  CCTokenResponse,
  EndpointListResponse,
  EndpointUpsertRequest,
  GitStatusResponse,
  SpawnSessionRequest,
  SpawnSessionResponse,
  KillSessionResponse,
  RenameSessionResponse,
  BulkResumeRequest,
  BulkResumeResponse,
  ProviderStatusResponse,
} from './types'
import type { ResumableSessionListResponse } from '@/types/sessions'

const BASE = 'agent-bridge'

function apiErrorMessage(error: ApiError, fallback = 'An error occurred'): string {
  if (error.message) return error.message
  if (typeof error.detail === 'string') return error.detail
  if (Array.isArray(error.detail)) {
    const messages = error.detail.map((item) => item.msg).filter(Boolean)
    if (messages.length > 0) return messages.join(', ')
  }
  if (error.detail && typeof error.detail === 'object' && 'msg' in error.detail && error.detail.msg) {
    return error.detail.msg
  }
  return fallback
}

async function attachmentRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const { token } = await fetchTerminalToken()
  const headers = new Headers(options.headers)
  headers.set('X-Claude-Cockpit-Terminal-Token', token)
  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const error: ApiError = await response.json().catch(() => ({
      message: `HTTP ${response.status}: ${response.statusText}`,
    }))
    throw new Error(apiErrorMessage(error))
  }

  return response.json()
}

export async function fetchCCSessions(provider?: AgenticCliId): Promise<CCSessionsResponse> {
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

export async function fetchMinimaxProviderStatus(): Promise<ProviderStatusResponse> {
  return apiClient<ProviderStatusResponse>(BASE + '/platforms/minimax/status')
}

export async function setMinimaxApiKey(minimaxApiKey: string): Promise<ProviderStatusResponse> {
  return apiClient<ProviderStatusResponse>(BASE + '/platforms/minimax/credentials', {
    method: 'POST',
    body: JSON.stringify({ minimax_api_key: minimaxApiKey }),
  })
}

export async function clearMinimaxApiKey(): Promise<ProviderStatusResponse> {
  return apiClient<ProviderStatusResponse>(BASE + '/platforms/minimax/credentials', {
    method: 'DELETE',
  })
}

export async function fetchEndpoints(projectKey?: string): Promise<EndpointListResponse> {
  return apiClient<EndpointListResponse>(
    buildEndpoint(BASE + '/platforms/endpoints', { project_key: projectKey }),
  )
}

export async function upsertEndpoint(
  projectKey: string,
  request: EndpointUpsertRequest,
): Promise<EndpointListResponse['endpoints'][number]> {
  return apiClient<EndpointListResponse['endpoints'][number]>(
    buildEndpoint(BASE + '/platforms/endpoints', { project_key: projectKey }),
    {
      method: 'POST',
      body: JSON.stringify(request),
    },
  )
}

export async function deleteEndpoint(projectKey: string, name: string): Promise<{ deleted: boolean }> {
  return apiClient<{ deleted: boolean }>(
    `${BASE}/platforms/endpoints/${encodeURIComponent(name)}${buildEndpoint('', { project_key: projectKey })}`,
    { method: 'DELETE' },
  )
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

// ── Run Group API ────────────────────────────────────────────────────────

import type { RunGroupsResponse, CreateGroupRequest, CreateGroupResponse } from './types'

export async function fetchTeams(): Promise<RunGroupsResponse> {
  return apiClient<RunGroupsResponse>(BASE + '/teams')
}

export async function createTeam(request: CreateGroupRequest): Promise<CreateGroupResponse> {
  return apiClient<CreateGroupResponse>(BASE + '/teams', {
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

export async function uploadBridgeAttachment(
  target: string,
  file: File
): Promise<BridgeAttachment> {
  const form = new FormData()
  form.append('file', file)
  form.append('created_by', 'deck-ui')
  return attachmentRequest<BridgeAttachment>(
    `${BASE}/sessions/${encodeURIComponent(target)}/attachments`,
    {
      method: 'POST',
      body: form,
    }
  )
}

export async function listBridgeAttachments(target: string): Promise<BridgeAttachmentListResponse> {
  return attachmentRequest<BridgeAttachmentListResponse>(
    `${BASE}/sessions/${encodeURIComponent(target)}/attachments`
  )
}

export async function pasteBridgeAttachment(
  target: string,
  attachmentId: number,
  request: BridgeAttachmentPasteRequest
): Promise<BridgeAttachmentPasteResponse> {
  return attachmentRequest<BridgeAttachmentPasteResponse>(
    `${BASE}/sessions/${encodeURIComponent(target)}/attachments/${attachmentId}/paste`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    }
  )
}

export async function deleteBridgeAttachment(
  target: string,
  attachmentId: number
): Promise<BridgeAttachmentDeleteResponse> {
  return attachmentRequest<BridgeAttachmentDeleteResponse>(
    `${BASE}/sessions/${encodeURIComponent(target)}/attachments/${attachmentId}`,
    { method: 'DELETE' }
  )
}
