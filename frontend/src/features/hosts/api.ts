import { apiClient } from '@/lib/api'
import type { Host, HostCreateRequest, HostUpdateRequest, HostTestResponse, HostListResponse } from './types'

const BASE = 'hosts'

export async function fetchHosts(): Promise<Host[]> {
  const result = await apiClient<HostListResponse>(BASE)
  return result.hosts
}

export async function fetchHost(id: number): Promise<Host> {
  return apiClient<Host>(`${BASE}/${id}`)
}

export async function createHost(data: HostCreateRequest): Promise<Host> {
  return apiClient<Host>(BASE, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateHost(id: number, data: HostUpdateRequest): Promise<Host> {
  return apiClient<Host>(`${BASE}/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteHost(id: number): Promise<void> {
  await apiClient<void>(`${BASE}/${id}`, {
    method: 'DELETE',
  })
}

export async function testHostConnection(id: number): Promise<HostTestResponse> {
  return apiClient<HostTestResponse>(`${BASE}/${id}/test`, {
    method: 'POST',
  })
}

export async function discoverHostSessions(id: number): Promise<{ sessions: Record<string, unknown>[]; count: number }> {
  return apiClient<{ sessions: Record<string, unknown>[]; count: number }>(`${BASE}/${id}/discover`, {
    method: 'POST',
  })
}
