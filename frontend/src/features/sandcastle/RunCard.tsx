import { useState, useEffect, useRef } from 'react'
import { ChevronDown, ChevronRight, XCircle, Trash2, Radio } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { CLICKABLE_CARD } from '@/lib/constants'
import { getSandcastleRunLogs, streamSandcastleRunLogs } from './api'
import type { SandcastleRun } from './types'
import { StatusBadge } from './sandcastleUtils'
import { shortPath } from './shortPath'

interface RunCardProps {
  run: SandcastleRun
  onCancel: (id: number) => void
  onDelete: (id: number) => void
}

export function RunCard({ run, onCancel, onDelete }: RunCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [logs, setLogs] = useState<string | null>(null)
  const logsEndRef = useRef<HTMLDivElement>(null)

  const isActive = run.status === 'running' || run.status === 'pending'
  // isLive: SSE stream open (active run, panel expanded)
  const isLive = expanded && isActive
  // loadingLogs: panel open, terminal run, not yet fetched
  const loadingLogs = expanded && !isActive && logs === null

  // SSE stream: open when expanded+active, close on unmount or collapse
  useEffect(() => {
    if (!isLive) return
    const es = streamSandcastleRunLogs(
      run.id,
      (data) => {
        const content = (data.log_content as string | undefined) || (data.stdout as string | undefined)
        if (content) setLogs(content)
      },
    )
    return () => es.close()
  }, [isLive, run.id])

  // Fetch logs once when panel opens for a terminal run (logs===null guards re-fetch)
  useEffect(() => {
    if (!expanded || isActive || logs !== null) return
    let cancelled = false
    getSandcastleRunLogs(run.id).then((data) => {
      if (!cancelled) setLogs(data.log_content || data.stdout || '')
    }).catch(() => {
      if (!cancelled) setLogs('')
    })
    return () => { cancelled = true }
  }, [expanded, isActive, logs, run.id])

  // Auto-scroll to bottom while streaming
  useEffect(() => {
    if (isLive && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, isLive])

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        className={`${CLICKABLE_CARD} rounded-lg p-4 flex items-start gap-3`}
        onClick={() => setExpanded((e) => !e)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpanded((v) => !v) }}
      >
        <span className="mt-1 text-muted-foreground">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <StatusBadge status={run.status} />
            {run.branch && <Badge variant="outline" className="font-mono text-xs">{run.branch}</Badge>}
            {run.commits && <Badge variant="outline">{run.commits.length} commits</Badge>}
            <span className="text-xs text-muted-foreground font-mono">{shortPath(run.project_path)}</span>
          </div>
          <p className="text-sm truncate">{run.prompt}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {run.started_at && `Started ${new Date(run.started_at).toLocaleString()}`}
            {run.completed_at && ` • Completed ${new Date(run.completed_at).toLocaleString()}`}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
          {run.status === 'running' ? (
            <Button
              variant="ghost"
              size="icon"
              title="Cancel"
              onClick={() => onCancel(run.id)}
            >
              <XCircle className="h-4 w-4 text-destructive" />
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="icon"
              title="Delete run"
              onClick={() => onDelete(run.id)}
            >
              <Trash2 className="h-4 w-4 text-destructive" />
            </Button>
          )}
        </div>
      </div>
      {expanded && (
        <div className="border-l-2 border-border ml-4 pl-4 mt-1 mb-2">
          <div className="space-y-2">
            <div>
              <p className="text-xs font-semibold text-muted-foreground mb-1 uppercase tracking-wide">Prompt</p>
              <pre className="text-xs bg-muted p-2 rounded overflow-auto max-h-32">{run.prompt}</pre>
            </div>
            {loadingLogs && (
              <div className="text-xs text-muted-foreground">Loading logs...</div>
            )}
            {(logs !== null || isLive) && (
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Agent Logs</p>
                  {isLive && (
                    <span className="flex items-center gap-1 text-xs text-green-600 font-medium">
                      <Radio className="h-3 w-3 animate-pulse" /> Live
                    </span>
                  )}
                </div>
                <pre className="text-xs bg-muted p-2 rounded overflow-auto max-h-64 font-mono whitespace-pre-wrap">
                  {logs || '(waiting for output…)'}
                  <div ref={logsEndRef} />
                </pre>
              </div>
            )}
            {run.stdout && logs === null && !isLive && (
              <div>
                <p className="text-xs font-semibold text-muted-foreground mb-1 uppercase tracking-wide">Output</p>
                <pre className="text-xs bg-muted p-2 rounded overflow-auto max-h-48">{run.stdout}</pre>
              </div>
            )}
            {run.error && (
              <div>
                <p className="text-xs font-semibold text-destructive mb-1 uppercase tracking-wide">Error</p>
                <pre className="text-xs bg-destructive/10 text-destructive p-2 rounded overflow-auto max-h-32">{run.error}</pre>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
