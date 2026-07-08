import { useState, useEffect, useMemo, useRef, type KeyboardEvent } from 'react'
import { toast } from 'sonner'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { MODAL_SIZES } from '@/lib/constants'
import { cn } from '@/lib/utils'
import { formatTimestamp } from '@/features/usage/utils'
import { fetchHosts } from '@/features/hosts/api'
import { spawnSession, fetchResumableSessions, bulkResumeSessions, fetchMinimaxPlatformStatus } from './api'
import { Link } from 'react-router-dom'
import { useProjectContext } from '@/contexts/ProjectContext'
import { useProviderContext } from '@/contexts/ProviderContext'
import type { AgentProviderId } from '@/types/providers'
import type { SpawnSessionRequest } from './types'
import type { ResumableSession } from '@/types/sessions'
import type { Host } from '@/features/hosts/types'
import type { ProjectResponse } from '@/types/projects'

type Mode = 'plain' | 'worktree' | 'resume' | 'fork'

interface NewSessionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSpawned: (tmuxTarget: string) => void
  initialProvider?: AgentProviderId
}

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: 'plain', label: 'Plain' },
  { value: 'worktree', label: 'Worktree' },
  { value: 'resume', label: 'Resume' },
]

const CODEX_MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: 'plain', label: 'New' },
  { value: 'resume', label: 'Resume' },
  { value: 'fork', label: 'Fork' },
]

const PLATFORM_STORAGE_KEY = 'cc-bridge.platform'

type Platform = 'anthropic' | 'bedrock' | 'minimax'

const MINIMAX_BASE_URL_INTERNATIONAL = 'https://api.minimax.io/anthropic'
const MINIMAX_BASE_URL_CHINA = 'https://api.minimaxi.com/anthropic'

interface RememberedPlatform {
  platform: Platform
  aws_region: string
  aws_profile: string
  bedrock_model: string
  minimax_base_url: string
}

function loadRememberedPlatform(): RememberedPlatform {
  const fallback: RememberedPlatform = {
    platform: 'anthropic',
    aws_region: '',
    aws_profile: '',
    bedrock_model: '',
    minimax_base_url: MINIMAX_BASE_URL_INTERNATIONAL,
  }
  try {
    const raw = localStorage.getItem(PLATFORM_STORAGE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Partial<RememberedPlatform>
    const platform: Platform =
      parsed.platform === 'bedrock' ? 'bedrock' : parsed.platform === 'minimax' ? 'minimax' : 'anthropic'
    return {
      platform,
      aws_region: typeof parsed.aws_region === 'string' ? parsed.aws_region : '',
      aws_profile: typeof parsed.aws_profile === 'string' ? parsed.aws_profile : '',
      bedrock_model: typeof parsed.bedrock_model === 'string' ? parsed.bedrock_model : '',
      minimax_base_url:
        typeof parsed.minimax_base_url === 'string' ? parsed.minimax_base_url : MINIMAX_BASE_URL_INTERNATIONAL,
    }
  } catch {
    return fallback
  }
}

function matchesProjectSearch(project: ProjectResponse, query: string) {
  const terms = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)

  if (terms.length === 0) return true

  const searchable = `${project.name} ${project.path}`.toLowerCase()
  return terms.every((term) => searchable.includes(term))
}

export function NewSessionDialog({ open, onOpenChange, onSpawned, initialProvider }: NewSessionDialogProps) {
  const { providers, selectedProviderId } = useProviderContext()
  const defaultProvider = initialProvider ?? selectedProviderId
  const [provider, setProvider] = useState<AgentProviderId>(defaultProvider)
  const [directory, setDirectory] = useState('')
  const [projectSearch, setProjectSearch] = useState('')
  const [mode, setMode] = useState<Mode>('plain')
  const [worktreeName, setWorktreeName] = useState('')
  const [sessionName, setSessionName] = useState('')
  const [skipPermissions, setSkipPermissions] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('')
  const [profile, setProfile] = useState('')
  const [sandbox, setSandbox] = useState('')
  const [approvalPolicy, setApprovalPolicy] = useState('')
  const [search, setSearch] = useState(false)
  const [noAltScreen, setNoAltScreen] = useState(true)
  const [dangerousBypass, setDangerousBypass] = useState(false)
  const [codexSessionId, setCodexSessionId] = useState('')
  const [useLast, setUseLast] = useState(true)
  const [platform, setPlatform] = useState<Platform>('anthropic')
  const [awsRegion, setAwsRegion] = useState('')
  const [awsProfile, setAwsProfile] = useState('')
  const [bedrockModel, setBedrockModel] = useState('')
  const [minimaxBaseUrl, setMinimaxBaseUrl] = useState(MINIMAX_BASE_URL_INTERNATIONAL)
  const [minimaxConfigured, setMinimaxConfigured] = useState<boolean | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [recentSessions, setRecentSessions] = useState<ResumableSession[]>([])
  const [selectedSessionIds, setSelectedSessionIds] = useState<Set<string>>(new Set())
  const [loadingSessions, setLoadingSessions] = useState(false)

  const [hosts, setHosts] = useState<Host[]>([])
  const [loadingHosts, setLoadingHosts] = useState(false)
  const [selectedHostId, setSelectedHostId] = useState<number | null>(null)
  const projectSearchRef = useRef<HTMLInputElement>(null)
  const projectOptionRefs = useRef<Array<HTMLButtonElement | null>>([])

  const { projects, activeProject } = useProjectContext()
  const isCodex = provider === 'codex-cli'
  const modeOptions = isCodex ? CODEX_MODE_OPTIONS : MODE_OPTIONS
  const filteredProjects = useMemo(
    () => projects.filter((project) => matchesProjectSearch(project, projectSearch)),
    [projects, projectSearch],
  )

  function selectProject(project: ProjectResponse) {
    setDirectory(project.path)
    setSelectedSessionIds(new Set())
    setError(null)
  }

  function focusProjectOption(index: number) {
    projectOptionRefs.current[index]?.focus()
  }

  function handleProjectSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' && filteredProjects.length > 0) {
      event.preventDefault()
      focusProjectOption(0)
      return
    }

    if (event.key === 'Enter' && filteredProjects.length === 1) {
      event.preventDefault()
      selectProject(filteredProjects[0])
    }
  }

  function handleProjectOptionKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      focusProjectOption(Math.min(index + 1, filteredProjects.length - 1))
      return
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (index === 0) {
        projectSearchRef.current?.focus()
      } else {
        focusProjectOption(index - 1)
      }
    }
  }

  useEffect(() => {
    if (open && !directory.trim() && activeProject?.path) {
      setDirectory(activeProject.path)
    }
  }, [open, activeProject?.path, directory])

  // Prefill the remembered platform selection when the dialog opens.
  useEffect(() => {
    if (!open) return
    const remembered = loadRememberedPlatform()
    setPlatform(remembered.platform)
    setAwsRegion(remembered.aws_region)
    setAwsProfile(remembered.aws_profile)
    setBedrockModel(remembered.bedrock_model)
    setMinimaxBaseUrl(remembered.minimax_base_url)
  }, [open])

  // Check whether the backend has a MiniMax API key configured. Cockpit never
  // handles the key itself, so this only toggles a "not configured" notice.
  useEffect(() => {
    if (!open || platform !== 'minimax') return
    let cancelled = false
    setMinimaxConfigured(null)
    fetchMinimaxPlatformStatus()
      .then((data) => { if (!cancelled) setMinimaxConfigured(data.configured) })
      .catch(() => { if (!cancelled) setMinimaxConfigured(null) })
    return () => { cancelled = true }
  }, [open, platform])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoadingHosts(true)
    setHosts([])
    fetchHosts()
      .then((data) => { if (!cancelled) setHosts(data) })
      .catch(() => { if (!cancelled) setHosts([]) })
      .finally(() => { if (!cancelled) setLoadingHosts(false) })
    return () => { cancelled = true }
  }, [open])

  // Fetch sessions for the selected project AND its git worktrees in resume mode.
  useEffect(() => {
    if (mode !== 'resume' || isCodex) return
    let cancelled = false
    setSelectedSessionIds(new Set())
    setRecentSessions([])
    const dir = directory.trim()
    if (!dir) {
      setLoadingSessions(false)
      return () => { cancelled = true }
    }
    setLoadingSessions(true)
    fetchResumableSessions(dir, 20)
      .then((data) => { if (!cancelled) setRecentSessions(data.sessions) })
      .catch(() => { if (!cancelled) setRecentSessions([]) })
      .finally(() => { if (!cancelled) setLoadingSessions(false) })
    return () => { cancelled = true }
  }, [mode, isCodex, directory])

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setDirectory('')
      setProvider(defaultProvider)
      setProjectSearch('')
      setMode('plain')
      setWorktreeName('')
      setSessionName('')
      setSkipPermissions(false)
      setPrompt('')
      setModel('')
      setProfile('')
      setSandbox('')
      setApprovalPolicy('')
      setSearch(false)
      setNoAltScreen(true)
      setDangerousBypass(false)
      setCodexSessionId('')
      setUseLast(true)
      setError(null)
      setSelectedSessionIds(new Set())
      setRecentSessions([])
      setSubmitting(false)
      setSelectedHostId(null)
      setHosts([])
      setMinimaxConfigured(null)
    }
  }, [open, defaultProvider])

  const canLaunch = (() => {
    if (submitting) return false
    if (isCodex && (mode === 'resume' || mode === 'fork')) {
      return directory.trim().length > 0 && (useLast || codexSessionId.trim().length > 0)
    }
    if (!isCodex && mode === 'resume') return directory.trim().length > 0 && selectedSessionIds.size > 0
    return directory.trim().length > 0
  })()

  async function handleLaunch() {
    setError(null)
    setSubmitting(true)

    try {
      const isBedrock = !isCodex && platform === 'bedrock'
      const isMinimax = !isCodex && platform === 'minimax'
      try {
        localStorage.setItem(
          PLATFORM_STORAGE_KEY,
          JSON.stringify({
            platform: isCodex ? 'anthropic' : platform,
            aws_region: awsRegion,
            aws_profile: awsProfile,
            bedrock_model: bedrockModel,
            minimax_base_url: minimaxBaseUrl,
          }),
        )
      } catch {
        // Persisting the platform preference is best-effort; ignore storage failures.
      }

      // Resume mode (Claude Code) resumes every selected session in one batch,
      // each in its own tmux pane.
      if (provider === 'claude-code' && mode === 'resume') {
        const selected = recentSessions.filter((s) => selectedSessionIds.has(s.id))
        const result = await bulkResumeSessions({
          provider,
          directory: directory.trim(),
          sessions: selected.map((s) => ({ session_id: s.id, project_folder: s.project_folder })),
          ...(skipPermissions && { skip_permissions: true }),
          ...(isBedrock && { platform: 'bedrock' as const }),
          ...(isBedrock && awsRegion.trim() && { aws_region: awsRegion.trim() }),
          ...(isBedrock && awsProfile.trim() && { aws_profile: awsProfile.trim() }),
          ...(isBedrock && bedrockModel.trim() && { bedrock_model: bedrockModel.trim() }),
          ...(isMinimax && { platform: 'minimax' as const }),
          ...(isMinimax && minimaxBaseUrl.trim() && { minimax_base_url: minimaxBaseUrl.trim() }),
        })
        for (const item of result.results) {
          if (item.ok && item.tmux_target) onSpawned(item.tmux_target)
        }
        if (result.failed > 0) {
          toast.error(`Resumed ${result.spawned} session(s), ${result.failed} failed.`)
        } else if (result.spawned > 1) {
          toast.success(`Resumed ${result.spawned} sessions.`)
        }
        onOpenChange(false)
        return
      }

      const request: SpawnSessionRequest = {
        provider,
        directory: directory.trim(),
        mode,
        ...(sessionName.trim() && { session_name: sessionName.trim() }),
        ...(provider === 'claude-code' && mode === 'worktree' && worktreeName.trim() && { worktree_name: worktreeName.trim() }),
        ...(provider === 'claude-code' && skipPermissions && { skip_permissions: true }),
        ...(isCodex && prompt.trim() && { prompt: prompt.trim() }),
        ...(isCodex && model.trim() && { model: model.trim() }),
        ...(isCodex && profile.trim() && { profile: profile.trim() }),
        ...(isCodex && sandbox && { sandbox }),
        ...(isCodex && approvalPolicy && { approval_policy: approvalPolicy }),
        ...(isCodex && search && { search: true }),
        ...(isCodex && { no_alt_screen: noAltScreen }),
        ...(isCodex && dangerousBypass && { dangerously_bypass_approvals_and_sandbox: true }),
        ...(isCodex && (mode === 'resume' || mode === 'fork') && {
          use_last: useLast,
          ...(!useLast && codexSessionId.trim() && { session_id: codexSessionId.trim() }),
        }),
        ...(isBedrock && { platform: 'bedrock' as const }),
        ...(isBedrock && awsRegion.trim() && { aws_region: awsRegion.trim() }),
        ...(isBedrock && awsProfile.trim() && { aws_profile: awsProfile.trim() }),
        ...(isBedrock && bedrockModel.trim() && { bedrock_model: bedrockModel.trim() }),
        ...(isMinimax && { platform: 'minimax' as const }),
        ...(isMinimax && minimaxBaseUrl.trim() && { minimax_base_url: minimaxBaseUrl.trim() }),
        ...(selectedHostId !== null && { host_id: selectedHostId }),
      }

      const response = await spawnSession(request)
      if (response.worktree_name_adjusted && response.worktree_name) {
        toast.info(`Worktree created as "${response.worktree_name}" (adjusted to a valid git branch name).`)
      }
      onSpawned(response.tmux_target)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to spawn session')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn(MODAL_SIZES.MD, 'overflow-y-auto')}>
        <DialogHeader>
          <DialogTitle>New Agent Session</DialogTitle>
          <DialogDescription>
            Launch a new agent CLI instance in a tmux session.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 min-w-0">
          <div className="space-y-1.5">
            <Label>Provider</Label>
            <Select
              value={provider}
              onValueChange={(value) => {
                setProvider(value as AgentProviderId)
                setMode('plain')
                setSelectedSessionIds(new Set())
                setError(null)
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providers.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.display_name}{!item.installed ? ' (missing)' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Host selection (optional) */}
          <div className="space-y-1.5">
            <Label>Host</Label>
            <Select
              value={selectedHostId !== null ? String(selectedHostId) : ''}
              onValueChange={(value) => {
                setSelectedHostId(value ? Number(value) : null)
                setError(null)
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder={loadingHosts ? 'Loading hosts...' : 'Local (default)'} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">Local (default)</SelectItem>
                {hosts.map((host) => (
                  <SelectItem key={host.id} value={String(host.id)}>
                    {host.alias}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Select a remote host to run the session on, or leave as Local.
            </p>
          </div>

          {/* Mode selector */}
          <div className="space-y-1.5">
            <Label>Mode</Label>
            <div className="flex gap-1 rounded-md bg-muted p-1">
              {modeOptions.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={cn(
                    'flex-1 px-3 py-1.5 rounded text-sm font-medium transition-colors',
                    mode === opt.value
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                  onClick={() => {
                    setMode(opt.value)
                    setSelectedSessionIds(new Set())
                    setError(null)
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Optional explicit session name */}
          <div className="space-y-1.5">
            <Label htmlFor="session-name">Session name</Label>
            <Input
              id="session-name"
              value={sessionName}
              onChange={(e) => setSessionName(e.target.value)}
              placeholder="auto"
              autoComplete="off"
            />
            <p className="text-xs text-muted-foreground">
              Optional. Defaults to the worktree name, or an auto-generated name.
            </p>
          </div>

          {/* Directory input */}
          <div className="space-y-2">
            {projects.length > 0 && (
              <div className="space-y-1.5">
                <Label htmlFor="session-project-search">Project</Label>
                <Input
                  ref={projectSearchRef}
                  id="session-project-search"
                  type="search"
                  value={projectSearch}
                  onChange={(event) => setProjectSearch(event.target.value)}
                  onKeyDown={handleProjectSearchKeyDown}
                  placeholder="Search projects by name or path"
                  autoComplete="off"
                  aria-controls="session-project-results"
                />
                <div
                  id="session-project-results"
                  aria-label="Projects"
                  className="max-h-48 overflow-y-auto rounded-md border border-border bg-background"
                >
                  {filteredProjects.length === 0 ? (
                    <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                      No projects match.
                    </div>
                  ) : (
                    filteredProjects.map((project, index) => {
                      const selected = project.path === directory.trim()
                      return (
                        <button
                          key={project.id}
                          ref={(element) => {
                            projectOptionRefs.current[index] = element
                          }}
                          type="button"
                          aria-pressed={selected}
                          className={cn(
                            'block w-full min-w-0 border-b px-3 py-2 text-left transition-colors last:border-b-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                            selected ? 'bg-primary/10 text-foreground' : 'hover:bg-muted/50',
                          )}
                          onClick={() => selectProject(project)}
                          onKeyDown={(event) => handleProjectOptionKeyDown(event, index)}
                        >
                          <span className="block truncate text-sm font-medium">
                            {project.name}
                          </span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {project.path}
                          </span>
                        </button>
                      )
                    })
                  )}
                </div>
              </div>
            )}
            <Label htmlFor="session-directory">Project Directory</Label>
            <Input
              id="session-directory"
              value={directory}
              onChange={(e) => {
                setDirectory(e.target.value)
                setSelectedSessionIds(new Set())
                setError(null)
              }}
              placeholder="/home/user/project"
              autoComplete="off"
            />
          </div>

          {/* Worktree name (only in worktree mode) */}
          {!isCodex && mode === 'worktree' && (
            <div className="space-y-1.5">
              <Label htmlFor="worktree-name">Worktree Name</Label>
              <Input
                id="worktree-name"
                value={worktreeName}
                onChange={(e) => setWorktreeName(e.target.value)}
                placeholder="feature-name"
              />
              <p className="text-xs text-muted-foreground">
                Optional. A git worktree will be created for isolated development.
              </p>
            </div>
          )}

          {/* Resume session picker (multi-select — resumes each in its own pane) */}
          {!isCodex && mode === 'resume' && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>Recent Sessions</Label>
                {selectedSessionIds.size > 0 && (
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => setSelectedSessionIds(new Set())}
                  >
                    Clear ({selectedSessionIds.size})
                  </button>
                )}
              </div>
              {loadingSessions ? (
                <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
                  Loading sessions...
                </div>
              ) : recentSessions.length === 0 ? (
                <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
                  No recent sessions found.
                </div>
              ) : (
                <div className="max-h-48 w-full overflow-y-auto rounded-md border">
                  {recentSessions.map((session) => {
                    const checked = selectedSessionIds.has(session.id)
                    return (
                      <button
                        key={session.id}
                        type="button"
                        aria-pressed={checked}
                        className={cn(
                          'flex w-full min-w-0 items-start gap-2 text-left px-3 py-2 border-b last:border-b-0 transition-colors',
                          checked
                            ? 'border-l-2 border-l-primary bg-primary/5'
                            : 'hover:bg-muted/50'
                        )}
                        onClick={() =>
                          setSelectedSessionIds((prev) => {
                            const next = new Set(prev)
                            if (next.has(session.id)) next.delete(session.id)
                            else next.add(session.id)
                            return next
                          })
                        }
                      >
                        <Checkbox checked={checked} className="mt-0.5 shrink-0 pointer-events-none" />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center justify-between gap-2 min-w-0">
                            <span className="flex items-center gap-1.5 min-w-0">
                              <span className="text-sm font-medium truncate min-w-0">
                                {session.project_name}
                              </span>
                              <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                                {session.worktree_label}
                              </span>
                            </span>
                            <span className="text-xs text-muted-foreground shrink-0">
                              {formatTimestamp(session.modified_at)}
                            </span>
                          </span>
                          {session.summary && (
                            <span className="block text-xs text-muted-foreground mt-0.5 truncate">
                              {session.summary}
                            </span>
                          )}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {isCodex && (mode === 'resume' || mode === 'fork') && (
            <div className="space-y-2">
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="codex-use-last"
                  checked={useLast}
                  onCheckedChange={(checked) => setUseLast(checked === true)}
                />
                <Label htmlFor="codex-use-last" className="cursor-pointer">
                  Use last Codex session
                </Label>
              </div>
              {!useLast && (
                <div className="space-y-1.5">
                  <Label htmlFor="codex-session-id">Codex Session ID</Label>
                  <Input
                    id="codex-session-id"
                    value={codexSessionId}
                    onChange={(e) => setCodexSessionId(e.target.value)}
                    placeholder="session id"
                  />
                </div>
              )}
            </div>
          )}

          {isCodex && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="codex-model">Model</Label>
                <Input id="codex-model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="default" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="codex-profile">Profile</Label>
                <Input id="codex-profile" value={profile} onChange={(e) => setProfile(e.target.value)} placeholder="default" />
              </div>
              <div className="space-y-1.5">
                <Label>Sandbox</Label>
                <Select value={sandbox || 'default'} onValueChange={(value) => setSandbox(value === 'default' ? '' : value)}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="default">Default</SelectItem>
                    <SelectItem value="read-only">Read-only</SelectItem>
                    <SelectItem value="workspace-write">Workspace write</SelectItem>
                    <SelectItem value="danger-full-access">Full access</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Approval</Label>
                <Select value={approvalPolicy || 'default'} onValueChange={(value) => setApprovalPolicy(value === 'default' ? '' : value)}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="default">Default</SelectItem>
                    <SelectItem value="untrusted">Untrusted</SelectItem>
                    <SelectItem value="on-failure">On failure</SelectItem>
                    <SelectItem value="on-request">On request</SelectItem>
                    <SelectItem value="never">Never</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="col-span-2 space-y-1.5">
                <Label htmlFor="codex-prompt">Initial Prompt</Label>
                <Input id="codex-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Optional prompt" />
              </div>
            </div>
          )}

          {!isCodex && (
            <div className="space-y-1.5">
              <Label>Platform</Label>
              <Select value={platform} onValueChange={(value) => setPlatform(value as Platform)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="anthropic">Anthropic (default)</SelectItem>
                  <SelectItem value="bedrock">Amazon Bedrock</SelectItem>
                  <SelectItem value="minimax">MiniMax</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}

          {!isCodex && platform === 'bedrock' && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <p className="text-xs text-muted-foreground">
                Uses AWS credentials from the server environment. Region is usually required.
              </p>
              <div className="space-y-1.5">
                <Label htmlFor="aws-region">AWS Region</Label>
                <Input id="aws-region" value={awsRegion} onChange={(e) => setAwsRegion(e.target.value)} placeholder="e.g. us-east-1" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="aws-profile">AWS Profile (optional)</Label>
                <Input id="aws-profile" value={awsProfile} onChange={(e) => setAwsProfile(e.target.value)} placeholder="e.g. bedrock-prod" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="bedrock-model">Model ARN / ID (optional)</Label>
                <Input id="bedrock-model" value={bedrockModel} onChange={(e) => setBedrockModel(e.target.value)} placeholder="arn:aws:bedrock:..." />
              </div>
            </div>
          )}

          {!isCodex && platform === 'minimax' && (
            <div className="space-y-3 rounded-md border border-border p-3">
              {minimaxConfigured === null && (
                <p className="text-xs text-muted-foreground">Checking configuration...</p>
              )}

              {minimaxConfigured === false && (
                <p className="text-xs text-muted-foreground">
                  MiniMax API key not configured.{' '}
                  <Link to="/subscriptions" className="underline hover:text-foreground">
                    Set it up on the Subscriptions page
                  </Link>
                  .
                </p>
              )}

              <div className="space-y-1.5">
                <Label>Endpoint</Label>
                <Select value={minimaxBaseUrl} onValueChange={setMinimaxBaseUrl}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={MINIMAX_BASE_URL_INTERNATIONAL}>International</SelectItem>
                    <SelectItem value={MINIMAX_BASE_URL_CHINA}>China</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          {!isCodex && (
            <div className="space-y-1">
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="skip-permissions"
                  checked={skipPermissions}
                  onCheckedChange={(checked) => setSkipPermissions(checked === true)}
                />
                <Label htmlFor="skip-permissions" className="cursor-pointer">
                  Skip permission prompts
                </Label>
              </div>
              <p className="text-xs text-destructive/80 ml-6">
                Allows Claude to run tools without asking for confirmation
              </p>
            </div>
          )}

          {isCodex && (
            <div className="space-y-2">
              <div className="flex items-center space-x-2">
                <Checkbox id="codex-search" checked={search} onCheckedChange={(checked) => setSearch(checked === true)} />
                <Label htmlFor="codex-search" className="cursor-pointer">Enable web search</Label>
              </div>
              <div className="flex items-center space-x-2">
                <Checkbox id="codex-no-alt-screen" checked={noAltScreen} onCheckedChange={(checked) => setNoAltScreen(checked === true)} />
                <Label htmlFor="codex-no-alt-screen" className="cursor-pointer">Disable alternate screen</Label>
              </div>
              <div className="flex items-center space-x-2">
                <Checkbox id="codex-dangerous" checked={dangerousBypass} onCheckedChange={(checked) => setDangerousBypass(checked === true)} />
                <Label htmlFor="codex-dangerous" className="cursor-pointer text-destructive">Bypass approvals and sandbox</Label>
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div className="rounded-md bg-destructive/10 border border-destructive/20 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleLaunch} disabled={!canLaunch}>
            {submitting ? 'Launching...' : 'Launch'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
