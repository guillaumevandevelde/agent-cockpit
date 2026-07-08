import type { AgentProviderCapabilities, AgentProviderId, AgentProviderStatus } from '@/types/providers'
import {
  LayoutDashboard,
  Settings,
  Server,
  Terminal,
  Package,
  Webhook,
  Shield,
  KeyRound,
  Bot,
  Sparkles,
  Brain,
  Paintbrush,
  Activity,
  MessageSquare,
  BarChart3,
  FolderOpen,
  Archive,
  Gauge,
  ClipboardList,
  MonitorPlay,
  Radio,
  CalendarClock,
  KanbanSquare,
  Boxes,
  TrendingUp,
  Network,
  Castle,
  Globe,
  RefreshCw,
  type LucideIcon,
} from 'lucide-react'

export type NavItem = {
  name: string
  href: string
  icon: LucideIcon
  capability?: keyof AgentProviderCapabilities
}

export type NavGroup = {
  name: string
  items: NavItem[]
}

const commonNavigation: NavGroup[] = [
  {
    name: 'Core',
    items: [
      { name: 'Dashboard', href: '/', icon: LayoutDashboard },
      { name: 'Projects', href: '/projects', icon: FolderOpen },
      { name: 'Agent Bridge', href: '/agent-bridge', icon: MonitorPlay, capability: 'sessions' },
      { name: 'Subscriptions', href: '/subscriptions', icon: KeyRound },
    ],
  },
  {
    name: 'Operations',
    items: [
      { name: 'APM', href: '/apm', icon: Boxes },
      { name: 'Presence', href: '/presence', icon: Radio },
      { name: 'Scheduled Messages', href: '/scheduled-messages', icon: CalendarClock },
      { name: 'Kanban', href: '/kanban', icon: KanbanSquare },
      { name: 'Agent Performance', href: '/agent-performance', icon: TrendingUp },
      { name: 'Plans', href: '/plans', icon: ClipboardList },
      { name: 'MCP Server', href: '/mcp-server', icon: Network },
      { name: 'Sandcastle', href: '/sandcastle', icon: Castle },
      { name: 'Hosts', href: '/hosts', icon: Globe },
      { name: 'Backup', href: '/backup', icon: Archive, capability: 'backup' },
      { name: 'Updates', href: '/updates', icon: RefreshCw },
    ],
  },
]

const providerNavigation: Record<AgentProviderId, NavGroup[]> = {
  'claude-code': [
    {
      name: 'Claude Code',
      items: [
        { name: 'Config', href: '/config', icon: Settings, capability: 'config' },
        { name: 'Sessions', href: '/sessions', icon: MessageSquare, capability: 'sessions' },
        { name: 'MCP Servers', href: '/mcp', icon: Server, capability: 'mcp' },
        { name: 'Plugins', href: '/plugins', icon: Package, capability: 'plugins' },
        { name: 'Permissions / Trust', href: '/permissions', icon: Shield, capability: 'permissions' },
      ],
    },
    {
      name: 'Claude Tools',
      items: [
        { name: 'Commands', href: '/commands', icon: Terminal, capability: 'commands' },
        { name: 'Hooks', href: '/hooks', icon: Webhook, capability: 'hooks' },
        { name: 'Agents', href: '/agents', icon: Bot, capability: 'agents' },
        { name: 'Skills', href: '/skills', icon: Sparkles, capability: 'skills' },
        { name: 'Memory', href: '/memory', icon: Brain, capability: 'memory' },
        { name: 'Output Styles', href: '/output-styles', icon: Paintbrush, capability: 'output_styles' },
        { name: 'Status Line', href: '/statusline', icon: Activity, capability: 'statusline' },
      ],
    },
    {
      name: 'Claude Metrics',
      items: [
        { name: 'Context', href: '/context', icon: Gauge, capability: 'context' },
        { name: 'Usage', href: '/usage', icon: BarChart3, capability: 'usage' },
      ],
    },
  ],
  'codex-cli': [
    {
      name: 'Codex',
      items: [
        { name: 'Config', href: '/config', icon: Settings, capability: 'config' },
      ],
    },
  ],
  'copilot-cli': [],
  'mimo-code': [
    {
      name: 'MiMoCode',
      items: [
        { name: 'Config', href: '/config', icon: Settings, capability: 'config' },
        { name: 'Skills', href: '/skills', icon: Sparkles, capability: 'skills' },
        { name: 'Memory', href: '/memory', icon: Brain, capability: 'memory' },
      ],
    },
  ],
  'open-code': [
    {
      name: 'OpenCode',
      items: [
        { name: 'Config', href: '/config', icon: Settings, capability: 'config' },
        { name: 'Sessions', href: '/sessions', icon: MessageSquare, capability: 'sessions' },
        { name: 'MCP Servers', href: '/mcp', icon: Server, capability: 'mcp' },
        { name: 'Plugins', href: '/plugins', icon: Package, capability: 'plugins' },
      ],
    },
    {
      name: 'OpenCode Tools',
      items: [
        { name: 'Agents', href: '/agents', icon: Bot, capability: 'agents' },
        { name: 'Skills', href: '/skills', icon: Sparkles, capability: 'skills' },
        { name: 'Memory', href: '/memory', icon: Brain, capability: 'memory' },
        { name: 'Commands', href: '/commands', icon: Terminal, capability: 'commands' },
      ],
    },
    {
      name: 'OpenCode Metrics',
      items: [
        { name: 'Usage', href: '/usage', icon: BarChart3, capability: 'usage' },
      ],
    },
  ],
}

export function getNavigation(providerId: AgentProviderId): NavGroup[] {
  return [
    ...commonNavigation,
    ...(providerNavigation[providerId] ?? []),
  ]
}

const visibleCapabilityStates = new Set(['supported', 'read_only', 'write_capable'])

export function supportsProvider(item: NavItem, provider: AgentProviderStatus | null) {
  if (!item.capability || !provider) return true
  const detail = provider.capability_matrix?.[item.capability]
  if (detail) return visibleCapabilityStates.has(detail.state)
  return Boolean(provider.capabilities?.[item.capability])
}
