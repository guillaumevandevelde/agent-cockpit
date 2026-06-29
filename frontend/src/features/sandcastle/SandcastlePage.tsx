import { useState, useEffect, useCallback, useRef } from 'react'
import { Castle, Plus, ChevronDown, ChevronRight, Loader2, XCircle, CheckCircle, AlertCircle, Trash2, AlertTriangle, Container, Radio } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { CLICKABLE_CARD, MODAL_SIZES } from '@/lib/constants'
import { useProjectContext } from '@/contexts/ProjectContext'
import {
  getSandcastleConfig,
  updateSandcastleConfig,
  toggleSandcastleConfig,
  startSandcastleRun,
  startParallelSandcastleRuns,
  listSandcastleRuns,
  cancelSandcastleRun,
  deleteSandcastleRun,
  clearSandcastleRuns,
  checkSandcastleHealth,
  buildSandcastleImage,
  getSandcastleStats,
  getSandcastleRunLogs,
  streamSandcastleRunLogs,
  listSandcastleContainers,
} from './api'
import type { SandcastleConfig, SandcastleRun, SandcastleHealth, SandcastleStats, SandcastleContainer } from './types'

function StatusBadge({ status }: { status: SandcastleRun['status'] }) {
  const variants: Record<SandcastleRun['status'], { icon: React.ReactNode; className: string }> = {
    pending: { icon: <Loader2 className="h-3 w-3 animate-spin" />, className: 'bg-blue-500 text-white' },
    running: { icon: <Loader2 className="h-3 w-3 animate-spin" />, className: 'bg-yellow-500 text-white' },
    completed: { icon: <CheckCircle className="h-3 w-3" />, className: 'bg-green-500 text-white' },
    failed: { icon: <XCircle className="h-3 w-3" />, className: 'bg-red-500 text-white' },
    cancelled: { icon: <AlertCircle className="h-3 w-3" />, className: 'bg-muted text-muted-foreground' },
  }
  const { icon, className } = variants[status]
  return <Badge className={`${className} gap-1`}>{icon}{status}</Badge>
}

function shortPath(p: string): string {
  const parts = p.split('/')
  return parts.slice(-2).join('/')
}

interface RunCardProps {
  run: SandcastleRun
  onCancel: (id: number) => void
  onDelete: (id: number) => void
}

function RunCard({ run, onCancel, onDelete }: RunCardProps) {
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

function LiveContainersPanel({ containers }: { containers: SandcastleContainer[] }) {
  if (containers.length === 0) return null
  return (
    <Card className="border-green-500/30">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Container className="h-4 w-4 text-green-600" />
          Live Containers
          <Badge className="bg-green-500 text-white text-xs">{containers.length} running</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {containers.map((c) => (
            <div key={c.id} className="flex items-center gap-3 text-sm font-mono border rounded-md p-2 bg-muted/40">
              <span className="h-2 w-2 rounded-full bg-green-500 shrink-0 animate-pulse" />
              <span className="text-xs text-muted-foreground">{c.runtime}</span>
              <span className="font-medium truncate">{c.name}</span>
              <span className="text-xs text-muted-foreground truncate">{c.image}</span>
              <span className="ml-auto text-xs text-green-700 shrink-0">{c.status}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export function SandcastlePage() {
  const { activeProject } = useProjectContext()
  const [config, setConfig] = useState<SandcastleConfig | null>(null)
  const [runs, setRuns] = useState<SandcastleRun[]>([])
  const [containers, setContainers] = useState<SandcastleContainer[]>([])
  const [loading, setLoading] = useState(true)
  const [health, setHealth] = useState<SandcastleHealth | null>(null)
  const [stats, setStats] = useState<SandcastleStats | null>(null)
  const [showRunDialog, setShowRunDialog] = useState(false)
  const [runPrompt, setRunPrompt] = useState('')
  const [runBranch, setRunBranch] = useState('')
  const [starting, setStarting] = useState(false)
  const [showParallelDialog, setShowParallelDialog] = useState(false)
  const [parallelPrompts, setParallelPrompts] = useState<{ prompt: string; branch_name: string }[]>([
    { prompt: '', branch_name: '' },
    { prompt: '', branch_name: '' },
  ])
  const [startingParallel, setStartingParallel] = useState(false)

  const loadConfig = useCallback(async () => {
    if (!activeProject?.path) return
    try {
      const cfg = await getSandcastleConfig(activeProject.path)
      setConfig(cfg)
    } catch {
      // Config might not exist yet, use defaults
      setConfig({
        id: null,
        project_path: activeProject.path,
        enabled: false,
        sandbox_provider: 'no-sandbox',
        agent_provider: 'claude-code',
        model: null,
        branch_strategy: 'merge-to-head',
        docker_image: null,
        max_iterations: 1,
        idle_timeout_seconds: 600,
        permission_mode: 'acceptEdits',
        created_at: null,
        updated_at: null,
      })
    }
  }, [activeProject?.path])

  const loadRuns = useCallback(async () => {
    if (!activeProject?.path) return
    try {
      const res = await listSandcastleRuns(activeProject.path)
      setRuns(res.runs as SandcastleRun[])
    } catch {
      toast.error('Failed to load runs')
    }
  }, [activeProject?.path])

  const loadHealth = useCallback(async () => {
    try {
      const h = await checkSandcastleHealth()
      setHealth(h)
    } catch {
      // Health check failed
    }
  }, [])

  const loadStats = useCallback(async () => {
    try {
      const s = await getSandcastleStats()
      setStats(s)
    } catch {
      // Stats load failed
    }
  }, [])

  const loadContainers = useCallback(async () => {
    try {
      const res = await listSandcastleContainers()
      setContainers(res.containers)
    } catch {
      // Not critical
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    await Promise.all([loadConfig(), loadRuns(), loadHealth(), loadStats(), loadContainers()])
    setLoading(false)
  }, [loadConfig, loadRuns, loadHealth, loadStats, loadContainers])

  useEffect(() => { load() }, [load])

  // Poll runs + containers every 3s while there are active runs
  const hasActiveRuns = runs.some((r) => r.status === 'running' || r.status === 'pending')
  useEffect(() => {
    if (!hasActiveRuns) return
    const id = setInterval(async () => {
      await Promise.all([loadRuns(), loadContainers(), loadStats()])
    }, 3000)
    return () => clearInterval(id)
  }, [hasActiveRuns, loadRuns, loadContainers, loadStats])

  const handleToggle = async () => {
    if (!config) return
    try {
      if (!config.id) {
        // Create new config
        await updateSandcastleConfig(config.project_path, { enabled: true })
      } else {
        await toggleSandcastleConfig(config.id)
      }
      toast.success(config.enabled ? 'Disabled' : 'Enabled')
      await loadConfig()
    } catch {
      toast.error('Failed to toggle')
    }
  }

  const handleUpdateConfig = async (updates: Partial<SandcastleConfig>) => {
    if (!config) return
    try {
      await updateSandcastleConfig(config.project_path, updates)
      toast.success('Config updated')
      await loadConfig()
    } catch {
      toast.error('Failed to update config')
    }
  }

  const handleStartRun = async () => {
    if (!activeProject?.path || !runPrompt.trim()) return
    setStarting(true)
    try {
      await startSandcastleRun(activeProject.path, {
        prompt: runPrompt.trim(),
        branch_name: runBranch.trim() || undefined,
      })
      toast.success('Run started')
      setShowRunDialog(false)
      setRunPrompt('')
      setRunBranch('')
      await loadRuns()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to start run')
    } finally {
      setStarting(false)
    }
  }

  const handleCancelRun = async (runId: number) => {
    try {
      await cancelSandcastleRun(runId)
      toast.success('Run cancelled')
      await loadRuns()
    } catch {
      toast.error('Failed to cancel run')
    }
  }

  const handleDeleteRun = async (runId: number) => {
    try {
      await deleteSandcastleRun(runId)
      toast.success('Run deleted')
      await Promise.all([loadRuns(), loadStats()])
    } catch {
      toast.error('Failed to delete run')
    }
  }

  const handleClearAll = async () => {
    if (!activeProject?.path || runs.length === 0) return
    if (!window.confirm(
      `Delete all ${runs.length} runs for this project? Any running runs will be cancelled first.`
    )) return
    try {
      const res = await clearSandcastleRuns(activeProject.path, true)
      toast.success(`Cleared ${res.deleted} run${res.deleted === 1 ? '' : 's'}`)
      await Promise.all([loadRuns(), loadStats()])
    } catch {
      toast.error('Failed to clear runs')
    }
  }

  const handleBuildImage = async () => {
    try {
      toast.info('Building Docker image...')
      const result = await buildSandcastleImage()
      if (result.success) {
        toast.success(result.message || 'Image built successfully')
      } else {
        toast.error(result.error || 'Build failed')
      }
      await loadHealth()
    } catch {
      toast.error('Failed to build image')
    }
  }

  const handleStartParallelRuns = async () => {
    if (!activeProject?.path) return
    const validPrompts = parallelPrompts.filter((p) => p.prompt.trim())
    if (validPrompts.length === 0) return

    setStartingParallel(true)
    try {
      await startParallelSandcastleRuns(
        activeProject.path,
        validPrompts.map((p) => ({
          prompt: p.prompt.trim(),
          branch_name: p.branch_name.trim() || undefined,
        })),
        config?.id ?? undefined
      )
      toast.success(`${validPrompts.length} runs started`)
      setShowParallelDialog(false)
      setParallelPrompts([{ prompt: '', branch_name: '' }, { prompt: '', branch_name: '' }])
      await loadRuns()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to start parallel runs')
    } finally {
      setStartingParallel(false)
    }
  }

  const addParallelPrompt = () => {
    setParallelPrompts([...parallelPrompts, { prompt: '', branch_name: '' }])
  }

  const removeParallelPrompt = (index: number) => {
    if (parallelPrompts.length > 1) {
      setParallelPrompts(parallelPrompts.filter((_, i) => i !== index))
    }
  }

  const updateParallelPrompt = (index: number, field: 'prompt' | 'branch_name', value: string) => {
    const updated = [...parallelPrompts]
    updated[index][field] = value
    setParallelPrompts(updated)
  }

  if (!activeProject) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-bold flex items-center gap-2">
          <Castle className="h-8 w-8" /> Sandcastle
        </h1>
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground">Select a project to manage sandcastle configuration.</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Castle className="h-8 w-8" /> Sandcastle
          </h1>
          <p className="text-muted-foreground mt-1">
            Run coding agents via sandcastle. Configure Docker or Podman for container isolation.
          </p>
        </div>
        <div className="flex gap-2">
          <RefreshButton onClick={load} loading={loading} />
          <Button variant="outline" onClick={() => setShowParallelDialog(true)} disabled={!config?.enabled}>
            <Plus className="h-4 w-4 mr-2" /> Parallel Runs
          </Button>
          <Button onClick={() => setShowRunDialog(true)} disabled={!config?.enabled}>
            <Plus className="h-4 w-4 mr-2" /> New Run
          </Button>
        </div>
      </div>

      {/* Health Status */}
      {health && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">System Health</CardTitle>
              {health.docker_available && !health.docker_image_exists && (
                <Button variant="outline" size="sm" onClick={handleBuildImage}>
                  Build Docker Image
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-4">
              <div className="flex items-center gap-2">
                <div className={`h-2 w-2 rounded-full ${health.node_available ? 'bg-green-500' : 'bg-red-500'}`} />
                <span className="text-sm">Node.js {health.node_version || '(not found)'}</span>
              </div>
              <div className="flex items-center gap-2">
                <div className={`h-2 w-2 rounded-full ${health.docker_available ? 'bg-green-500' : 'bg-yellow-500'}`} />
                <span className="text-sm">Docker {health.docker_version || '(not found)'}</span>
              </div>
              <div className="flex items-center gap-2">
                <div className={`h-2 w-2 rounded-full ${health.podman_available ? 'bg-green-500' : 'bg-yellow-500'}`} />
                <span className="text-sm">Podman {health.podman_version || '(not found)'}</span>
              </div>
              {health.docker_available && (
                <div className="flex items-center gap-2">
                  <div className={`h-2 w-2 rounded-full ${health.docker_image_exists ? 'bg-green-500' : 'bg-yellow-500'}`} />
                  <span className="text-sm">Docker Image {health.docker_image_exists ? '(built)' : '(not built)'}</span>
                </div>
              )}
              <div className="flex items-center gap-2">
                <div className={`h-2 w-2 rounded-full ${health.npm_dependencies_installed ? 'bg-green-500' : 'bg-red-500'}`} />
                <span className="text-sm">npm deps {health.npm_dependencies_installed ? '(installed)' : '(missing)'}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Stats Card */}
      {stats && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Run Statistics</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="text-center">
                <div className="text-2xl font-bold">{stats.total_runs}</div>
                <div className="text-xs text-muted-foreground">Total Runs</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-green-600">{stats.runs_by_status?.completed || 0}</div>
                <div className="text-xs text-muted-foreground">Completed</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-yellow-600">{stats.active_runs}</div>
                <div className="text-xs text-muted-foreground">Active</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-blue-600">{stats.recent_runs_24h}</div>
                <div className="text-xs text-muted-foreground">Last 24h</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Live Containers */}
      <LiveContainersPanel containers={containers} />

      {/* Configuration Card */}
      {config && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle>Configuration</CardTitle>
                <CardDescription>{shortPath(config.project_path)}</CardDescription>
              </div>
              <Switch
                checked={config.enabled}
                onCheckedChange={handleToggle}
              />
            </div>
          </CardHeader>
          {config.enabled && (
            <CardContent className="space-y-4">
              {config.sandbox_provider === 'no-sandbox' && (
                <Alert>
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    <strong>No container isolation.</strong> Agents run directly on the host with full filesystem access. Switch to Docker or Podman for true sandbox isolation.
                  </AlertDescription>
                </Alert>
              )}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Sandbox Provider</Label>
                  <Select
                    value={config.sandbox_provider}
                    onValueChange={(v) => handleUpdateConfig({ sandbox_provider: v as SandcastleConfig['sandbox_provider'] })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="no-sandbox">No Sandbox</SelectItem>
                      <SelectItem value="docker">Docker</SelectItem>
                      <SelectItem value="podman">Podman</SelectItem>
                      <SelectItem value="vercel">Vercel</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Agent Provider</Label>
                  <Select
                    value={config.agent_provider}
                    onValueChange={(v) => handleUpdateConfig({ agent_provider: v as SandcastleConfig['agent_provider'] })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="claude-code">Claude Code</SelectItem>
                      <SelectItem value="codex-cli">Codex CLI</SelectItem>
                      <SelectItem value="open-code">Open Code</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Branch Strategy</Label>
                  <Select
                    value={config.branch_strategy}
                    onValueChange={(v) => handleUpdateConfig({ branch_strategy: v as SandcastleConfig['branch_strategy'] })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="head">Head (direct write)</SelectItem>
                      <SelectItem value="merge-to-head">Merge to Head</SelectItem>
                      <SelectItem value="branch">Named Branch</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Docker Image (optional)</Label>
                  <input
                    type="text"
                    value={config.docker_image || ''}
                    onChange={(e) => handleUpdateConfig({ docker_image: e.target.value || null })}
                    placeholder="sandcastle:local"
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  />
                </div>
              </div>
            </CardContent>
          )}
        </Card>
      )}

      {/* Runs List */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Runs</CardTitle>
              <CardDescription>{runs.length} total runs</CardDescription>
            </div>
            {runs.length > 0 && (
              <Button variant="outline" size="sm" onClick={handleClearAll}>
                <Trash2 className="h-4 w-4 mr-2" /> Clear all
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <p className="text-muted-foreground text-sm">No runs yet. Click "New Run" to start one.</p>
          ) : (
            <div className="space-y-2">
              {runs.map((run) => (
                <RunCard key={run.id} run={run} onCancel={handleCancelRun} onDelete={handleDeleteRun} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* New Run Dialog */}
      <Dialog open={showRunDialog} onOpenChange={setShowRunDialog}>
        <DialogContent className={MODAL_SIZES.MD}>
          <DialogHeader>
            <DialogTitle>New Sandcastle Run</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Prompt</Label>
              <Textarea
                value={runPrompt}
                onChange={(e) => setRunPrompt(e.target.value)}
                placeholder="Describe the task for the agent..."
                rows={4}
              />
            </div>
            <div className="space-y-2">
              <Label>Branch Name (optional)</Label>
              <input
                type="text"
                value={runBranch}
                onChange={(e) => setRunBranch(e.target.value)}
                placeholder="agent/fix-issue-42"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowRunDialog(false)}>Cancel</Button>
              <Button onClick={handleStartRun} disabled={!runPrompt.trim() || starting}>
                {starting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Start Run
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Parallel Runs Dialog */}
      <Dialog open={showParallelDialog} onOpenChange={setShowParallelDialog}>
        <DialogContent className={MODAL_SIZES.LG}>
          <DialogHeader>
            <DialogTitle>Parallel Sandcastle Runs</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Start multiple agent runs in parallel. Each run will execute independently in its own sandbox.
            </p>
            {parallelPrompts.map((item, index) => (
              <div key={index} className="space-y-2 border rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <Label>Run {index + 1}</Label>
                  {parallelPrompts.length > 1 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => removeParallelPrompt(index)}
                    >
                      <XCircle className="h-4 w-4" />
                    </Button>
                  )}
                </div>
                <Textarea
                  value={item.prompt}
                  onChange={(e) => updateParallelPrompt(index, 'prompt', e.target.value)}
                  placeholder="Describe the task for this agent..."
                  rows={2}
                />
                <input
                  type="text"
                  value={item.branch_name}
                  onChange={(e) => updateParallelPrompt(index, 'branch_name', e.target.value)}
                  placeholder="Branch name (optional)"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
            ))}
            <Button variant="outline" onClick={addParallelPrompt}>
              <Plus className="h-4 w-4 mr-2" /> Add Another Run
            </Button>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowParallelDialog(false)}>Cancel</Button>
              <Button
                onClick={handleStartParallelRuns}
                disabled={parallelPrompts.every((p) => !p.prompt.trim()) || startingParallel}
              >
                {startingParallel && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Start {parallelPrompts.filter((p) => p.prompt.trim()).length} Runs
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}