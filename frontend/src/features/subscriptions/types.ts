export type SubscriptionProviderId = 'anthropic' | 'minimax'

export type SubscriptionErrorCode =
  | 'not_configured'
  | 'unauthorized'
  | 'unreachable'
  | 'malformed'
  | 'no_endpoint'
  | 'plan_unknown'

export type PlanTier = 'pro' | 'max_5x' | 'max_20x' | 'team'

export interface PeriodUsageResponse {
  label: string
  used: number
  limit: number | null
  unit: string
  reset_at: string | null
  source: string
  note: string | null
}

export interface SubscriptionUsageResponse {
  provider: SubscriptionProviderId
  plan_label: string | null
  periods: PeriodUsageResponse[]
  fetched_at: string
  error: string | null
  error_code: SubscriptionErrorCode | null
}

export interface AnthropicPlanTierResponse {
  tier: PlanTier | null
}
