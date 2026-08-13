import { Badge } from '@/components/ui/badge'
import { formatTokens, formatTimestamp } from '@/features/usage/utils'
import type { SubscriptionUsageRow, Betrouwbaarheid } from './types'

const BETROUWBAARHEID_VARIANT: Record<Betrouwbaarheid, 'default' | 'secondary' | 'outline'> = {
  exact: 'default',
  schatting: 'secondary',
  onbekend: 'outline',
}

const BETROUWBAARHEID_LABEL: Record<Betrouwbaarheid, string> = {
  exact: 'Measured',
  schatting: 'Measured locally',
  onbekend: 'No signal',
}

/**
 * One subscription's usage line.
 *
 * There is deliberately no progress bar. Anthropic publishes no quota
 * number for Pro/Max, and a measurement on this machine found every
 * non-empty 5h block exceeding even the Max 20x community estimate — so
 * any percentage would be a ratio against a guess. We show the measured
 * token count and the window it belongs to, and nothing we can't source.
 */
export function SubscriptionUsageRowItem({ row }: { row: SubscriptionUsageRow }) {
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

        {row.verbruikt != null ? (
          <p className="mt-1.5 text-sm tabular-nums">
            {formatTokens(row.verbruikt)}{' '}
            <span className="text-xs font-normal text-muted-foreground">{row.eenheid} used this window</span>
          </p>
        ) : (
          <p className="text-xs text-muted-foreground mt-1">No usage signal available.</p>
        )}
      </div>

      {row.reset_op && (
        <span className="text-xs text-muted-foreground shrink-0">resets {formatTimestamp(row.reset_op)}</span>
      )}
    </div>
  )
}
