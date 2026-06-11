import { useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAttention } from '@/contexts/AttentionContext'
import { usePresenceWebSocket } from '@/hooks/usePresenceWebSocket'
import type { PresenceSession } from '@/types/presence'

/** The slice of session state we diff against to detect attention-worthy transitions. */
interface TrackedState {
  status: PresenceSession['status']
  lastNarrativeAt?: string
}

interface AttentionEvent {
  title: string
  body: string
  /** Same tag collapses repeated notifications of one kind for one session. */
  tag: string
}

function sessionLabel(s: PresenceSession): string {
  return s.label || s.session_id.slice(0, 8)
}

/**
 * Compare the previous tracked state with the incoming session and return any
 * attention-worthy transitions. Returns an empty array when nothing changed.
 */
function detectAttention(prev: TrackedState, next: PresenceSession): AttentionEvent[] {
  const events: AttentionEvent[] = []
  const label = sessionLabel(next)

  // Waiting for input: just stopped.
  if (next.status === 'stopped' && prev.status !== 'stopped') {
    events.push({
      title: `🟡 ${label} wacht op je input`,
      body: next.status_text || 'Waiting for input',
      tag: `${next.session_id}:input`,
    })
  }

  // Command failed: just went into error.
  if (next.status === 'error' && prev.status !== 'error') {
    events.push({
      title: `🔴 ${label}: commando faalde`,
      body: next.last_command ? `$ ${next.last_command}` : next.status_text || 'Een commando faalde',
      tag: `${next.session_id}:error`,
    })
  }

  // Permission / notification: a fresh non-generic narrative arrived.
  if (next.last_narrative_at && next.last_narrative_at !== prev.lastNarrativeAt) {
    events.push({
      title: `🔐 ${label}`,
      body: next.last_narrative || 'vraagt je aandacht',
      tag: `${next.session_id}:note`,
    })
  }

  return events
}

/**
 * Mount once (app-wide). When the user has enabled attention notifications and
 * granted permission, fires a clickable desktop notification whenever a session
 * transitions into a state that needs the user: waiting for input, a permission
 * prompt, or a failed command. Clicking focuses the window and opens Presence
 * with that session highlighted.
 */
export function useAttentionNotifications() {
  const { enabled, permission } = useAttention()
  const navigate = useNavigate()
  // Last-seen state per session, for edge detection. First sight seeds silently.
  const tracked = useRef<Map<string, TrackedState>>(new Map())

  const active = enabled && permission === 'granted'

  const fire = useCallback(
    (session: PresenceSession, event: AttentionEvent) => {
      const notification = new Notification(event.title, {
        body: event.body,
        tag: event.tag,
      })
      notification.onclick = () => {
        window.focus()
        // Prefer the Agent Bridge, attaching the exact pane; fall back to
        // Presence when the session isn't running under tmux.
        if (session.tmux_pane) {
          navigate(`/cc-bridge?attach=${encodeURIComponent(session.tmux_pane)}`)
        } else {
          navigate(`/presence?session=${encodeURIComponent(session.session_id)}`)
        }
        notification.close()
      }
    },
    [navigate],
  )

  const onSessionUpdate = useCallback(
    (session: PresenceSession) => {
      const prev = tracked.current.get(session.session_id)
      const nextState: TrackedState = {
        status: session.status,
        lastNarrativeAt: session.last_narrative_at,
      }

      // First time we see this session (incl. the snapshot replayed on connect):
      // seed state without notifying so we don't fire for pre-existing states.
      if (prev) {
        for (const event of detectAttention(prev, session)) {
          fire(session, event)
        }
      }

      tracked.current.set(session.session_id, nextState)
    },
    [fire],
  )

  const onSessionRemove = useCallback((sessionId: string) => {
    tracked.current.delete(sessionId)
  }, [])

  const onSessionsCleared = useCallback(() => {
    tracked.current.clear()
  }, [])

  usePresenceWebSocket({
    onSessionUpdate,
    onSessionRemove,
    onSessionsCleared,
    enabled: active,
  })
}
