import { LayoutDashboard } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { useNavigate } from 'react-router-dom'
import { Progress } from '@/components/ui/progress'
import { useDashboard } from '@/contexts/DashboardContext'
import { useProjectContext } from '@/contexts/ProjectContext'
import { useProviderContext } from '@/contexts/ProviderContext'
import { getRelativeTime } from '@/features/usage/utils'
import { AgentActivityCard } from '@/features/dashboard/components/AgentActivityCard'
import { EnhancedProviderCards } from '@/features/dashboard/components/EnhancedProviderCards'

export function DashboardPage() {
  const { stats, loading, error, lastFetched, refreshDashboard } = useDashboard({ autoFetch: true })
  const { projects } = useProjectContext()
  const { selectedProviderId, selectedProvider } = useProviderContext()
  const navigate = useNavigate()

  // Guard against showing stats fetched for a since-abandoned provider selection.
  const providerStats = stats?.providerId === selectedProviderId ? stats : null
  const selectedProviderName = selectedProvider?.display_name ?? (selectedProviderId === 'codex-cli' ? 'Codex' : 'Claude Code')
  const isCodex = selectedProviderId === 'codex-cli'

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <LayoutDashboard className="h-8 w-8" />
            Dashboard
          </h1>
          <p className="text-muted-foreground">
            Overview of your local agent workspace
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastFetched && (
            <span className="text-xs text-muted-foreground">
              Updated {getRelativeTime(lastFetched.toISOString())}
            </span>
          )}
          <RefreshButton onClick={refreshDashboard} loading={loading} />
        </div>
      </div>

      {error && (
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{error}</p>
          </CardContent>
        </Card>
      )}

      {loading && !providerStats && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[...Array(9)].map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <CardDescription>Loading...</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">-</div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {providerStats && providerStats.warnings.length > 0 && (
        <Card className="border-amber-500/60">
          <CardHeader className="pb-2">
            <CardTitle className="text-amber-600 dark:text-amber-400">Partial {selectedProviderName} data</CardTitle>
            <CardDescription>
              Some provider-specific checks could not be loaded.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-1 text-xs text-muted-foreground">
              {providerStats.warnings.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {providerStats && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {/* Order matches sidebar navigation */}

          {/* Tier 1: Overview & Setup */}
          <EnhancedProviderCards />

          {/* Live Agent Activity */}
          <AgentActivityCard />

          {isCodex ? (
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Codex Config</CardDescription>
                <CardTitle className="text-3xl">{providerStats.settingsKeys}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">
                  Safe config entries, profiles, projects, and feature settings
                </p>
                <Button
                  variant="link"
                  className="p-0 h-auto mt-2"
                  onClick={() => navigate('/config')}
                >
                  View Codex config →
                </Button>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Projects</CardDescription>
                <CardTitle className="text-3xl">{projects.length}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">
                  Tracked projects
                </p>
              </CardContent>
            </Card>
          )}

          {/* Tier 2: Core Configuration */}
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>{selectedProviderName} MCP Servers</CardDescription>
              <CardTitle className="text-3xl">{providerStats.mcpServerCount}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">
                Configured MCP servers
              </p>
            </CardContent>
          </Card>

          {!isCodex && (
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Claude Commands</CardDescription>
                <CardTitle className="text-3xl">{providerStats.commandCount}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">
                  Claude slash commands available
                </p>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardDescription>{selectedProviderName} Plugins</CardDescription>
              <CardTitle className="text-3xl">{providerStats.pluginCount ?? 0}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">
                Installed {selectedProviderName} plugins
              </p>
            </CardContent>
          </Card>

          {isCodex && (
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Codex Feature Flags</CardDescription>
                <CardTitle className="text-3xl">{providerStats.featureFlagCount ?? 0}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">
                  {providerStats.enabledFeatureFlagCount ?? 0} enabled
                </p>
              </CardContent>
            </Card>
          )}

          {!isCodex && (
            <>
              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>Claude Hooks</CardDescription>
                  <CardTitle className="text-3xl">{providerStats.hookCount}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    Claude automation hooks configured
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>Claude Permissions</CardDescription>
                  <CardTitle className="text-3xl">{providerStats.permissionCount}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    Claude permission rules
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>Claude Agents</CardDescription>
                  <CardTitle className="text-3xl">{providerStats.agentCount}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    Custom agents (user, project, plugin)
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardDescription>Claude Skills</CardDescription>
                  <CardTitle className="text-3xl">{providerStats.skillCount}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    Available skills
                  </p>
                </CardContent>
              </Card>
            </>
          )}

          {/* Tier 3: Customization */}
          {!isCodex && (
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Claude Output Styles</CardDescription>
                <CardTitle className="text-3xl">{providerStats.outputStyleCount}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">
                  Custom output formats
                </p>
              </CardContent>
            </Card>
          )}

          {/* Tier 4: Monitoring */}
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>
                {providerStats.sessionMetricKind === 'live' ? `${selectedProviderName} Live Runs` : 'Claude Session History'}
              </CardDescription>
              <CardTitle className="text-3xl">{providerStats.sessionCount}</CardTitle>
            </CardHeader>
            <CardContent>
              {providerStats.sessionMetricKind === 'live' ? (
                <p className="text-xs text-muted-foreground">
                  Runs currently visible through Agent Bridge
                </p>
              ) : (
                <div className="text-xs text-muted-foreground space-y-1">
                  <p>{providerStats.sessionsToday} today</p>
                  <p>{providerStats.sessionsThisWeek} this week</p>
                  {providerStats.mostActiveProject && (
                    <p className="text-primary">Most active: {providerStats.mostActiveProject}</p>
                  )}
                </div>
              )}
              <Button
                variant="link"
                className="p-0 h-auto mt-2"
                onClick={() => navigate(providerStats.sessionMetricKind === 'live' ? '/agent-bridge' : '/sessions')}
              >
                {providerStats.sessionMetricKind === 'live' ? 'View live runs →' : 'View all sessions →'}
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardDescription>{isCodex ? 'Plan Snapshots' : 'Plans'}</CardDescription>
              <CardTitle className="text-3xl">{providerStats.planCount}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">
                {isCodex ? 'Codex update_plan snapshots' : 'Plan deliverables & spec docs'}
              </p>
              <Button
                variant="link"
                className="p-0 h-auto mt-2"
                onClick={() => navigate('/plans')}
              >
                View all plans →
              </Button>
            </CardContent>
          </Card>

          {!isCodex && (
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Claude Context Window</CardDescription>
                <CardTitle className="text-3xl">
                  {providerStats.contextActiveCount && providerStats.contextActiveCount > 0
                    ? `${(providerStats.contextHighestPct ?? 0).toFixed(0)}%`
                    : '--'}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {providerStats.contextActiveCount && providerStats.contextActiveCount > 0 ? (
                  <div className="space-y-2">
                    <Progress
                      value={providerStats.contextHighestPct ?? 0}
                      className={`h-2 ${
                        (providerStats.contextHighestPct ?? 0) >= 95 ? '[&>div]:bg-red-500' :
                        (providerStats.contextHighestPct ?? 0) >= 80 ? '[&>div]:bg-orange-500' :
                        (providerStats.contextHighestPct ?? 0) >= 50 ? '[&>div]:bg-yellow-500' :
                        '[&>div]:bg-green-500'
                      }`}
                    />
                    <p className="text-xs text-muted-foreground">
                      {providerStats.contextActiveCount} active session{providerStats.contextActiveCount !== 1 ? 's' : ''}
                      {providerStats.contextHighestProject && ` - ${providerStats.contextHighestProject}`}
                    </p>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">No active sessions</p>
                )}
                <Button
                  variant="link"
                  className="p-0 h-auto mt-2"
                  onClick={() => navigate('/context')}
                >
                  View context →
                </Button>
              </CardContent>
            </Card>
          )}

          {isCodex && providerStats.unsupportedFeatures.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Not shown for Codex</CardDescription>
                <CardTitle className="text-base">Claude Code-only surfaces</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-1.5">
                  {providerStats.unsupportedFeatures.map((feature) => (
                    <Badge key={feature} variant="secondary">
                      {feature}
                    </Badge>
                  ))}
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  These cards are hidden because Codex does not expose equivalent data yet.
                </p>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {providerStats && (
        <Card>
          <CardHeader>
            <CardTitle>Quick Status</CardTitle>
            <CardDescription>{selectedProviderName} configuration health indicators</CardDescription>
          </CardHeader>
          <CardContent>
            {isCodex ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span>Config entries:</span>
                  <span className="font-medium">
                    {providerStats.settingsKeys}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span>MCP servers:</span>
                  <span className="font-medium">
                    {providerStats.mcpServerCount}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span>Plugins:</span>
                  <span className="font-medium">
                    {providerStats.pluginCount ?? 0}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span>Enabled features:</span>
                  <span className="font-medium">
                    {providerStats.enabledFeatureFlagCount ?? 0}
                  </span>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span>Settings keys:</span>
                  <span className="font-medium">
                    {providerStats.settingsKeys} configured
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span>Allow rules:</span>
                  <span className="font-medium text-success">
                    {providerStats.allowRules}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span>Deny rules:</span>
                  <span className="font-medium text-destructive">
                    {providerStats.denyRules}
                  </span>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
