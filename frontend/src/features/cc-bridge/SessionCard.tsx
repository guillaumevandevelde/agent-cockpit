import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { CLICKABLE_CARD } from '@/lib/constants'
import { cn } from '@/lib/utils'
import type { CCSession } from './types'
import type { AttentionKind } from './attention'

interface SessionCardProps {
  session: CCSession
  gridPosition: number | null
  onClick: () => void
  onKill: (session: CCSession) => void
  onRename: (session: CCSession, newName: string) => Promise<void>
  attention?: AttentionKind | null
}

export function SessionCard({ session, gridPosition, onClick, onKill, onRename, attention }: SessionCardProps) {
  const projectName = session.cwd.split('/').pop() || session.cwd
  const isActive = gridPosition !== null

  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(session.session_name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function startEdit() {
    setValue(session.session_name)
    setError(null)
    setEditing(true)
  }

  function cancelEdit() {
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
            {attention && (
              <span
                className={cn(
                  'h-2 w-2 rounded-full shrink-0',
                  attention === 'error' ? 'bg-red-500' : 'bg-yellow-500'
                )}
                title={attention === 'error' ? 'Command failed' : 'Waiting for input'}
              />
            )}
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
                onBlur={() => { if (!saving) void commitEdit() }}
              />
            ) : (
              <span className="text-sm font-medium truncate">{session.session_name}</span>
            )}
          </div>
          {!editing && (
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground/50 hover:text-foreground transition-colors"
                onClick={(e) => { e.stopPropagation(); startEdit() }}
                onKeyDown={(e) => e.stopPropagation()}
                title="Rename session"
              >
                <Pencil className="h-3 w-3" />
              </button>
              <button
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
        <Badge variant="outline" className="mt-2 max-w-full truncate">
          {session.provider_display_name}
        </Badge>
        <p className="text-xs text-muted-foreground truncate mt-1" title={session.cwd}>
          {projectName}
        </p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {session.tmux_target}
        </p>
      </CardContent>
    </Card>
  )
}
