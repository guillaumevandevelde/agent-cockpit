import { Loader2, RefreshCw, MonitorX, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { SessionCard } from './SessionCard'
import { TeamCard } from './TeamCard'
import type { CCSession, RunGroup } from './types'
import type { AgenticCliId } from '@/types/providers'
import type { InstanceIdentity } from '@/types/status'

type ProviderFilter = 'all' | AgenticCliId

interface SessionListProps {
  sessions: CCSession[]
  teams: RunGroup[]
  ungrouped: CCSession[]
  loading: boolean
  error: string | null
  activeTargets: string[]
  onToggleTarget: (target: string) => void
  onRefresh: () => void
  onNewSession: () => void
  onKillSession: (session: CCSession) => void
  onRename: (session: CCSession, newName: string) => Promise<void>
  onOpenCard?: (cardId: string) => void
  providerFilter: ProviderFilter
  canCreateSession: boolean
  createDisabledReason: string | null
  instance?: InstanceIdentity | null
}

export function SessionList({
  sessions,
  teams,
  ungrouped,
  loading,
  error,
  activeTargets,
  onToggleTarget,
  onRefresh,
  onNewSession,
  onKillSession,
  onRename,
  onOpenCard,
  providerFilter,
  canCreateSession,
  createDisabledReason,
  instance,
}: SessionListProps) {
  const totalCount = sessions.length
  const emptyName = providerFilter === 'all'
    ? 'run'
    : providerFilter === 'codex-cli' ? 'Codex'
    : providerFilter === 'mimo-code' ? 'MiMoCode'
    : 'Claude Code'
  const emptyHint = createDisabledReason
    ?? (providerFilter === 'all'
      ? 'Launch or start a supported CLI in tmux.'
      : `Launch ${emptyName} from Agent Bridge or start it in tmux.`)

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between p-3 border-b">
        <span className="text-sm font-medium">
          Runs ({totalCount})
          {teams.length > 0 && (
            <span className="text-muted-foreground ml-1">
              · {teams.length} group{teams.length !== 1 ? 's' : ''}
            </span>
          )}
        </span>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onNewSession}
            disabled={!canCreateSession}
            title={createDisabledReason ?? 'New session'}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onRefresh} title="Refresh">
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {loading && totalCount === 0 && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {error && (
          <p className="text-sm text-destructive p-2">{error}</p>
        )}

        {!loading && !error && totalCount === 0 && (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
            <MonitorX className="h-8 w-8 mb-2" />
            <p className="text-sm">No {emptyName} sessions found</p>
            <p className="text-xs mt-1 text-center px-3">{emptyHint}</p>
          </div>
        )}

        {/* Render team cards */}
        {teams.map((team) => (
          <TeamCard
            key={team.team_id}
            team={team}
            activeTargets={activeTargets}
            onToggleTarget={onToggleTarget}
            onKillSession={onKillSession}
            onRename={onRename}
            onOpenCard={onOpenCard}
            instance={instance}
          />
        ))}

        {/* Render ungrouped sessions */}
        {ungrouped.map((session) => {
          const pos = activeTargets.indexOf(session.tmux_target)
          return (
            <SessionCard
              key={session.pane_id}
              session={session}
              gridPosition={pos === -1 ? null : pos}
              onClick={() => onToggleTarget(session.tmux_target)}
              onKill={onKillSession}
              onRename={onRename}
              onOpenCard={onOpenCard}
              instance={instance}
            />
          )
        })}
      </div>
    </div>
  )
}
