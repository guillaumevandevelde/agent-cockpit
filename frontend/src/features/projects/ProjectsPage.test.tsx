// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const fetchProjects = vi.fn()
const metaProject = {
  id: 7,
  name: 'claude-cockpit',
  path: '/home/vdvgu/claude-cockpit',
  source: 'configured',
  kind: 'meta',
  priority: null,
  is_active: true,
  last_accessed: '2026-08-01T00:00:00+00:00',
  created_at: '2026-08-01T00:00:00+00:00',
}

vi.mock('@/contexts/ProjectContext', () => ({
  useProjectContext: () => ({
    projects: [metaProject],
    loading: false,
    error: null,
    fetchProjects,
    addProject: vi.fn(),
    removeProject: vi.fn(),
    discoverProjects: vi.fn(),
    setActiveProject: vi.fn(),
    clearActiveProject: vi.fn(),
  }),
}))

const { ProjectsPage } = await import('./ProjectsPage')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/projects']}>
      <ProjectsPage />
    </MemoryRouter>,
  )
}

describe('ProjectsPage', () => {
  it('lists the tracked projects', () => {
    renderPage()
    expect(screen.getByText('Tracked Projects')).toBeInTheDocument()
    expect(screen.getByText('1 project tracked')).toBeInTheDocument()
  })

  it('offers the two surviving entry points: Add Folder and Discover', () => {
    renderPage()
    expect(screen.getByRole('button', { name: /add folder/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /discover projects/i })).toBeInTheDocument()
  })

  // Regression guard for the cleanup in docs/cockpit/kern-terugbrengen-plan.md
  // §4 fase 6. Both blocks were removed on request; these negative assertions
  // are what keeps them from drifting back in unnoticed.
  it('does not render the spec-driven "start new app" block', () => {
    renderPage()
    expect(screen.queryByRole('button', { name: /start new app/i })).toBeNull()
    expect(screen.queryByText(/spec-driven/i)).toBeNull()
  })

  it('does not render the "Wacht op jou" queue', () => {
    renderPage()
    expect(screen.queryByText(/wacht op jou/i)).toBeNull()
  })
})
