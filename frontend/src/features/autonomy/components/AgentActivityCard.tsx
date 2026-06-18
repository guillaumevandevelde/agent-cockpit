import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient, buildEndpoint } from '@/lib/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Terminal, RefreshCw, Eye } from 'lucide-react'
import { CLICKABLE_CARD } from '@/lib/constants'

interface AgentSession {
  tmux_target: string
  session_name: string
  cwd: string
  pid: string
  provider: string
  preview: string | null
  status: string
}

interface AgentActivityResponse {
  agents: AgentSession[]
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

export function AgentActivityCard() {
  const [agents, setAgents] = useState<AgentSession[]>([])
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const navigate = useNavigate()

  const fetchAgents = async () => {
    setLoading(true)
    try {
      const data = await apiClient<AgentActivityResponse>(buildEndpoint('agent-activity/live'))
      setAgents(data.agents)
    } catch {
      setAgents([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAgents()
    const interval = setInterval(fetchAgents, 10000)
    return () => clearInterval(interval)
  }, [])

  return (
    <Card className="md:col-span-2 lg:col-span-3">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Terminal className="h-5 w-5" />
              Live Agent Sessions
            </CardTitle>
            <CardDescription>
              Currently running agent sessions across all providers
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline">{agents.length} active</Badge>
            <Button
              variant="ghost"
              size="sm"
              onClick={fetchAgents}
              disabled={loading}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {agents.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-4">
            No active agent sessions. Start one from the Agent Bridge.
          </p>
        ) : (
          <div className="space-y-2">
            {agents.map((agent) => (
              <div
                key={agent.tmux_target}
                className={CLICKABLE_CARD + ' rounded-lg border p-3'}
                onClick={() => setExpanded(expanded === agent.tmux_target ? null : agent.tmux_target)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">{agent.session_name || agent.tmux_target}</span>
                        <Badge variant="outline" className="text-xs">{agent.provider}</Badge>
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[agent.status] ?? STATUS_COLORS.active}`}>
                          {STATUS_LABELS[agent.status] ?? agent.status}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground truncate mt-0.5">
                        {agent.cwd}
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
                {expanded === agent.tmux_target && agent.preview && (
                  <pre className="mt-2 rounded bg-muted p-2 text-xs overflow-x-auto max-h-32 overflow-y-auto">
                    {agent.preview}
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
