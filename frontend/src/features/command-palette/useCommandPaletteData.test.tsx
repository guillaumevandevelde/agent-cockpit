// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor, act } from '@testing-library/react'

const navigate = vi.fn()

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
}))

vi.mock('@/features/cc-bridge/api', () => ({
  fetchCCSessions: vi.fn(async () => ({
    sessions: [
      {
        provider: 'claude-code',
        provider_display_name: 'Claude Code',
        tmux_target: 'main:0.0',
        session_name: 'feature-work',
        window_name: 'main',
        pane_id: '%1',
        cwd: '/repo',
        pid: '123',
        status: 'active',
      },
    ],
    count: 1,
  })),
}))

vi.mock('@/features/kanban/api', () => ({
  kanbanApi: {
    projectKey: vi.fn(async () => ({ project_key: 'proj-1' })),
    listCards: vi.fn(async () => ({
      items: [
        {
          id: 'card-1',
          project_key: 'proj-1',
          title: 'Ship command palette',
          description: '',
          column: 'Doing',
          rank: 'a',
          priority: 'high',
          created_at: '',
          updated_at: '',
        },
      ],
    })),
  },
}))

vi.mock('@/lib/api', () => ({
  apiClient: vi.fn(async (endpoint: string) => {
    if (endpoint.startsWith('commands')) {
      return { commands: [{ name: 'review', path: '', scope: 'user', description: 'Review code', content: '' }] }
    }
    if (endpoint.startsWith('mcp/servers')) {
      return { servers: [{ name: 'filesystem', type: 'stdio', scope: 'user' }] }
    }
    if (endpoint.startsWith('agents/skills')) {
      return { skills: [{ name: 'brainstorming', description: 'Explore requirements', location: 'user' }] }
    }
    throw new Error(`unexpected endpoint ${endpoint}`)
  }),
  buildEndpoint: (endpoint: string) => endpoint,
}))

vi.mock('@/lib/navigation', () => ({
  getNavigation: () => [
    { name: 'Nav', items: [{ name: 'Kanban', href: '/kanban', icon: () => null }] },
  ],
  supportsProvider: () => true,
}))

import { useCommandPaletteData } from './useCommandPaletteData'

afterEach(() => {
  cleanup()
  navigate.mockClear()
})

describe('useCommandPaletteData', () => {
  it('includes static navigation items immediately', () => {
    const { result } = renderHook(() => useCommandPaletteData('claude-code', null, null))
    expect(result.current.items.map((i) => i.title)).toContain('Kanban')
  })

  it('loads sessions, kanban cards, commands, mcp servers and skills', async () => {
    const { result } = renderHook(() => useCommandPaletteData('claude-code', null, { id: 1, path: '/repo' } as never))

    act(() => result.current.load())

    await waitFor(() => expect(result.current.loading).toBe(false))

    const titles = result.current.items.map((i) => i.title)
    expect(titles).toContain('feature-work')
    expect(titles).toContain('Ship command palette')
    expect(titles).toContain('/review')
    expect(titles).toContain('filesystem')
    expect(titles).toContain('brainstorming')
  })

  it('navigates to the session deep link on select', async () => {
    const { result } = renderHook(() => useCommandPaletteData('claude-code', null, null))

    act(() => result.current.load())
    await waitFor(() => expect(result.current.loading).toBe(false))

    const session = result.current.items.find((i) => i.title === 'feature-work')
    session?.onSelect()

    expect(navigate).toHaveBeenCalledWith('/cc-bridge?attach=%251')
  })

  it('skips kanban cards when there is no active project', async () => {
    const { result } = renderHook(() => useCommandPaletteData('claude-code', null, null))

    act(() => result.current.load())
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.items.map((i) => i.title)).not.toContain('Ship command palette')
  })
})
