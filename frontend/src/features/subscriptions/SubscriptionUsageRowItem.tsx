import { Badge } from '@/components/ui/badge'
import { formatTokens, formatTimestamp } from '@/features/usage/utils'
import type { SubscriptionUsageRow, UsageWindow, Betrouwbaarheid } from './types'

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
 * Status steps for a usage meter.
 *
 * These are *status* colors, not categorical ones: the meter answers
 * "am I about to run out", which is a state, not an identity. They are
 * therefore never reused for anything else on this page, and the numeric
 * percentage is always rendered beside the bar — color is never the only
 * carrier of the signal.
 *
 * 90% is the operationally meaningful line: it is the usual `drempel` at
 * which the subscription pool starts routing cards away from a lane.
 */
function meterTone(usedFraction: number): string {
  if (usedFraction >= 0.9) return 'bg-destructive'
  if (usedFraction >= 0.75) return 'bg-amber-600 dark:bg-amber-400'
  return 'bg-emerald-600 dark:bg-emerald-400'
}

/**
 * The raw pair behind the percentage, in the provider's own unit.
 *
 * Suppressed when the unit is already '%', where "44 / 100 %" merely
 * restates the number next to it. opencode's dollar caps are the case
 * this exists for: "$24.99 / $30" is the sentence a human wants.
 */
function rawPair(window: UsageWindow): string | null {
  if (window.verbruikt == null || window.limiet == null) return null
  if (window.eenheid === '%') return null
  if (window.eenheid === '$') return `$${window.verbruikt.toFixed(2)} / $${window.limiet.toFixed(0)}`
  return `${formatTokens(window.verbruikt)} / ${formatTokens(window.limiet)} ${window.eenheid}`
}

function UsageMeter({ window: w }: { window: UsageWindow }) {
  const pct = w.used_fraction * 100
  const raw = rawPair(w)
  // Overspend is real — opencode Go's "Use balance" runs past the cap
  // rather than blocking — so the number keeps climbing while the bar,
  // which has nowhere left to go, pins at full.
  const barWidth = Math.min(100, Math.max(0, pct))

  return (
    <div className="mt-2 first:mt-1.5">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="text-muted-foreground">{w.label}</span>
        <span className="flex items-baseline gap-2 tabular-nums">
          {raw && <span className="text-muted-foreground">{raw}</span>}
          <span className="font-medium text-foreground">{pct.toFixed(pct < 10 ? 1 : 0)}%</span>
        </span>
      </div>
      <div
        className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${w.label} usage`}
      >
        <div
          className={`h-full rounded-full transition-[width] ${meterTone(w.used_fraction)}`}
          style={{ width: `${barWidth}%` }}
        />
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-3 text-[11px] text-muted-foreground">
        <span>{pct > 100 ? 'over cap — drawing on balance' : ''}</span>
        {w.resets_at && <span className="shrink-0">resets {formatTimestamp(w.resets_at)}</span>}
      </div>
    </div>
  )
}

/**
 * One subscription's usage line — a meter per rate window.
 *
 * This row deliberately had *no* progress bar for a long time, because
 * Anthropic publishes no quota number for Pro/Max and every percentage
 * we could have drawn would have been a ratio against a guess. That
 * reasoning was correct then and no longer applies: all four
 * subscriptions now report against a real denominator — an official
 * percentage (Anthropic, MiniMax, codex) or a published dollar cap
 * (opencode Go). A bar is honest when the denominator is.
 *
 * Windows differ per subscription in both count and kind — ChatGPT Go
 * has one 30-day window, opencode Go has three dollar-denominated ones —
 * so the row renders whatever the provider measured rather than assuming
 * a shape. A subscription with no signal still says so in words.
 */
export function SubscriptionUsageRowItem({ row }: { row: SubscriptionUsageRow }) {
  return (
    <div className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-sm">{row.subscription_label}</span>
        <Badge variant={BETROUWBAARHEID_VARIANT[row.betrouwbaarheid]}>
          {BETROUWBAARHEID_LABEL[row.betrouwbaarheid]}
        </Badge>
      </div>

      {row.windows.length > 0 ? (
        row.windows.map((w) => <UsageMeter key={w.label} window={w} />)
      ) : row.verbruikt != null ? (
        // Fallback rung: a measured amount with no published limit to
        // divide by. No bar, because there is no denominator.
        <p className="mt-1.5 text-sm tabular-nums">
          {formatTokens(row.verbruikt)}{' '}
          <span className="text-xs font-normal text-muted-foreground">
            {row.eenheid} used in the {row.venster_label ?? 'current'} window
          </span>
          {row.reset_op && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              · resets {formatTimestamp(row.reset_op)}
            </span>
          )}
        </p>
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">No usage signal available.</p>
      )}
    </div>
  )
}
