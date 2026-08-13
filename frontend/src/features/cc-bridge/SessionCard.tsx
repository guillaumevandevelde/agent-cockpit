import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, GitBranch, Pencil, Trash2, KanbanSquare } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { CLICKABLE_CARD } from '@/lib/constants'
import { cn } from '@/lib/utils'
import { fetchSessionGitStatus } from './api'
import type { CCSession, GitStatusResponse } from './types'
import { isVendorEqualToCli } from '@/types/providers'
import type { InstanceIdentity } from '@/types/status'

interface SessionCardProps {
  session: CCSession
  gridPosition: number | null
  onClick: () => void
  onKill: (session: CCSession) => void
  onRename: (session: CCSession, newName: string) => Promise<void>
  /**
   * Open the kanban card the dispatcher associated with this session. Only
   * present when `session.card_id` is set; the SessionCard only renders the
   * affordance in that case.
   */
  onOpenCard?: (cardId: string) => void
  instance?: InstanceIdentity | null
}

export function SessionCard({ session, gridPosition, onClick, onKill, onRename, onOpenCard, instance }: SessionCardProps) {
  const projectName = session.cwd.split('/').pop() || session.cwd
  const isActive = gridPosition !== null

  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(session.session_name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Set when Escape cancels, so the input's onBlur (which fires as the input
  // unmounts) skips the commit it would otherwise trigger.
  const cancelledRef = useRef(false)

  const [gitStatus, setGitStatus] = useState<GitStatusResponse | null>(null)

  // On-demand git status: fetched once when the card mounts (no polling).
  useEffect(() => {
    let active = true
    fetchSessionGitStatus(session.tmux_target)
      .then((status) => { if (active) setGitStatus(status) })
      .catch(() => { if (active) setGitStatus(null) })
    return () => { active = false }
  }, [session.tmux_target])

  function startEdit() {
    cancelledRef.current = false
    setValue(session.session_name)
    setError(null)
    setEditing(true)
  }

  function cancelEdit() {
    cancelledRef.current = true
    setEditing(false)
    setError(null)
  }

  async function commitEdit() {
    const next = value.trim()
    if (!next || next === session.session_name) {
      cancelEdit()
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onRename(session, next)
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Rename failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card
      className={cn(CLICKABLE_CARD, isActive && 'border-primary bg-primary/5')}
      onClick={editing ? undefined : onClick}
      onKeyDown={(e) => {
        if (editing) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      tabIndex={editing ? -1 : 0}
      role="button"
    >
      <CardContent className="p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 min-w-0">
            {editing ? (
              <Input
                autoFocus
                value={value}
                disabled={saving}
                className="h-6 text-sm py-0"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') { e.preventDefault(); void commitEdit() }
                  if (e.key === 'Escape') { e.preventDefault(); cancelEdit() }
                }}
                onChange={(e) => setValue(e.target.value)}
                onBlur={() => { if (!saving && !cancelledRef.current) void commitEdit() }}
              />
            ) : (
              <span className="text-sm font-medium truncate">{session.session_name}</span>
            )}
          </div>
          {!editing && (
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                type="button"
                aria-label="Rename session"
                className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground/50 hover:text-foreground transition-colors"
                onClick={(e) => { e.stopPropagation(); startEdit() }}
                onKeyDown={(e) => e.stopPropagation()}
                title="Rename session"
              >
                <Pencil className="h-3 w-3" />
              </button>
              <button
                type="button"
                aria-label="Kill session"
                className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground/50 hover:text-destructive transition-colors"
                onClick={(e) => { e.stopPropagation(); onKill(session) }}
                onKeyDown={(e) => e.stopPropagation()}
                title="Kill session"
              >
                <Trash2 className="h-3 w-3" />
              </button>
              {isActive ? (
                <span className="h-5 w-5 flex items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
                  {gridPosition + 1}
                </span>
              ) : (
                <span className="h-2 w-2 rounded-full bg-green-500" />
              )}
            </div>
          )}
        </div>
        {error && (
          <p className="text-xs text-destructive mt-1">{error}</p>
        )}
        <div className="flex flex-wrap items-center gap-1 mt-2">
          <Badge variant="outline" className="max-w-full truncate">
            {session.cli_display_name}
          </Badge>
          {!isVendorEqualToCli(session.provider, session.cli) && (
            <Badge
              variant="secondary"
              className="max-w-full truncate"
              title={`Subscription: ${session.provider_display_name}`}
            >
              {session.provider_display_name}
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground truncate mt-1" title={session.cwd}>
          {projectName}
        </p>
        <p
          className="text-xs text-muted-foreground mt-0.5 truncate"
          title={instance ? `${instance.name} · tmux: ${session.tmux_target}` : session.tmux_target}
        >
          {instance ? `${instance.name} · ` : ''}tmux: {session.tmux_target}
        </p>
        {session.card_id && onOpenCard && (
          <button
            type="button"
            aria-label={`View kanban card: ${session.card_title || `#${session.card_id.slice(0, 8)}`}`}
            className="flex items-center gap-1.5 mt-1 text-xs text-muted-foreground hover:text-primary transition-colors min-w-0 w-full text-left"
            onClick={(e) => {
              // The card root toggles the pane grid; without
              // stopPropagation the bridge would also attach this pane
              // the moment the operator clicks through to the board.
              e.stopPropagation()
              onOpenCard(session.card_id!)
            }}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onOpenCard(session.card_id!)
              }
            }}
            title={session.card_title || `Card ${session.card_id}`}
          >
            <KanbanSquare className="h-3 w-3 shrink-0" />
            <span className="truncate font-medium">
              {session.card_title || `#${session.card_id.slice(0, 8)}`}
            </span>
          </button>
        )}
        {gitStatus?.is_git_repo && (
          <div
            className="flex items-center gap-1.5 mt-1 text-xs text-muted-foreground"
            title={gitStatus.dirty ? 'Uncommitted changes' : 'Working tree clean'}
          >
            <GitBranch className="h-3 w-3 shrink-0" />
            <span className="truncate font-medium">
              {gitStatus.detached ? 'detached' : gitStatus.branch ?? 'unknown'}
            </span>
            {gitStatus.dirty && (
              <span
                className="h-1.5 w-1.5 rounded-full bg-amber-500 shrink-0"
                title="Uncommitted changes"
              />
            )}
            {gitStatus.ahead > 0 && (
              <span className="flex items-center" title={`${gitStatus.ahead} ahead of upstream`}>
                <ArrowUp className="h-3 w-3" />{gitStatus.ahead}
              </span>
            )}
            {gitStatus.behind > 0 && (
              <span className="flex items-center" title={`${gitStatus.behind} behind upstream`}>
                <ArrowDown className="h-3 w-3" />{gitStatus.behind}
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
