import { useCallback, useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { fetchSubscriptionUsage } from './api'
import { SubscriptionUsageRowItem } from './SubscriptionUsageRowItem'
import type { SubscriptionUsageRow } from './types'

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

  // Most providers publish nothing at all. Listing seven rows of which six
  // say "no signal" buries the one row that carries a real number, so the
  // signal-less ones collapse into a footnote instead.
  const [measured, silent] = useMemo(() => {
    const all = rows ?? []
    return [
      all.filter((r) => r.betrouwbaarheid !== 'onbekend'),
      all.filter((r) => r.betrouwbaarheid === 'onbekend'),
    ]
  }, [rows])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Usage</CardTitle>
        <CardDescription>
          Tokens consumed in the current rate window, summed from local session logs. Anthropic publishes no
          quota figure for Pro/Max, so there is no percentage here — only what was measured.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && <p className="text-xs text-destructive">{error}</p>}
        {!rows && !error && <p className="text-xs text-muted-foreground">Loading...</p>}

        <div className="divide-y">
          {measured.map((row) => (
            <SubscriptionUsageRowItem key={row.subscription_id} row={row} />
          ))}
        </div>

        {measured.length === 0 && rows && !error && (
          <p className="text-xs text-muted-foreground">No subscription reports usage right now.</p>
        )}

        {silent.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-muted-foreground">
              {silent.length} subscription{silent.length === 1 ? '' : 's'} publish no usage signal
            </summary>
            <ul className="mt-2 space-y-1 pl-1">
              {silent.map((row) => (
                <li key={row.subscription_id} className="text-xs text-muted-foreground">
                  {row.subscription_label}
                </li>
              ))}
            </ul>
          </details>
        )}
      </CardContent>
    </Card>
  )
}
