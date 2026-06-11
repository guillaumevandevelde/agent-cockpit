import type { PresenceSession } from '@/types/presence'

/** Persistent attention state shown as a badge in the Agent Bridge. */
export type AttentionKind = 'input' | 'error'

/** Current attention state of a presence session, or null if none. */
export function paneAttentionKind(session: PresenceSession): AttentionKind | null {
  if (session.status === 'error') return 'error'
  if (session.status === 'stopped') return 'input'
  return null
}
