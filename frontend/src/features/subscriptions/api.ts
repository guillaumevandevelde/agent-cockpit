import { apiClient } from '@/lib/api'
import type {
  AnthropicPlanTier,
  AnthropicPlanTierOptionsResponse,
  SubscriptionUsageListResponse,
} from './types'

const BASE = 'subscriptions'

export async function fetchSubscriptionUsage(): Promise<SubscriptionUsageListResponse> {
  return apiClient<SubscriptionUsageListResponse>(`${BASE}/usage`)
}

export async function fetchAnthropicPlanTierOptions(): Promise<AnthropicPlanTierOptionsResponse> {
  return apiClient<AnthropicPlanTierOptionsResponse>(`${BASE}/anthropic/plan-tiers`)
}

export async function fetchAnthropicPlanTier(): Promise<AnthropicPlanTier> {
  return apiClient<AnthropicPlanTier>(`${BASE}/anthropic/plan-tier`)
}

export async function setAnthropicPlanTier(
  tier: string | null,
  customLimitTokens: number | null
): Promise<AnthropicPlanTier> {
  return apiClient<AnthropicPlanTier>(`${BASE}/anthropic/plan-tier`, {
    method: 'PUT',
    body: JSON.stringify({ tier, custom_limit_tokens: customLimitTokens }),
  })
}
