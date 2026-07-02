import { useCallback, useMemo, useState } from 'react'
import { usePresenceWebSocket } from '@/hooks/usePresenceWebSocket'
import type { PresenceSession } from '@/types/presence'
import { paneAttentionKind, type AttentionKind } from './attention'

/**
 * Live map of tmux pane id -> attention state, derived from the presence WS.
 * Join against Agent Bridge sessions via `session.pane_id`.
 */
export function useAttentionByPane(): Map<string, AttentionKind> {
  const [sessions, setSessions] = useState<Map<string, PresenceSession>>(new Map())

  const onSessionUpdate = useCallback((session: PresenceSession) => {
    setSessions((prev) => {
      const next = new Map(prev)
      next.set(session.session_id, session)
      return next
    })
  }, [])

  const onSessionRemove = useCallback((sessionId: string) => {
    setSessions((prev) => {
      const next = new Map(prev)
      next.delete(sessionId)
      return next
    })
  }, [])

  const onSessionsCleared = useCallback(() => setSessions(new Map()), [])

  usePresenceWebSocket({
    onSessionUpdate,
    onSessionRemove,
    onSessionsCleared,
    enabled: true,
  })

  return useMemo(() => {
    const map = new Map<string, AttentionKind>()
    for (const s of sessions.values()) {
      if (!s.tmux_pane) continue
      const kind = paneAttentionKind(s)
      if (kind) map.set(s.tmux_pane, kind)
    }
    return map
  }, [sessions])
}
