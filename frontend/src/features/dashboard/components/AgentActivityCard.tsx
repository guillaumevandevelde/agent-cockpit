import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient, buildEndpoint } from '@/lib/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Terminal, RefreshCw, Eye } from 'lucide-react'
import { CLICKABLE_CARD } from '@/lib/constants'
import { cn } from '@/lib/utils'
import type { AgenticCliId } from '@/types/providers'

interface RunPreview {
  tmux_target: string
  session_name: string
  cwd: string
  pid: string
  provider: string
  preview: string | null
  status: string
}

interface RunActivityResponse {
  runs: RunPreview[]
  count: number
}

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300',
  waiting: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300',
  error: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300',
}

const STATUS_LABELS: Record<string, string> = {
  active: 'Running',
  waiting: 'Waiting',
  error: 'Error',
}

type ProviderFilter = 'all' | AgenticCliId

const PROVIDER_FILTERS: { value: ProviderFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'claude-code', label: 'Claude Code' },
  { value: 'codex-cli', label: 'Codex' },
  { value: 'copilot-cli', label: 'Copilot' },
  { value: 'mimo-code', label: 'MiMoCode' },
  { value: 'open-code', label: 'OpenCode' },
]

export function AgentActivityCard() {
  const [runs, setRuns] = useState<RunPreview[]>([])
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [providerFilter, setProviderFilter] = useState<ProviderFilter>('all')
  const navigate = useNavigate()

  const filteredRuns = providerFilter === 'all'
    ? runs
    : runs.filter((run) => run.provider === providerFilter)

  const filterCounts: Record<ProviderFilter, number> = {
    all: runs.length,
    'claude-code': runs.filter((r) => r.provider === 'claude-code').length,
    'codex-cli': runs.filter((r) => r.provider === 'codex-cli').length,
    'copilot-cli': runs.filter((r) => r.provider === 'copilot-cli').length,
    'mimo-code': runs.filter((r) => r.provider === 'mimo-code').length,
    'open-code': runs.filter((r) => r.provider === 'open-code').length,
  }

  const fetchRuns = async () => {
    setLoading(true)
    try {
      const data = await apiClient<RunActivityResponse>(buildEndpoint('agent-activity/live'))
      setRuns(data.runs)
    } catch {
      setRuns([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchRuns()
    const interval = setInterval(fetchRuns, 10000)
    return () => clearInterval(interval)
  }, [])

  return (
    <Card className="md:col-span-2 lg:col-span-3">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Terminal className="h-5 w-5" />
              Live Runs
            </CardTitle>
            <CardDescription>
              Currently running CLI sessions across all providers
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-md bg-background border p-0.5">
              {PROVIDER_FILTERS.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  className={cn(
                    'px-2 py-1 text-xs rounded-sm transition-colors',
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
            <Button
              variant="ghost"
              size="sm"
              onClick={fetchRuns}
              disabled={loading}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {filteredRuns.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-4">
            {runs.length === 0
              ? 'No active runs. Start one from the Agent Bridge.'
              : `No ${providerFilter === 'all' ? '' : providerFilter + ' '}runs found.`}
          </p>
        ) : (
          <div className="space-y-2">
            {filteredRuns.map((run) => (
              <div
                key={run.tmux_target}
                className={CLICKABLE_CARD + ' rounded-lg border p-3'}
                onClick={() => setExpanded(expanded === run.tmux_target ? null : run.tmux_target)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">{run.session_name || run.tmux_target}</span>
                        <Badge variant="outline" className="text-xs">{run.provider}</Badge>
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[run.status] ?? STATUS_COLORS.active}`}>
                          {STATUS_LABELS[run.status] ?? run.status}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground truncate mt-0.5">
                        {run.cwd}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation()
                        navigate('/agent-bridge')
                      }}
                      title="Open in Agent Bridge"
                    >
                      <Eye className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                {expanded === run.tmux_target && run.preview && (
                  <pre className="mt-2 rounded bg-muted p-2 text-xs overflow-x-auto max-h-32 overflow-y-auto">
                    {run.preview}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
