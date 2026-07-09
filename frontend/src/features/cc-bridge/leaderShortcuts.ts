import type { LeaderNavigationDirection } from './types'

/** Minimal shape of the KeyboardEvent fields the leader-key logic needs, so this stays testable without jsdom. */
export interface LeaderKeyEventLike {
  type: string
  key: string
  code: string
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
  altKey: boolean
  isComposing: boolean
}

export interface LeaderShortcut {
  keys: string
  label: string
}

export const LEADER_PREFIX_LABEL = 'Ctrl+Space'

export const LEADER_SHORTCUTS: LeaderShortcut[] = [
  { keys: '←/→', label: 'Previous / next displayed pane' },
  { keys: '1-4', label: 'Jump to displayed pane' },
  { keys: 'r', label: 'Toggle read-only / interactive' },
  { keys: 'Esc', label: 'Cancel the leader' },
]

/** Ctrl+Space (and nothing else held) arms the leader. */
export function isLeaderPrefix(event: LeaderKeyEventLike): boolean {
  return (
    event.ctrlKey
    && !event.altKey
    && !event.metaKey
    && !event.shiftKey
    && (event.code === 'Space' || event.key === ' ' || event.key === 'Spacebar')
  )
}

export function keyHasCommandModifier(event: LeaderKeyEventLike): boolean {
  return event.ctrlKey || event.altKey || event.metaKey
}

/** Follow-up key after the leader is armed: pane navigation, or null if it's not a navigation shortcut. */
export function shortcutDirection(event: LeaderKeyEventLike): LeaderNavigationDirection | null {
  if (keyHasCommandModifier(event)) return null
  if (event.key === 'ArrowLeft') return 'prev'
  if (event.key === 'ArrowRight') return 'next'
  if (/^[1-4]$/.test(event.key)) return Number(event.key)
  return null
}
