export type Betrouwbaarheid = 'exact' | 'schatting' | 'onbekend'

export interface SubscriptionUsageRow {
  subscription_id: string
  subscription_label: string
  beschikbaar: boolean
  drempel_gebruikt: number | null
  bron: string
  betrouwbaarheid: Betrouwbaarheid
  verbruikt: number | null
  limiet: number | null
  eenheid: string
  venster_label: string | null
  reset_op: string | null
}

export interface SubscriptionUsageListResponse {
  subscriptions: SubscriptionUsageRow[]
}

export interface AnthropicPlanTierOption {
  key: string
  label: string
  tokens_5h: number
}

export interface AnthropicPlanTierOptionsResponse {
  tiers: AnthropicPlanTierOption[]
}

export interface AnthropicPlanTier {
  tier: string | null
  custom_limit_tokens: number | null
}

export const CUSTOM_PLAN_TIER = 'custom'
