import { useCallback, useEffect, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { fetchSubscriptionUsage } from './api'
import { AnthropicPlanTierSelect } from './AnthropicPlanTierSelect'
import { SubscriptionUsageRowItem } from './SubscriptionUsageRowItem'
import type { SubscriptionUsageRow } from './types'

const ANTHROPIC_SUBSCRIPTION_ID = 'claude-code:anthropic'

export function SubscriptionUsageSection() {
  const [rows, setRows] = useState<SubscriptionUsageRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    fetchSubscriptionUsage()
      .then((data) => {
        setRows(data.subscriptions)
        setError(null)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load subscription usage'))
  }, [])

  useEffect(() => {
    load()
    function onVisible() {
      if (document.visibilityState === 'visible') load()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [load])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Usage</CardTitle>
        <CardDescription>
          How much of each subscription's quota is used right now. Each provider keeps its own labels — the
          numbers are not comparable across providers.
        </CardDescription>
      </CardHeader>
      <CardContent className="divide-y">
        {error && <p className="text-xs text-destructive">{error}</p>}
        {!rows && !error && <p className="text-xs text-muted-foreground">Loading...</p>}
        {rows?.map((row) => (
          <div key={row.subscription_id}>
            <SubscriptionUsageRowItem row={row} />
            {row.subscription_id === ANTHROPIC_SUBSCRIPTION_ID && <AnthropicPlanTierSelect onChange={load} />}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
