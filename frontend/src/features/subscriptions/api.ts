import { apiClient } from '@/lib/api'
import type {
  AnthropicPlanTierResponse,
  PlanTier,
  SubscriptionProviderId,
  SubscriptionUsageResponse,
} from './types'

const BASE = 'agent-bridge/subscriptions'

export function fetchSubscriptionUsage(
  providerId: SubscriptionProviderId,
): Promise<SubscriptionUsageResponse> {
  return apiClient<SubscriptionUsageResponse>(`${BASE}/${providerId}/usage`)
}

export function fetchAnthropicPlanTier(): Promise<AnthropicPlanTierResponse> {
  return apiClient<AnthropicPlanTierResponse>(`${BASE}/anthropic/plan-tier`)
}

export function setAnthropicPlanTier(tier: PlanTier | null): Promise<AnthropicPlanTierResponse> {
  return apiClient<AnthropicPlanTierResponse>(`${BASE}/anthropic/plan-tier`, {
    method: 'PUT',
    body: JSON.stringify({ tier }),
  })
}
