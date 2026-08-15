import { useCallback, useEffect, useMemo, useState } from 'react'
import { BookOpen, Mail, Plug, RefreshCw, Users } from 'lucide-react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { Card, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import type {
  AgentMailInstallStatus,
  AgentMailSnippets,
  MailMemberResponse,
  MailMemberUpdate,
} from '@/types/agentMail'
import {
  applyClaudeCodeAgentMailInstall,
  applyCodexAgentMailInstall,
  fetchAgentMailInstallStatus,
  fetchAgentMailSnippets,
  fetchAgentMailTeam,
  uninstallClaudeCodeAgentMail,
  uninstallCodexAgentMail,
  updateAgentMailMember,
} from './api'
import { AgentMailHelpDialog } from './AgentMailHelpDialog'
import { InstallTab } from './InstallTab'
import { MemberEditDialog } from './MemberEditDialog'
import { TeamTab, type TeamStatusFilter } from './TeamTab'

const OPERATIONAL_POLL_INTERVAL_MS = 5000

export function AgentMailPage() {
  const [members, setMembers] = useState<MailMemberResponse[]>([])
  const [installStatus, setInstallStatus] = useState<AgentMailInstallStatus | null>(null)
  const [snippets, setSnippets] = useState<AgentMailSnippets | null>(null)
  const [loading, setLoading] = useState(true)
  const [installLoading, setInstallLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('team')
  const [teamSearch, setTeamSearch] = useState('')
  const [teamStatus, setTeamStatus] = useState<TeamStatusFilter>('all')
  const [editingMember, setEditingMember] = useState<MailMemberResponse | null>(null)
  const [helpOpen, setHelpOpen] = useState(false)

  const loadOperationalData = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true)
    try {
      const team = await fetchAgentMailTeam(true)
      setMembers(team.members)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load Agent Mail')
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [])

  const loadInstallData = useCallback(async () => {
    setInstallLoading(true)
    try {
      const [status, snippetData] = await Promise.all([fetchAgentMailInstallStatus(), fetchAgentMailSnippets()])
      setInstallStatus(status)
      setSnippets(snippetData)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load install status')
    } finally {
      setInstallLoading(false)
    }
  }, [])

  const refreshAll = useCallback(async () => {
    await Promise.all([loadOperationalData(), loadInstallData()])
  }, [loadInstallData, loadOperationalData])

  useEffect(() => {
    let cancelled = false
    queueMicrotask(() => { if (!cancelled) void refreshAll() })
    return () => { cancelled = true }
  }, [refreshAll])

  useEffect(() => {
    const timer = window.setInterval(() => { void loadOperationalData(false) }, OPERATIONAL_POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [loadOperationalData])

  const stats = useMemo(() => {
    const connected = members.filter((m) => m.status === 'connected').length
    const observed = members.filter((m) => m.status === 'observed').length
    return { connected, observed }
  }, [members])

  const claudeReady = Boolean(installStatus && installStatus.claude_code_hooks_missing.length === 0)
  const codexReady = Boolean(installStatus && installStatus.codex_hooks_missing.length === 0 && installStatus.codex_cli_available)
  const hasConfiguredIntegration = claudeReady || codexReady
  const showSetupNotice = !installLoading && (!hasConfiguredIntegration || members.length === 0)

  const handleUpdateMember = async (memberId: number, update: MailMemberUpdate) => {
    try {
      await updateAgentMailMember(memberId, update)
      await loadOperationalData()
      toast.success('Participant updated')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to update participant')
      throw error
    }
  }

  const runInstallAction = async (action: () => Promise<AgentMailInstallStatus>, label: string) => {
    try {
      const status = await action()
      setInstallStatus(status)
      await loadInstallData()
      await loadOperationalData(false)
      toast.success(label)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Install action failed')
      throw error
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-3xl font-bold">
            <Mail className="h-8 w-8" />
            Agent Mail
          </h1>
          <p className="mt-1 text-muted-foreground">
            Roster and session discovery for local Claude Code / Codex CLI runs.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <RefreshButton onClick={refreshAll} loading={loading || installLoading} />
          <Button variant="outline" onClick={() => setHelpOpen(true)}>
            <BookOpen className="mr-2 h-4 w-4" />
            How it works
          </Button>
        </div>
      </div>

      {showSetupNotice && (
        <Alert className="border-amber-300 bg-amber-50/60 dark:bg-amber-950/20">
          <Plug className="h-4 w-4" />
          <AlertTitle>{!hasConfiguredIntegration ? 'Agent setup required' : 'No agents registered yet'}</AlertTitle>
          <AlertDescription>
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <p>
                {!hasConfiguredIntegration
                  ? 'Install the Agent Mail hooks for Claude Code or Codex, and create an MCP token on the MCP Server page, before agents can register with the roster.'
                  : 'Start or resume a run in a repository, then have it call agent_mail_whoami once so Agent Cockpit can attach it to a participant.'}
              </p>
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => setHelpOpen(true)}>
                  <BookOpen className="mr-2 h-4 w-4" />
                  Setup notes
                </Button>
                {!hasConfiguredIntegration && (
                  <Button size="sm" onClick={() => setActiveTab('install')}>
                    <Plug className="mr-2 h-4 w-4" />
                    Open Install
                  </Button>
                )}
              </div>
            </div>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="rounded-lg">
          <CardHeader className="pb-3">
            <CardDescription>Participants</CardDescription>
            <CardTitle className="flex items-center gap-2 text-3xl">
              <Users className="h-5 w-5 text-muted-foreground" />
              {members.length}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card className="rounded-lg">
          <CardHeader className="pb-3">
            <CardDescription>Connected</CardDescription>
            <CardTitle className="text-3xl">{stats.connected}</CardTitle>
          </CardHeader>
        </Card>
        <Card className="rounded-lg">
          <CardHeader className="pb-3">
            <CardDescription>Observed only</CardDescription>
            <CardTitle className="text-3xl">{stats.observed}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid h-auto w-full grid-cols-2 md:w-[360px]">
          <TabsTrigger value="team" className="gap-2"><Users className="h-4 w-4" />Team</TabsTrigger>
          <TabsTrigger value="install" className="gap-2"><RefreshCw className="h-4 w-4" />Install</TabsTrigger>
        </TabsList>
        <TabsContent value="team" className="mt-4">
          <TeamTab
            members={members} loading={loading} repoSearch={teamSearch} statusFilter={teamStatus}
            onRepoSearchChange={setTeamSearch} onStatusFilterChange={setTeamStatus}
            onEdit={setEditingMember}
          />
        </TabsContent>
        <TabsContent value="install" className="mt-4">
          <InstallTab
            status={installStatus} snippets={snippets} loading={installLoading} onRefresh={loadInstallData}
            onApplyClaudeCode={() => runInstallAction(applyClaudeCodeAgentMailInstall, 'Claude Code hooks installed')}
            onUninstallClaudeCode={() => runInstallAction(uninstallClaudeCodeAgentMail, 'Claude Code hooks removed')}
            onApplyCodex={() => runInstallAction(applyCodexAgentMailInstall, 'Codex hooks installed')}
            onUninstallCodex={() => runInstallAction(uninstallCodexAgentMail, 'Codex hooks removed')}
          />
        </TabsContent>
      </Tabs>

      <MemberEditDialog
        member={editingMember} open={Boolean(editingMember)}
        onOpenChange={(open) => !open && setEditingMember(null)} onSave={handleUpdateMember}
      />
      <AgentMailHelpDialog open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  )
}
