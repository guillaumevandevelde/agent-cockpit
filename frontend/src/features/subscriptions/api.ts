import { apiClient } from '@/lib/api'
import type { SubscriptionUsageListResponse } from './types'

const BASE = 'subscriptions'

export async function fetchSubscriptionUsage(): Promise<SubscriptionUsageListResponse> {
  return apiClient<SubscriptionUsageListResponse>(`${BASE}/usage`)
}
