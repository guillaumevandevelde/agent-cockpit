import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  KanbanSquare,
  MessageSquare,
  Server,
  Sparkles,
  Terminal,
} from 'lucide-react'
import { apiClient, buildEndpoint } from '@/lib/api'
import { getNavigation, supportsProvider } from '@/lib/navigation'
import { fetchCCSessions } from '@/features/cc-bridge/api'
import { kanbanApi } from '@/features/kanban/api'
import type { SlashCommandListResponse } from '@/types/commands'
import type { MCPServerListResponse } from '@/types/mcp'
import type { SkillListResponse } from '@/types/agents'
import type { AgenticCliId, AgenticCliStatus } from '@/types/providers'
import type { ProjectResponse } from '@/types/projects'
import type { PaletteItem } from './types'

function navigationItems(providerId: AgenticCliId, provider: AgenticCliStatus | null, navigate: (path: string) => void): PaletteItem[] {
  return getNavigation(providerId)
    .flatMap((group) => group.items)
    .filter((item) => supportsProvider(item, provider))
    .map((item) => ({
      id: `nav:${item.href}`,
      group: 'Navigation',
      title: item.name,
      icon: item.icon,
      onSelect: () => navigate(item.href),
    }))
}

async function sessionItems(navigate: (path: string) => void): Promise<PaletteItem[]> {
  const { sessions } = await fetchCCSessions()
  return sessions.map((session) => ({
    id: `session:${session.pane_id}`,
    group: 'Sessions',
    title: session.session_name || session.window_name,
    subtitle: `${session.cli_display_name}${session.provider !== session.cli ? ` · ${session.provider_display_name}` : ''} · ${session.cwd}`,
    icon: MessageSquare,
    keywords: [session.cwd, session.status, session.cli_display_name, session.provider_display_name],
    onSelect: () => navigate(`/cc-bridge?attach=${encodeURIComponent(session.pane_id)}`),
  }))
}

async function kanbanItems(activeProject: ProjectResponse | null, navigate: (path: string) => void): Promise<PaletteItem[]> {
  if (!activeProject?.path) return []
  const { project_key } = await kanbanApi.projectKey(activeProject.path)
  const { items } = await kanbanApi.listCards(project_key)
  return items.map((card) => ({
    id: `kanban:${card.id}`,
    group: 'Kanban',
    title: card.title,
    subtitle: `${card.column}${card.priority ? ` · ${card.priority}` : ''}`,
    icon: KanbanSquare,
    keywords: card.labels ?? [],
    // kanban-pro-analyse.md §4.4 (problem 1): the palette used to drop the
    // card id here, leaving the user on a plain /kanban board with the
    // drawer closed. KanbanPage already implements a `?card=<id>` deep-link
    // (the `searchParams`-driven `useEffect`), so the palette only has to
    // navigate to the deep-link URL — the drawer then opens against the
    // same card without any extra wiring. The other end of the round-trip
    // is unit-tested in `KanbanPage.test.tsx` ("already-mounted ?card=
    // deep link") so the same deep-link handles the same-mount case too.
    onSelect: () => navigate(`/kanban?card=${encodeURIComponent(card.id)}`),
  }))
}

async function commandItems(activeProject: ProjectResponse | null, navigate: (path: string) => void): Promise<PaletteItem[]> {
  const response = await apiClient<SlashCommandListResponse>(
    buildEndpoint('commands', { project_path: activeProject?.path })
  )
  return response.commands.map((command) => ({
    id: `command:${command.scope}:${command.name}`,
    group: 'Commands',
    title: `/${command.name}`,
    subtitle: command.description,
    icon: Terminal,
    onSelect: () => navigate('/commands'),
  }))
}

async function mcpItems(activeProject: ProjectResponse | null, navigate: (path: string) => void): Promise<PaletteItem[]> {
  const response = await apiClient<MCPServerListResponse>(
    buildEndpoint('mcp/servers', { project_path: activeProject?.path })
  )
  return response.servers.map((server) => ({
    id: `mcp:${server.scope}:${server.name}`,
    group: 'MCP Servers',
    title: server.name,
    subtitle: `${server.type} · ${server.scope}`,
    icon: Server,
    onSelect: () => navigate('/mcp'),
  }))
}

async function skillItems(activeProject: ProjectResponse | null, navigate: (path: string) => void): Promise<PaletteItem[]> {
  const response = await apiClient<SkillListResponse>(
    buildEndpoint('agents/skills', { project_path: activeProject?.path })
  )
  return response.skills.map((skill) => ({
    id: `skill:${skill.location}:${skill.name}`,
    group: 'Skills',
    title: skill.name,
    subtitle: skill.description ?? undefined,
    icon: Sparkles,
    onSelect: () => navigate('/skills'),
  }))
}

export interface UseCommandPaletteDataResult {
  items: PaletteItem[]
  loading: boolean
  load: () => void
}

export function useCommandPaletteData(
  providerId: AgenticCliId,
  provider: AgenticCliStatus | null,
  activeProject: ProjectResponse | null
): UseCommandPaletteDataResult {
  const navigate = useNavigate()
  const [dynamicItems, setDynamicItems] = useState<PaletteItem[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([
      sessionItems(navigate),
      kanbanItems(activeProject, navigate),
      commandItems(activeProject, navigate),
      mcpItems(activeProject, navigate),
      skillItems(activeProject, navigate),
    ])
      .then((results) => {
        setDynamicItems(
          results.flatMap((result) => (result.status === 'fulfilled' ? result.value : []))
        )
      })
      .finally(() => setLoading(false))
  }, [navigate, activeProject])

  const items = [...navigationItems(providerId, provider, navigate), ...dynamicItems]

  return { items, loading, load }
}
