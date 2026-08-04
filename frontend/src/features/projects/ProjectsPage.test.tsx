// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const navigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  )
  return {
    ...actual,
    useNavigate: () => navigate,
  }
})

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

const spawnSession = vi.fn()
vi.mock('@/features/cc-bridge/api', () => ({
  spawnSession,
}))

const toast = vi.fn()
const toastError = vi.fn()
const toastInfo = vi.fn()
const toastSuccess = vi.fn()
vi.mock('sonner', () => ({
  toast: Object.assign(toast, {
    error: toastError,
    info: toastInfo,
    success: toastSuccess,
  }),
  Toaster: () => null,
}))

const { ProjectsPage } = await import('./ProjectsPage')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  navigate.mockClear()
  spawnSession.mockReset()
  toast.mockReset()
  toastError.mockReset()
  toastInfo.mockReset()
  toastSuccess.mockReset()
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/projects']}>
      <ProjectsPage />
    </MemoryRouter>,
  )
}

describe('ProjectsPage "Start new app" button', () => {
  it('renders the Start new app button', () => {
    renderPage()
    expect(
      screen.getByRole('button', { name: /start new app/i }),
    ).toBeInTheDocument()
  })

  it('spawns a /new-app session in the meta project directory on click', async () => {
    spawnSession.mockResolvedValueOnce({
      session_name: 'cockpit-abc',
      tmux_target: 'cockpit-abc:0.0',
      worktree_name: null,
      worktree_name_adjusted: false,
    })

    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /start new app/i }))

    await waitFor(() => expect(spawnSession).toHaveBeenCalledTimes(1))
    expect(spawnSession).toHaveBeenCalledWith({
      cli: 'claude-code',
      directory: '/home/vdvgu/claude-cockpit',
      mode: 'plain',
      prompt: '/new-app',
    })
  })

  it('navigates to the spawned session after a successful spawn', async () => {
    spawnSession.mockResolvedValueOnce({
      session_name: 'cockpit-abc',
      tmux_target: 'cockpit-abc:0.0',
      worktree_name: null,
      worktree_name_adjusted: false,
    })

    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /start new app/i }))

    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1))
    // /home/vdvgu/claude-cockpit → -home-vdvgu-claude-cockpit (slashes + dots → '-')
    expect(navigate).toHaveBeenCalledWith(
      '/sessions/-home-vdvgu-claude-cockpit/cockpit-abc',
    )
  })

  it('shows a toast error when the spawn fails', async () => {
    spawnSession.mockRejectedValueOnce(new Error('boom: directory missing'))

    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /start new app/i }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(toastError).toHaveBeenCalledWith('boom: directory missing')
  })
})
