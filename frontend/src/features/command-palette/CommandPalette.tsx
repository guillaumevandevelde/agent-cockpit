import { useEffect, useMemo } from 'react'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { useProjectContext } from '@/contexts/ProjectContext'
import { useProviderContext } from '@/contexts/ProviderContext'
import { useCommandPaletteData } from './useCommandPaletteData'

interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const { activeProject } = useProjectContext()
  const { selectedProviderId, selectedProvider } = useProviderContext()
  const { items, loading, load } = useCommandPaletteData(selectedProviderId, selectedProvider, activeProject)

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const groups = useMemo(() => {
    const byGroup = new Map<string, typeof items>()
    for (const item of items) {
      const bucket = byGroup.get(item.group)
      if (bucket) bucket.push(item)
      else byGroup.set(item.group, [item])
    }
    return byGroup
  }, [items])

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Search sessions, kanban cards, commands, MCP servers, skills..." />
      <CommandList>
        <CommandEmpty>{loading ? 'Loading…' : 'No results found.'}</CommandEmpty>
        {Array.from(groups.entries()).map(([group, groupItems]) => (
          <CommandGroup key={group} heading={group}>
            {groupItems.map((item) => (
              <CommandItem
                key={item.id}
                value={[item.title, item.subtitle, ...(item.keywords ?? [])].filter(Boolean).join(' ')}
                onSelect={() => {
                  item.onSelect()
                  onOpenChange(false)
                }}
              >
                <item.icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="flex min-w-0 flex-col">
                  <span className="truncate">{item.title}</span>
                  {item.subtitle && (
                    <span className="truncate text-xs text-muted-foreground">{item.subtitle}</span>
                  )}
                </div>
              </CommandItem>
            ))}
          </CommandGroup>
        ))}
      </CommandList>
    </CommandDialog>
  )
}
