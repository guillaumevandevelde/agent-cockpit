import { SubscriptionUsageCard } from './SubscriptionUsageCard'
import { AnthropicCredentialsCard } from './AnthropicCredentialsCard'

export function SubscriptionsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Subscriptions</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Per-subscription quota left (5h rate, weekly, or whatever the provider exposes).
        </p>
      </div>

      <AnthropicCredentialsCard />
      <SubscriptionUsageCard
        provider="anthropic"
        title="Anthropic"
        description="5h rate and weekly leftover based on local JSONL and your selected plan."
      />
      <SubscriptionUsageCard
        provider="minimax"
        title="MiniMax"
        description="Quota left for the MiniMax subscription, fetched from the MiniMax API."
      />
    </div>
  )
}