import { Progress } from '@/components/ui/progress'
import { Badge } from '@/components/ui/badge'
import { formatTokens, formatCost, getRelativeTime } from '@/features/usage/utils'
import type { PeriodUsageResponse } from './types'

interface UsagePeriodRowProps {
  period: PeriodUsageResponse
}

function formatUsed(used: number, unit: string): string {
  if (unit === 'tokens') return formatTokens(used)
  if (unit === 'USD' || unit === 'usd') return formatCost(used)
  return `${used.toLocaleString()} ${unit}`
}

export function UsagePeriodRow({ period }: UsagePeriodRowProps) {
  const { label, used, limit, unit, reset_at, source, note } = period
  const hasLimit = limit !== null
  const percent = hasLimit && limit! > 0 ? Math.min(100, (used / limit!) * 100) : 0

  return (
    <div className="space-y-2" data-testid={`period-row-${label}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{label}</span>
          <Badge variant="outline" className="text-xs">
            {source}
          </Badge>
        </div>
        <span className="text-sm tabular-nums">{formatUsed(used, unit)}</span>
      </div>

      {hasLimit ? (
        <>
          <Progress value={percent} className="h-2" />
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>of {formatUsed(limit!, unit)}</span>
            {reset_at && <span>resets {getRelativeTime(reset_at)}</span>}
          </div>
        </>
      ) : (
        <p className="text-xs text-muted-foreground">limit not published by provider</p>
      )}

      {note && <p className="text-xs text-muted-foreground italic">{note}</p>}
    </div>
  )
}
