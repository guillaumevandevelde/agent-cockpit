// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

type SessionsParams = { project_folder?: string }
const listSessionsMock = vi.fn(
  async (_params?: SessionsParams): Promise<{ sessions: unknown[]; total: number }> => {
    void _params
    return { sessions: [], total: 0 }
  }
)
const listProjectsMock = vi.fn(async () => ({
  projects: [
    {
      folder: '-tmp-product-a',
      name: 'product-a',
      session_count: 0,
      most_recent: '2026-01-01T00:00:00+00:00',
    },
  ],
  total_sessions: 0,
}))

vi.mock('@/hooks/useSessionsApi', () => ({
  useSessionsApi: () => ({
    listProjects: listProjectsMock,
    listSessions: listSessionsMock,
    getSessionDetail: vi.fn(),
    getDashboardStats: vi.fn(),
  }),
}))

vi.mock('@/contexts/ProjectContext', () => ({
  useProjectContext: () => ({
    activeProject: {
      id: 1,
      name: 'product-a',
      path: '/tmp/product-a',
      kind: 'product',
      priority: null,
      is_active: true,
      last_accessed: '2026-01-01T00:00:00+00:00',
      created_at: '2026-01-01T00:00:00+00:00',
    },
    projects: [],
    loading: false,
    error: null,
    fetchProjects: vi.fn(),
    addProject: vi.fn(),
    removeProject: vi.fn(),
    discoverProjects: vi.fn(),
    setActiveProject: vi.fn(),
    clearActiveProject: vi.fn(),
  }),
}))

vi.mock('@/components/shared/RefreshButton', () => ({
  RefreshButton: () => null,
}))

const { SessionsPage } = await import('./SessionsPage')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('SessionsPage active-project filter', () => {
  it('seeds the project filter from the encoded folder, not the raw project path', async () => {
    render(
      <MemoryRouter initialEntries={['/sessions']}>
        <SessionsPage />
      </MemoryRouter>
    )

    await waitFor(() => expect(listSessionsMock).toHaveBeenCalled())
    const params = listSessionsMock.mock.calls[0]?.[0]
    expect(params?.project_folder).toBe('-tmp-product-a')
    expect(params?.project_folder).not.toContain('/')
  })
})