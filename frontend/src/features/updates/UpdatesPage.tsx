import { useState, useEffect, useCallback, useRef } from 'react'
import { RefreshCw, RotateCw, CheckCircle2, XCircle, AlertTriangle, Terminal, GitCommit, GitBranch, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { ScrollArea } from '@/components/ui/scroll-area'
import { apiClient } from '@/lib/api'

type UpdateStatus = {
  version: string
  commit: string
  branch: string
  update_script_available: boolean
  working_tree_clean: boolean
  update_possible: boolean
}

type LogEntry = {
  event: 'preflight' | 'pulling' | 'building' | 'installing' | 'healthcheck' | 'done' | 'error' | 'log'
  message: string
  data?: Record<string, unknown>
}

function eventIcon(evt: LogEntry['event']) {
  switch (evt) {
    case 'preflight': return <AlertTriangle className="h-4 w-4 text-muted-foreground" />
    case 'pulling': return <RotateCw className="h-4 w-4 text-blue-500" />
    case 'building': return <RefreshCw className="h-4 w-4 text-yellow-500" />
    case 'installing': return <Terminal className="h-4 w-4 text-purple-500" />
    case 'healthcheck': return <Loader2 className="h-4 w-4 text-cyan-500 animate-spin" />
    case 'done': return <CheckCircle2 className="h-4 w-4 text-green-500" />
    case 'error': return <XCircle className="h-4 w-4 text-red-500" />
    default: return <ChevronRight className="h-4 w-4 text-muted-foreground" />
  }
}

function eventColor(evt: LogEntry['event']): string {
  switch (evt) {
    case 'done': return 'text-green-600'
    case 'error': return 'text-red-600'
    default: return 'text-foreground'
  }
}

export function UpdatesPage() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [updating, setUpdating] = useState(false)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [logExpanded, setLogExpanded] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const logEndRef = useRef<HTMLDivElement>(null)

  const fetchStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await apiClient<UpdateStatus>('update/status')
      setStatus(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load update status')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  const handleUpdate = async () => {
    if (updating) return

    setUpdating(true)
    setLogs([])
    setError(null)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const response = await fetch('/api/v1/update/run', {
        signal: controller.signal,
      })

      if (!response.ok) {
        const text = await response.text()
        throw new Error(text || `HTTP ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Parse SSE events from buffer
        const lines = buffer.split('\n')
        buffer = lines.pop() || '' // Keep incomplete line in buffer

        let currentEvent = 'log'
        let currentData = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6).trim()
          } else if (line === '' && currentData) {
            // End of event
            try {
              const parsed = JSON.parse(currentData)
              const entry: LogEntry = {
                event: parsed.event || currentEvent as LogEntry['event'],
                message: parsed.message || '',
                data: parsed.data,
              }
              setLogs(prev => [...prev, entry])

              if (entry.event === 'done') {
                setUpdating(false)
                // Refresh status after a moment
                setTimeout(fetchStatus, 2000)
              } else if (entry.event === 'error') {
                setUpdating(false)
              }
            } catch {
              // Non-JSON data line — skip
            }
            currentEvent = 'log'
            currentData = ''
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError(err instanceof Error ? err.message : 'Update failed')
        setLogs(prev => [...prev, {
          event: 'error',
          message: err instanceof Error ? err.message : 'Update mislukt',
        }])
      }
    } finally {
      setUpdating(false)
      abortRef.current = null
    }
  }

  const handleCancel = () => {
    abortRef.current?.abort()
    setUpdating(false)
  }

  const canUpdate = status?.update_possible && !updating
  const showBlockers = status && !status.update_possible && !updating

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <RefreshCw className="h-8 w-8" />
            Updates
          </h1>
          <p className="text-muted-foreground">
            One-click self-update voor Claude Cockpit
          </p>
        </div>
      </div>

      {error && !updating && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Current Version Info */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            Huidige versie
          </CardTitle>
          <CardDescription>
            Details over de geïnstalleerde Cockpit-versie
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Laden...</p>
          ) : status ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground font-medium">Versie</p>
                <p className="text-lg font-semibold">{status.version}</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground font-medium">Commit</p>
                <p className="flex items-center gap-1 text-sm font-mono">
                  <GitCommit className="h-3 w-3" />
                  {status.commit}
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground font-medium">Branch</p>
                <p className="flex items-center gap-1 text-sm">
                  <GitBranch className="h-3 w-3" />
                  {status.branch}
                  <Badge variant="outline" className="ml-1 text-xs">
                    {status.working_tree_clean ? 'clean' : 'dirty'}
                  </Badge>
                </p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-red-500">Kon status niet laden</p>
          )}
        </CardContent>
      </Card>

      {/* Preflight blockers */}
      {showBlockers && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-amber-600">
              <AlertTriangle className="h-5 w-5" />
              Update niet mogelijk
            </CardTitle>
            <CardDescription>
              Los de volgende problemen op voordat je kunt updaten:
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {!status.working_tree_clean && (
              <div className="flex items-center gap-2 text-sm">
                <XCircle className="h-4 w-4 text-red-500" />
                <span>Werkmap is niet schoon — commit of stash wijzigingen eerst.</span>
              </div>
            )}
            {!status.update_script_available && (
              <div className="flex items-center gap-2 text-sm">
                <XCircle className="h-4 w-4 text-red-500" />
                <span>Update script (scripts/update.sh) niet gevonden of niet uitvoerbaar.</span>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Action buttons */}
      <div className="flex gap-3">
        <Button
          onClick={handleUpdate}
          disabled={!canUpdate}
          size="lg"
          className="gap-2"
        >
          {updating ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          {updating ? 'Bezig met updaten...' : 'Update nu'}
        </Button>

        {updating && (
          <Button
            onClick={handleCancel}
            variant="outline"
            size="lg"
          >
            Annuleren
          </Button>
        )}

        <Button
          onClick={fetchStatus}
          variant="outline"
          size="lg"
          disabled={loading || updating}
        >
          <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Verversen
        </Button>
      </div>

      {/* Progress log */}
      {logs.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <button
                onClick={() => setLogExpanded(!logExpanded)}
                className="flex items-center gap-2 text-sm font-medium"
              >
                {logExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                Voortgangslog ({logs.length})
              </button>
              {logs.some(l => l.event === 'done') && (
                <Badge variant="default" className="bg-green-600">Voltooid</Badge>
              )}
              {logs.some(l => l.event === 'error') && !logs.some(l => l.event === 'done') && (
                <Badge variant="destructive">Mislukt</Badge>
              )}
              {updating && (
                <Badge variant="outline" className="animate-pulse">Bezig...</Badge>
              )}
            </div>
          </CardHeader>
          {logExpanded && (
            <CardContent className="pt-0">
              <ScrollArea className="h-64 rounded border bg-muted/30 p-3">
                <div className="space-y-1.5">
                  {logs.map((entry, i) => (
                    <div key={i} className={`flex items-start gap-2 text-xs ${eventColor(entry.event)}`}>
                      <span className="mt-0.5 shrink-0">{eventIcon(entry.event)}</span>
                      <span className="flex-1">{entry.message}</span>
                    </div>
                  ))}
                  <div ref={logEndRef} />
                </div>
              </ScrollArea>
            </CardContent>
          )}
        </Card>
      )}

      {/* Rollback notice */}
      {logs.some(l => l.event === 'error' && l.data?.rolled_back) && (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Er is een rollback uitgevoerd naar de vorige commit. Cockpit draait weer op de oude versie.
            Check de logs voor details over wat er mis ging.
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
