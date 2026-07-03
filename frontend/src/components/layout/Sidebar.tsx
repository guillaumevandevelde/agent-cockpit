import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { getNavigation, supportsProvider, type NavGroup } from '@/lib/navigation'
import { ProjectSwitcher } from '@/features/projects/ProjectSwitcher'
import { useSidebar } from '@/contexts/SidebarContext'
import { useProviderContext } from '@/contexts/ProviderContext'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { AgentProviderId, AgentProviderStatus } from '@/types/providers'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'

function NavGroupSection({ group, collapsed, selectedProvider }: {
  group: NavGroup
  collapsed: boolean
  selectedProvider: AgentProviderStatus | null
}) {
  const visibleItems = group.items.filter((item) => supportsProvider(item, selectedProvider))
  if (visibleItems.length === 0) return null

  return (
    <div className="space-y-1">
      {!collapsed && (
        <p className="px-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {group.name}
        </p>
      )}
      {visibleItems.map((item) => (
        <NavLink
          key={item.href}
          to={item.href}
          end={item.href === '/'}
          title={collapsed ? item.name : undefined}
          className={({ isActive }) =>
            cn(
              'flex items-center rounded-md text-sm font-medium transition-colors',
              collapsed ? 'justify-center p-2' : 'gap-2 px-3 py-2',
              isActive
                ? 'bg-primary text-primary-foreground'
                : 'text-foreground hover:bg-accent hover:text-accent-foreground'
            )
          }
        >
          <item.icon className="h-4 w-4 shrink-0" />
          {!collapsed && item.name}
        </NavLink>
      ))}
    </div>
  )
}

export function Sidebar() {
  const { collapsed, setCollapsed } = useSidebar()
  const { providers, selectedProviderId, selectedProvider, setSelectedProviderId } = useProviderContext()
  const visibleGroups = getNavigation(selectedProviderId)

  return (
    <aside className={cn(
      'border-r bg-background transition-all duration-200 flex flex-col',
      collapsed ? 'w-14' : 'w-64'
    )}>
      {!collapsed && (
        <div className="py-4 border-b space-y-3">
          <ProjectSwitcher />
          <div className="px-4 space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">Agent Provider</p>
            <Select value={selectedProviderId} onValueChange={(value) => setSelectedProviderId(value as AgentProviderId)}>
              <SelectTrigger className="h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providers.map((provider) => (
                  <SelectItem key={provider.id} value={provider.id}>
                    {provider.display_name}{!provider.installed ? ' (missing)' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      )}
      <nav className={cn(
        'flex flex-col flex-1 overflow-y-auto',
        collapsed ? 'gap-1 p-2' : 'gap-4 p-4'
      )}>
        {visibleGroups.map((group) => (
          <NavGroupSection
            key={group.name}
            group={group}
            collapsed={collapsed}
            selectedProvider={selectedProvider}
          />
        ))}
      </nav>
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center justify-center p-3 border-t text-muted-foreground hover:text-foreground transition-colors"
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
      </button>
    </aside>
  )
}
