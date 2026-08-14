export type Betrouwbaarheid = 'exact' | 'schatting' | 'onbekend'

/**
 * One rate window of a subscription.
 *
 * `used_fraction` is always the part consumed, whatever the vendor
 * reported — MiniMax publishes what is left and the backend inverts it.
 * Values above 1 are legal: opencode Go's "Use balance" option lets
 * spend run past the cap instead of blocking.
 *
 * `limiet` is null only when no denominator exists. `eenheid` says what
 * `verbruikt`/`limiet` are counted in: '%' for the vendors that report a
 * percentage, '$' for opencode's dollar caps.
 */
export interface UsageWindow {
  label: string
  used_fraction: number
  resets_at: string | null
  verbruikt: number | null
  limiet: number | null
  eenheid: string
}

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
  /** Every window this subscription meters against; empty when no signal. */
  windows: UsageWindow[]
}

export interface SubscriptionUsageListResponse {
  subscriptions: SubscriptionUsageRow[]
}
