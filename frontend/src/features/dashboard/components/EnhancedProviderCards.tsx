import { useNavigate } from 'react-router-dom'
import { useProviderContext } from '@/contexts/ProviderContext'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Terminal,
  CheckCircle2,
  AlertCircle,
  Settings,
  Server,
  Package,
  Shield,
  Bot,
  Sparkles,
  Brain,
  Activity,
  BarChart3,
  Wrench,
  Eye,
  Zap,
} from 'lucide-react'
import { CLICKABLE_CARD } from '@/lib/constants'
import type { AgentProviderStatus, AgentProviderCapabilityDetail, AgentProviderCapabilities } from '@/types/providers'

const CAPABILITY_GROUPS = [
  {
    name: 'Configuration',
    icon: Settings,
    capabilities: ['config', 'permissions', 'commands', 'hooks'],
  },
  {
    name: 'Sessions',
    icon: Terminal,
    capabilities: ['sessions', 'spawn', 'resume', 'fork'],
  },
  {
    name: 'Extensions',
    icon: Package,
    capabilities: ['mcp', 'plugins', 'agents', 'skills'],
  },
  {
    name: 'Customization',
    icon: Wrench,
    capabilities: ['memory', 'output_styles', 'statusline'],
  },
  {
    name: 'Monitoring',
    icon: Activity,
    capabilities: ['usage', 'context', 'backup', 'restore'],
  },
]

const CAPABILITY_ICONS: Record<string, typeof Settings> = {
  config: Settings,
  sessions: Terminal,
  spawn: Zap,
  resume: Activity,
  fork: Activity,
  mcp: Server,
  plugins: Package,
  permissions: Shield,
  commands: Terminal,
  agents: Bot,
  skills: Sparkles,
  hooks: Activity,
  memory: Brain,
  output_styles: Eye,
  statusline: Activity,
  usage: BarChart3,
  context: Activity,
  doctor: Activity,
  backup: Activity,
  restore: Activity,
}

function CapabilityBadge({ name, detail }: { name: string; detail: AgentProviderCapabilityDetail }) {
  const Icon = CAPABILITY_ICONS[name] ?? Activity
  const isSupported = detail.state === 'supported' || detail.state === 'read_only' || detail.state === 'write_capable'
  const isWritable = detail.state === 'write_capable'

  return (
    <div
      className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors ${
        isSupported
          ? isWritable
            ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
            : 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
          : 'bg-muted text-muted-foreground opacity-50'
      }`}
      title={`${detail.label}: ${detail.state}${detail.reason ? ` — ${detail.reason}` : ''}`}
    >
      <Icon className="h-3 w-3 shrink-0" />
      <span className="truncate">{detail.label}</span>
      {isWritable && <span className="text-[10px] opacity-70">R/W</span>}
    </div>
  )
}

function ProviderCard({ provider, isSelected, onSelect }: {
  provider: AgentProviderStatus
  isSelected: boolean
  onSelect: () => void
}) {
  const navigate = useNavigate()
  const matrix = (provider.capability_matrix ?? {}) as Partial<Record<keyof AgentProviderCapabilities, AgentProviderCapabilityDetail>>
  const supportedCount = Object.values(matrix).filter(
    (d) => d.state === 'supported' || d.state === 'read_only' || d.state === 'write_capable'
  ).length
  const totalCount = Object.keys(matrix).length

  return (
    <div
      className={`${CLICKABLE_CARD} rounded-lg border p-4 ${
        isSelected ? 'border-primary bg-primary/5' : 'border-border'
      }`}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 shrink-0" />
            <p className="font-medium">{provider.display_name}</p>
            {isSelected && <Badge variant="secondary">Selected</Badge>}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {provider.version ?? provider.binary_path ?? 'Not installed'}
          </p>
        </div>
        <Badge
          variant={provider.installed ? 'outline' : 'destructive'}
          className={provider.installed ? 'text-green-600 dark:text-green-400' : undefined}
        >
          {provider.installed ? (
            <CheckCircle2 className="mr-1 h-3 w-3" />
          ) : (
            <AlertCircle className="mr-1 h-3 w-3" />
          )}
          {provider.installed ? 'Installed' : 'Missing'}
        </Badge>
      </div>

      {/* Capability progress */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
          <span>Capabilities</span>
          <span>{supportedCount}/{totalCount}</span>
        </div>
        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${totalCount > 0 ? (supportedCount / totalCount) * 100 : 0}%` }}
          />
        </div>
      </div>

      {/* Capability groups */}
      <div className="space-y-2">
        {CAPABILITY_GROUPS.map((group) => {
          const groupCaps = group.capabilities
            .map((c) => ({ name: c, detail: matrix[c as keyof AgentProviderCapabilities] }))
            .filter((c): c is { name: string; detail: AgentProviderCapabilityDetail } => !!c.detail)
          if (groupCaps.length === 0) return null
          const groupSupported = groupCaps.filter(
            (c) => c.detail.state === 'supported' || c.detail.state === 'read_only' || c.detail.state === 'write_capable'
          ).length
          return (
            <div key={group.name}>
              <div className="flex items-center gap-1.5 mb-1">
                <group.icon className="h-3 w-3 text-muted-foreground" />
                <span className="text-[11px] font-medium text-muted-foreground">{group.name}</span>
                <span className="text-[10px] text-muted-foreground">({groupSupported}/{groupCaps.length})</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {groupCaps.map(({ name, detail }) => (
                  <CapabilityBadge key={name} name={name} detail={detail} />
                ))}
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-3 pt-3 border-t">
        <Button
          variant="ghost"
          size="sm"
          className="w-full"
          onClick={(e) => {
            e.stopPropagation()
            navigate('/config')
          }}
        >
          Configure →
        </Button>
      </div>
    </div>
  )
}

export function EnhancedProviderCards() {
  const { providers, selectedProviderId, setSelectedProviderId } = useProviderContext()
  const installedCount = providers.filter((p) => p.installed).length

  return (
    <Card className="md:col-span-2 lg:col-span-3">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Bot className="h-5 w-5" />
              Agent Providers
            </CardTitle>
            <CardDescription>
              Provider capabilities and availability on this machine
            </CardDescription>
          </div>
          <Badge variant="outline">
            {installedCount}/{providers.length} installed
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 md:grid-cols-2">
          {providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              isSelected={provider.id === selectedProviderId}
              onSelect={() => setSelectedProviderId(provider.id)}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
