import { useEffect, useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { UsagePeriodRow } from './UsagePeriodRow'
import { MinimaxCredentialsCard } from './MinimaxCredentialsCard'
import { fetchSubscriptionUsage } from './api'
import type { SubscriptionProviderId, SubscriptionUsageResponse } from './types'

interface SubscriptionUsageCardProps {
  provider: SubscriptionProviderId
  title: string
  description: string
  onRefresh?: () => void
}

export function SubscriptionUsageCard({
  provider,
  title,
  description,
  onRefresh,
}: SubscriptionUsageCardProps) {
  const [data, setData] = useState<SubscriptionUsageResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(
    async (force = false) => {
      setLoading(true)
      try {
        const res = await fetchSubscriptionUsage(provider)
        setData(res)
        if (force) onRefresh?.()
      } finally {
        setLoading(false)
      }
    },
    [provider, onRefresh],
  )

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === 'visible') load()
    }
    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
  }, [load])

  return (
    <Card data-testid={`usage-card-${provider}`}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {data?.error_code === 'not_configured' && provider === 'minimax' && (
          <>
            <MinimaxCredentialsCard />
            <p className="text-xs text-muted-foreground">Set your API key to see usage.</p>
          </>
        )}

        {data?.error_code === 'plan_unknown' && provider === 'anthropic' && (
          <p className="text-sm text-muted-foreground">
            Pick your plan in the card above to see 5h/weekly leftover.
          </p>
        )}

        {data?.error_code &&
          data.error_code !== 'not_configured' &&
          data.error_code !== 'plan_unknown' && (
            <div
              className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              data-testid="error-badge"
            >
              {data.error ?? 'Could not fetch usage.'}
            </div>
          )}

        {data && !data.error_code && (
          <div className="space-y-4">
            {data.periods.map((p) => (
              <UsagePeriodRow key={p.label} period={p} />
            ))}
            <StaleFooter fetchedAt={data.fetched_at} />
          </div>
        )}

        {loading && !data && (
          <p className="text-xs text-muted-foreground">Loading...</p>
        )}
      </CardContent>
    </Card>
  )
}

function StaleFooter({ fetchedAt }: { fetchedAt: string }) {
  const fetchedMs = new Date(fetchedAt).getTime()
  const ageMin = Math.round((Date.now() - fetchedMs) / 60_000)
  if (ageMin <= 5) return null
  return (
    <p className="text-xs text-muted-foreground">
      refreshed {ageMin} min ago — open another tab or click refresh to get a live number.
    </p>
  )
}
