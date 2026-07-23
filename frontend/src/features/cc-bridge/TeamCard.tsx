import { useState } from 'react'
import { ChevronDown, ChevronRight, Users } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { CLICKABLE_CARD } from '@/lib/constants'
import { SessionCard } from './SessionCard'
import type { RunGroup, CCSession } from './types'
import type { AttentionKind } from './attention'
import type { InstanceIdentity } from '@/types/status'

interface TeamCardProps {
  team: RunGroup
  activeTargets: string[]
  onToggleTarget: (target: string) => void
  onKillSession: (session: CCSession) => void
  onRename: (session: CCSession, newName: string) => Promise<void>
  attentionByPane: Map<string, AttentionKind>
  instance?: InstanceIdentity | null
}

export function TeamCard({
  team,
  activeTargets,
  onToggleTarget,
  onKillSession,
  onRename,
  attentionByPane,
  instance,
}: TeamCardProps) {
  const [collapsed, setCollapsed] = useState(false)
  const allAttached = team.members.every((m) => activeTargets.includes(m.tmux_target))
  const someAttached = team.members.some((m) => activeTargets.includes(m.tmux_target))

  return (
    <Card className={cn(CLICKABLE_CARD, someAttached && 'border-primary/50')}>
      <CardContent className="p-0">
        {/* Team header — clickable to toggle collapse */}
        <button
          type="button"
          className="flex items-center gap-2 w-full p-3 text-left hover:bg-accent/30 transition-colors"
          onClick={() => setCollapsed(!collapsed)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              setCollapsed(!collapsed)
            }
          }}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="text-sm font-medium truncate">{team.name}</span>
          <Badge variant="outline" className="text-xs shrink-0 ml-1">
            {team.members.length}
          </Badge>
          {team.is_auto_detected && (
            <span className="text-[10px] text-muted-foreground/60 italic ml-auto">
              auto
            </span>
          )}
          {allAttached && (
            <span className="h-2 w-2 rounded-full bg-green-500 shrink-0 ml-1" title="All sessions attached" />
          )}
          {someAttached && !allAttached && (
            <span className="h-2 w-2 rounded-full bg-yellow-500 shrink-0 ml-1" title="Some sessions attached" />
          )}
        </button>

        {/* Collapsible member list */}
        {!collapsed && (
          <div className="border-t">
            <div className="py-1 px-2 bg-muted/20">
              <span className="text-[11px] text-muted-foreground/70 uppercase tracking-wider">
                {team.cli_display_name || team.cli}
              </span>
            </div>
            <div className="divide-y">
              {team.members.map((session) => {
                const pos = activeTargets.indexOf(session.tmux_target)
                return (
                  <div key={session.pane_id || session.session_name} className="px-2 py-1">
                    <SessionCard
                      session={session}
                      gridPosition={pos === -1 ? null : pos}
                      onClick={() => onToggleTarget(session.tmux_target)}
                      onKill={onKillSession}
                      onRename={onRename}
                      attention={session.pane_id ? attentionByPane.get(session.pane_id) ?? null : null}
                      instance={instance}
                    />
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
