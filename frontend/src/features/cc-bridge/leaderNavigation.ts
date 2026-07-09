import type { LeaderNavigationDirection } from './types'

/**
 * Resolves a leader-key navigation direction to the target that should gain focus.
 * Returns null when there is nothing to navigate to (source not displayed, empty
 * list, or an out-of-range numeric jump) — callers should keep the current focus.
 */
export function resolveLeaderNavigationTarget(
  displayedTargets: string[],
  sourceTarget: string,
  direction: LeaderNavigationDirection,
): string | null {
  const sourceIndex = displayedTargets.indexOf(sourceTarget)
  if (sourceIndex === -1) return null

  if (typeof direction === 'number') {
    return displayedTargets[direction - 1] ?? null
  }

  if (displayedTargets.length === 0) return null
  const delta = direction === 'next' ? 1 : -1
  const nextIndex = (sourceIndex + delta + displayedTargets.length) % displayedTargets.length
  return displayedTargets[nextIndex]
}
