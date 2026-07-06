import { useState, useEffect, useCallback } from 'react'
import { Castle, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { useProjectContext } from '@/contexts/ProjectContext'
import { RunGraph } from './RunGraph'
import { RunCard } from './RunCard'
import { LiveContainersPanel } from './LiveContainersPanel'
import { HealthStatusCard } from './HealthStatusCard'
import { StatsCard } from './StatsCard'
import { ConfigurationCard } from './ConfigurationCard'
import { NewRunDialog } from './NewRunDialog'
import { ParallelRunsDialog } from './ParallelRunsDialog'
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
  listSandcastleContainers,
} from './api'
import type { SandcastleConfig, SandcastleRun, SandcastleHealth, SandcastleStats, SandcastleContainer } from './types'

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
  const [parallelPrompts, setParallelPrompts] = useState<{ id: string; prompt: string; branch_name: string }[]>([
    { id: crypto.randomUUID(), prompt: '', branch_name: '' },
    { id: crypto.randomUUID(), prompt: '', branch_name: '' },
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
      toast.info('Building sandcastle image...')
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
      setParallelPrompts([
        { id: crypto.randomUUID(), prompt: '', branch_name: '' },
        { id: crypto.randomUUID(), prompt: '', branch_name: '' },
      ])
      await loadRuns()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to start parallel runs')
    } finally {
      setStartingParallel(false)
    }
  }

  const addParallelPrompt = () => {
    setParallelPrompts([...parallelPrompts, { id: crypto.randomUUID(), prompt: '', branch_name: '' }])
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
      {health && <HealthStatusCard health={health} onBuildImage={handleBuildImage} />}

      {/* Stats Card */}
      {stats && <StatsCard stats={stats} />}

      {/* Live Containers */}
      <LiveContainersPanel containers={containers} />

      {/* Configuration Card */}
      {config && (
        <ConfigurationCard config={config} onToggle={handleToggle} onUpdateConfig={handleUpdateConfig} />
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
            <Tabs defaultValue="list">
              <TabsList>
                <TabsTrigger value="list">List</TabsTrigger>
                <TabsTrigger value="graph">Graph</TabsTrigger>
              </TabsList>
              <TabsContent value="list">
                <div className="space-y-2">
                  {runs.map((run) => (
                    <RunCard key={run.id} run={run} onCancel={handleCancelRun} onDelete={handleDeleteRun} />
                  ))}
                </div>
              </TabsContent>
              <TabsContent value="graph">
                <RunGraph projectPath={activeProject.path} />
              </TabsContent>
            </Tabs>
          )}
        </CardContent>
      </Card>

      {/* New Run Dialog */}
      <NewRunDialog
        open={showRunDialog}
        onOpenChange={setShowRunDialog}
        prompt={runPrompt}
        onPromptChange={setRunPrompt}
        branch={runBranch}
        onBranchChange={setRunBranch}
        starting={starting}
        onStart={handleStartRun}
      />

      {/* Parallel Runs Dialog */}
      <ParallelRunsDialog
        open={showParallelDialog}
        onOpenChange={setShowParallelDialog}
        prompts={parallelPrompts}
        starting={startingParallel}
        onAdd={addParallelPrompt}
        onRemove={removeParallelPrompt}
        onUpdate={updateParallelPrompt}
        onStart={handleStartParallelRuns}
      />
    </div>
  )
}
