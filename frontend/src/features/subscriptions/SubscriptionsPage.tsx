import { MinimaxCredentialsCard } from './MinimaxCredentialsCard'
import { SubscriptionUsageSection } from './SubscriptionUsageSection'

export function SubscriptionsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Subscriptions</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Credentials for launching Claude Code sessions against alternate providers.
        </p>
      </div>

      <SubscriptionUsageSection />
      <MinimaxCredentialsCard />
    </div>
  )
}