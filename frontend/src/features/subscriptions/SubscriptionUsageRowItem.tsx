import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { formatTokens, formatTimestamp } from '@/features/usage/utils'
import type { SubscriptionUsageRow, Betrouwbaarheid } from './types'

const BETROUWBAARHEID_VARIANT: Record<Betrouwbaarheid, 'default' | 'secondary' | 'outline'> = {
  exact: 'default',
  schatting: 'secondary',
  onbekend: 'outline',
}

const BETROUWBAARHEID_LABEL: Record<Betrouwbaarheid, string> = {
  exact: 'Exact',
  schatting: 'Estimate',
  onbekend: 'Unknown',
}

export function SubscriptionUsageRowItem({ row }: { row: SubscriptionUsageRow }) {
  const hasLimit = row.limiet != null && row.limiet > 0
  const percent =
    hasLimit && row.verbruikt != null ? Math.min(100, (row.verbruikt / (row.limiet as number)) * 100) : null

  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-sm">{row.subscription_label}</span>
          <Badge variant={BETROUWBAARHEID_VARIANT[row.betrouwbaarheid]}>
            {BETROUWBAARHEID_LABEL[row.betrouwbaarheid]}
          </Badge>
          {row.venster_label && <span className="text-xs text-muted-foreground">{row.venster_label}</span>}
        </div>

        {row.betrouwbaarheid === 'onbekend' ? (
          <p className="text-xs text-muted-foreground mt-1">No usage signal available for this subscription.</p>
        ) : hasLimit && percent != null ? (
          <div className="mt-2 space-y-1 max-w-sm">
            <Progress value={percent} className="h-2" />
            <p className="text-xs text-muted-foreground">
              {formatTokens(row.verbruikt ?? 0)} / {formatTokens(row.limiet as number)} {row.eenheid}
            </p>
          </div>
        ) : row.drempel_gebruikt != null ? (
          <div className="mt-2 space-y-1 max-w-sm">
            <Progress value={Math.min(100, row.drempel_gebruikt * 100)} className="h-2" />
            <p className="text-xs text-muted-foreground">
              {Math.round(row.drempel_gebruikt * 100)}% used — limit not published
            </p>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground mt-1">Limit not published.</p>
        )}
      </div>

      {row.reset_op && (
        <span className="text-xs text-muted-foreground shrink-0">resets {formatTimestamp(row.reset_op)}</span>
      )}
    </div>
  )
}
