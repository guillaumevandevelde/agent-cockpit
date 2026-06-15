import { apiClient, buildEndpoint } from '@/lib/api'
import type {
  ScheduledMessage,
  ScheduledMessageCreate,
  ScheduledMessageUpdate,
  ScheduledMessageListResponse,
  DeliveryAttempt,
  ResumableSession,
} from './types'

const BASE = 'scheduled-messages'

export async function listScheduledMessages(): Promise<ScheduledMessageListResponse> {
  return apiClient<ScheduledMessageListResponse>(BASE)
}

export async function createScheduledMessage(data: ScheduledMessageCreate): Promise<ScheduledMessage> {
  return apiClient<ScheduledMessage>(BASE, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateScheduledMessage(id: number, data: ScheduledMessageUpdate): Promise<ScheduledMessage> {
  return apiClient<ScheduledMessage>(`${BASE}/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deleteScheduledMessage(id: number): Promise<void> {
  await apiClient<unknown>(`${BASE}/${id}`, { method: 'DELETE' })
}

export async function listDeliveryAttempts(id: number): Promise<DeliveryAttempt[]> {
  return apiClient<DeliveryAttempt[]>(`${BASE}/${id}/attempts`)
}

export async function listResumableSessions(directory: string): Promise<ResumableSession[]> {
  const res = await apiClient<{ sessions: ResumableSession[] }>(
    buildEndpoint('agent-bridge/resumable-sessions', { directory, limit: 20 }),
  )
  return res.sessions
}
