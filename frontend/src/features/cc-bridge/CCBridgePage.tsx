import { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MonitorPlay, Monitor } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCCSessions } from './useCCSessions'
import { useTeams } from './useTeams'
import { SessionList } from './SessionList'
import { TerminalView } from './TerminalView'
import { LeaderHintOverlay } from './LeaderHintOverlay'
import { NewSessionDialog } from './NewSessionDialog'
import { KillSessionDialog } from './KillSessionDialog'
import { renameSession } from './api'
import { resolveLeaderNavigationTarget } from './leaderNavigation'
import type { CCSession, LeaderNavigationDirection } from './types'
import { useProviderContext } from '@/contexts/ProviderContext'
import { useSystemStatus } from '@/hooks/useSystemStatus'
import type { AgenticCliId, AgenticCliStatus } from '@/types/providers'

const MAX_GRID_PANES = 4
const FOCUSED_PANE_RING_CLASS =
  "after:pointer-events-none after:absolute after:inset-0 after:z-10 after:ring-2 after:ring-primary after:ring-inset after:content-['']"
type ProviderFilter = 'all' | AgenticCliId

const PROVIDER_FILTERS: { value: ProviderFilter; label: string }[] = [
  { value: 'all', label: 'All runs' },
  { value: 'claude-code', label: 'Claude Code' },
  { value: 'codex-cli', label: 'Codex' },
  { value: 'copilot-cli', label: 'Copilot' },
  { value: 'mimo-code', label: 'MiMoCode' },
  { value: 'open-code', label: 'OpenCode' },
]

function addTarget(prev: string[], target: string): string[] {
  if (prev.includes(target)) return prev
  if (prev.length >= MAX_GRID_PANES) return prev
  return [...prev, target]
}

export function CCBridgePage() {
  const [providerFilter, setProviderFilter] = useState<ProviderFilter>('all')
  const { providers, selectedProviderId } = useProviderContext()
  const status = useSystemStatus()
  const instance = status?.instance ?? null
  const { sessions, loading, error, refresh } = useCCSessions()
  const { teams, ungrouped, loading: teamsLoading, refresh: refreshTeams } = useTeams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [activeTargets, setActiveTargets] = useState<string[]>([])
  const [fullscreenTarget, setFullscreenTarget] = useState<string | null>(null)
  const [focusedTarget, setFocusedTarget] = useState<string | null>(null)
  const [leaderHintTarget, setLeaderHintTarget] = useState<string | null>(null)
  const [newSessionOpen, setNewSessionOpen] = useState(false)
  const [killSession, setKillSession] = useState<CCSession | null>(null)

  const isFullscreen = fullscreenTarget !== null
  const displayedTargets = useMemo(
    () => (isFullscreen && fullscreenTarget ? [fullscreenTarget] : activeTargets),
    [isFullscreen, fullscreenTarget, activeTargets]
  )
  const visibleSessions = providerFilter === 'all'
    ? sessions
    : sessions.filter((session) => session.cli === providerFilter)

  const visibleTeams = providerFilter === 'all'
    ? teams
    : teams.filter((team) => team.cli === providerFilter)

  const visibleUngrouped = providerFilter === 'all'
    ? ungrouped
    : ungrouped.filter((s) => s.cli === providerFilter)

  const providersById = providers.reduce<Partial<Record<AgenticCliId, AgenticCliStatus>>>((acc, provider) => {
    acc[provider.id] = provider
    return acc
  }, {})

  const canProviderSpawn = (provider: AgenticCliStatus | undefined) => (
    Boolean(provider?.installed)
    && provider?.capability_details?.spawn?.state !== 'unsupported'
    && provider?.capability_details?.spawn?.state !== 'read_only'
    && provider?.capabilities.spawn === true
  )

  const selectedFilterProvider = providerFilter === 'all' ? null : providersById[providerFilter]
  const canCreateSession = providerFilter === 'all'
    ? providers.some(canProviderSpawn)
    : canProviderSpawn(selectedFilterProvider ?? undefined)
  const createDisabledReason = providerFilter === 'all'
    ? 'No installed provider can launch sessions.'
    : !selectedFilterProvider
      ? 'Provider metadata is not available.'
      : !selectedFilterProvider.installed
        ? `${selectedFilterProvider.display_name} is not installed.`
        : selectedFilterProvider.capability_details?.spawn?.reason
          ?? `${selectedFilterProvider.display_name} cannot launch runs from Agent Bridge.`

  const filterCounts: Record<ProviderFilter, number> = {
    all: sessions.length,
    'claude-code': sessions.filter((session) => session.cli === 'claude-code').length,
    'codex-cli': sessions.filter((session) => session.cli === 'codex-cli').length,
    'copilot-cli': sessions.filter((session) => session.cli === 'copilot-cli').length,
    'mimo-code': sessions.filter((session) => session.cli === 'mimo-code').length,
    'open-code': sessions.filter((session) => session.cli === 'open-code').length,
  }

  const initialDialogProvider = providerFilter === 'all' ? selectedProviderId : providerFilter

  const handleLeaderNavigate = useCallback((sourceTarget: string, direction: LeaderNavigationDirection) => {
    setFocusedTarget(sourceTarget)
    const nextTarget = resolveLeaderNavigationTarget(displayedTargets, sourceTarget, direction)
    if (nextTarget) setFocusedTarget(nextTarget)
  }, [displayedTargets])

  const handleLeaderStateChange = useCallback((sourceTarget: string, active: boolean) => {
    if (active) {
      setFocusedTarget(sourceTarget)
      setLeaderHintTarget(sourceTarget)
      return
    }
    setLeaderHintTarget((current) => (current === sourceTarget ? null : current))
  }, [])

  useEffect(() => {
    if (!isFullscreen) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreenTarget(null)
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [isFullscreen])

  const toggleTarget = useCallback((target: string) => {
    setActiveTargets((prev) =>
      prev.includes(target) ? prev.filter((t) => t !== target) : addTarget(prev, target)
    )
    setFullscreenTarget((cur) => (cur === target ? null : cur))
  }, [])

  const removeTarget = useCallback((target: string) => {
    setActiveTargets((prev) => prev.filter((t) => t !== target))
    setFullscreenTarget((cur) => (cur === target ? null : cur))
  }, [])

  // Attach even when the grid is full (drop the oldest) — used for the
  // notification deep-link so it always lands on the requested pane.
  const attachTarget = useCallback((target: string) => {
    setActiveTargets((prev) => {
      if (prev.includes(target)) return prev
      if (prev.length >= MAX_GRID_PANES) return [...prev.slice(1), target]
      return [...prev, target]
    })
    setFocusedTarget(target)
  }, [])

  // Resolve ?attach=<pane_id> to a discovered session's tmux_target, attach it,
  // then clear the param. If the pane isn't discovered yet, refresh and retry.
  useEffect(() => {
    const pane = searchParams.get('attach')
    if (!pane) return
    const match = sessions.find((s) => s.pane_id === pane)
    if (match) {
      attachTarget(match.tmux_target)
      const next = new URLSearchParams(searchParams)
      next.delete('attach')
      setSearchParams(next, { replace: true })
    } else {
      refresh()
    }
  }, [searchParams, sessions, attachTarget, refresh, setSearchParams])

  const handleSpawned = (tmuxTarget: string) => {
    refresh()
    refreshTeams()
    setActiveTargets((prev) => addTarget(prev, tmuxTarget))
  }

  const handleRename = useCallback(async (session: CCSession, newName: string) => {
    const res = await renameSession(session.session_name, newName)
    const oldTarget = session.tmux_target
    const newTarget = res.tmux_target
    setActiveTargets((prev) => prev.map((t) => (t === oldTarget ? newTarget : t)))
    setFocusedTarget((cur) => (cur === oldTarget ? newTarget : cur))
    setFullscreenTarget((cur) => (cur === oldTarget ? newTarget : cur))
    refresh()
    refreshTeams()
  }, [refresh, refreshTeams])

  const handleKilled = () => {
    if (killSession) {
      removeTarget(killSession.tmux_target)
    }
    setKillSession(null)
    refresh()
    refreshTeams()
  }

  // Navigate from an agent-bridge session to the kanban card that
  // dispatched it. The KanbanPage already supports a `?card=<id>` deep-link
  // (card-references-analysis §2.4/§D2) and falls back to a project-agnostic
  // `getCard` lookup when the active project doesn't carry that card, so we
  // don't need to switch the active project here — passing the card id alone
  // is enough.
  const handleOpenCard = useCallback(
    (cardId: string) => {
      navigate(`/kanban?card=${encodeURIComponent(cardId)}`)
    },
    [navigate]
  )

  const gridCols = activeTargets.length <= 1 ? 'grid-cols-1' : 'grid-cols-2'
  const showLeaderHint = leaderHintTarget !== null && displayedTargets.includes(leaderHintTarget)

  return (
    <div className={cn(
      'flex flex-col',
      isFullscreen
        ? 'fixed inset-0 z-50 bg-background'
        : 'h-[calc(100vh-8.5rem)] border rounded-lg overflow-hidden'
    )}>
      {showLeaderHint && <LeaderHintOverlay />}

      {!isFullscreen && (
        <div className="flex items-center gap-3 px-4 py-3 border-b shrink-0 bg-muted/30">
          <MonitorPlay className="h-5 w-5 shrink-0" />
          <div className="flex items-baseline gap-2 flex-wrap min-w-0">
            <h1 className="text-base font-semibold">Agent Bridge</h1>
            <span className="text-xs text-muted-foreground">
              Discover and observe Claude Code and Codex sessions running in tmux. Select up to 4 sessions to monitor simultaneously.
            </span>
          </div>
          <div className="ml-auto flex rounded-md bg-background border p-0.5 shrink-0">
            {PROVIDER_FILTERS.map(({ value, label }) => (
              <button
                key={value}
                type="button"
                className={cn(
                  'px-2.5 py-1 text-xs rounded-sm transition-colors',
                  providerFilter === value
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                )}
                onClick={() => setProviderFilter(value)}
              >
                {label} <span className="opacity-70">({filterCounts[value]})</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        {!isFullscreen && (
          <div className="w-52 border-r shrink-0">
            <SessionList
              sessions={visibleSessions}
              teams={visibleTeams}
              ungrouped={visibleUngrouped}
              loading={loading || teamsLoading}
              error={error}
              activeTargets={activeTargets}
              onToggleTarget={toggleTarget}
              onRefresh={() => { refresh(); refreshTeams() }}
              onNewSession={() => setNewSessionOpen(true)}
              onKillSession={setKillSession}
              onRename={handleRename}
              onOpenCard={handleOpenCard}
              providerFilter={providerFilter}
              canCreateSession={canCreateSession}
              createDisabledReason={canCreateSession ? null : createDisabledReason}
              instance={instance}
            />
          </div>
        )}

        <div className="flex-1 min-w-0 relative">
          {activeTargets.length === 0 ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-muted-foreground bg-background">
              <Monitor className="h-12 w-12 mb-3" />
              <p className="text-sm">Select a session to attach</p>
            </div>
          ) : (
            <div className={cn(
              'absolute inset-0 grid auto-rows-fr',
              isFullscreen ? 'grid-cols-1' : gridCols
            )}>
              {activeTargets.map((target) => {
                const isThisFullscreen = fullscreenTarget === target
                const hidden = isFullscreen && !isThisFullscreen
                return (
                  <div
                    key={target}
                    className={cn(
                      hidden
                        ? 'hidden'
                        : 'relative min-h-0 min-w-0 overflow-hidden',
                      !isFullscreen && !hidden && 'border-b border-r last:border-r-0',
                      !hidden && focusedTarget === target && FOCUSED_PANE_RING_CLASS,
                    )}
                    onMouseDown={() => setFocusedTarget(target)}
                    onFocusCapture={() => setFocusedTarget(target)}
                  >
                    <div className={cn(
                      'absolute inset-[2px] rounded-sm',
                      hidden && 'inset-0 rounded-none',
                    )}>
                      <TerminalView
                        target={target}
                        fullscreen={isThisFullscreen}
                        focused={focusedTarget === target}
                        onLeaderNavigate={handleLeaderNavigate}
                        onLeaderStateChange={handleLeaderStateChange}
                        onToggleFullscreen={() =>
                          setFullscreenTarget(isThisFullscreen ? null : target)
                        }
                        onClose={() => removeTarget(target)}
                        instance={instance}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      <NewSessionDialog
        open={newSessionOpen}
        onOpenChange={setNewSessionOpen}
        onSpawned={handleSpawned}
        initialProvider={initialDialogProvider}
      />

      <KillSessionDialog
        open={killSession !== null}
        onOpenChange={(open) => { if (!open) setKillSession(null) }}
        session={killSession}
        isWorktreeSession={false}
        onKilled={handleKilled}
        instance={instance}
      />
    </div>
  )
}
