// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, createEvent, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import type { DocContentResponse, PlansOverviewResponse } from '@/types/plans'

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

const docContent: DocContentResponse = {
  path: 'docs/cockpit/plans-feature-decision.md',
  title: '# Plans feature — analyse & richting',
  content: '# Plans feature\n\nThe decision body.',
  modified_at: '2026-07-17T00:00:00Z',
  size_bytes: 12345,
}

const kanbanConventionsContent: DocContentResponse = {
  path: 'docs/cockpit/kanban-conventions.md',
  title: '# Kanban string-conventies',
  content: '# Kanban string-conventies\n\nThe conventions body.',
  modified_at: '2026-07-17T00:00:00Z',
  size_bytes: 4096,
}

const overview: PlansOverviewResponse = {
  project_key: 'git:github.com/example/repo',
  cards: [],
  docs: [
    {
      path: 'docs/cockpit/plans-feature-decision.md',
      title: '# Plans feature — analyse & richting',
      modified_at: new Date(Date.now() - 60 * 60_000).toISOString(),
      size_bytes: 12345,
      implemented_by: [
        { card_id: 'card-abc', card_title: 'Aggregator design doc' },
        { card_id: 'card-ghi', card_title: 'B↔C correlation follow-up' },
      ],
    },
    {
      path: 'docs/cockpit/kanban-conventions.md',
      title: '# Kanban string-conventies',
      modified_at: new Date(Date.now() - 24 * 60 * 60_000).toISOString(),
      size_bytes: 4096,
      implemented_by: [],
    },
  ],
}

vi.mock('@/contexts/ProjectContext', () => ({
  useProjectContext: () => ({
    activeProject: {
      path: '/tmp/test-project',
      id: '1',
      name: 'test-project',
      is_active: true,
    },
  }),
}))

vi.mock('@/hooks/usePlansApi', () => ({
  usePlansApi: () => ({
    getOverview: vi.fn(async () => overview),
    getDocContent: vi.fn(async (path: string) =>
      path === 'docs/cockpit/kanban-conventions.md'
        ? kanbanConventionsContent
        : docContent
    ),
  }),
}))

const { PlanDetailPage } = await import('./PlanDetailPage')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  navigate.mockClear()
})

function renderDetail(pathParam: string) {
  // The route is registered as ``plans/:filename`` with the encoded path
  // — the production router (``BrowserRouter``) decodes ``useParams`` for
  // us, but ``MemoryRouter`` also decodes path params, so we can pass the
  // encoded path directly.
  return render(
    <MemoryRouter initialEntries={[`/plans/${pathParam}`]}>
      <Routes>
        <Route path="plans/:filename" element={<PlanDetailPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('PlanDetailPage rendering', () => {
  it('renders the doc content fetched via usePlansApi', async () => {
    renderDetail(encodeURIComponent('docs/cockpit/plans-feature-decision.md'))
    await screen.findByRole('heading', {
      level: 1,
      name: /plans feature — analyse/i,
    })
    // Body content rendered through MarkdownRenderer.
    expect(screen.getByText('The decision body.')).toBeTruthy()
  })

  it('renders the loading copy while the doc-content fetch is pending', async () => {
    // Slow-doc variant: getDocContent resolves on a manual tick.
    vi.resetModules()
    const deferred: { resolve: ((value: DocContentResponse) => void) | null } = {
      resolve: null,
    }
    vi.doMock('@/hooks/usePlansApi', () => ({
      usePlansApi: () => ({
        getOverview: vi.fn(async () => overview),
        getDocContent: vi.fn(
          () =>
            new Promise<DocContentResponse>((resolve) => {
              deferred.resolve = resolve
            })
        ),
      }),
    }))
    const { PlanDetailPage: SlowPlanDetailPage } = await import(
      './PlanDetailPage'
    )
    render(
      <MemoryRouter
        initialEntries={[
          `/plans/${encodeURIComponent('docs/cockpit/plans-feature-decision.md')}`,
        ]}
      >
        <Routes>
          <Route path="plans/:filename" element={<SlowPlanDetailPage />} />
        </Routes>
      </MemoryRouter>
    )
    expect(await screen.findByText(/loading doc/i)).toBeTruthy()
    deferred.resolve?.(docContent)
    vi.doUnmock('@/hooks/usePlansApi')
    vi.resetModules()
  })
})

describe('PlanDetailPage B↔C correlation (Optie B, kanban plan 2026-07-28-plans-b-c-correlation Task 2)', () => {
  it('renders an "Implemented by cards" chip-list sourced from the overview fetch', async () => {
    renderDetail(encodeURIComponent('docs/cockpit/plans-feature-decision.md'))
    await screen.findByRole('heading', {
      level: 1,
      name: /plans feature — analyse/i,
    })
    // Chip-list heading.
    expect(
      screen.getByRole('heading', { name: /implemented by cards/i })
    ).toBeTruthy()
    // Chip titles (sourced from the overview's implemented_by list).
    expect(screen.getByText('Aggregator design doc')).toBeTruthy()
    expect(screen.getByText('B↔C correlation follow-up')).toBeTruthy()
  })

  it('renders chip links that navigate to /kanban?card=<id>', async () => {
    renderDetail(encodeURIComponent('docs/cockpit/plans-feature-decision.md'))
    await screen.findByRole('heading', {
      level: 1,
      name: /plans feature — analyse/i,
    })
    const chipCardAbc = screen.getByRole('link', {
      name: /open kanban card aggregator design doc/i,
    })
    const chipCardGhi = screen.getByRole('link', {
      name: /open kanban card b↔c correlation follow-up/i,
    })
    expect(chipCardAbc.getAttribute('href')).toBe('/kanban?card=card-abc')
    expect(chipCardGhi.getAttribute('href')).toBe('/kanban?card=card-ghi')
  })

  // I1 (PlanDetailPage side) — the chip anchors used to be raw <a>
  // elements without an onClick handler, so clicking them caused a full
  // browser navigation (SPA hard-reload). The interaction test below
  // pins the router-integrated behaviour: a chip click must prevent
  // the default browser navigation AND fire the SPA navigate().
  it('card-chip click prevents default browser navigation AND fires SPA navigate', async () => {
    navigate.mockClear()
    renderDetail(encodeURIComponent('docs/cockpit/plans-feature-decision.md'))
    await screen.findByRole('heading', {
      level: 1,
      name: /plans feature — analyse/i,
    })
    const chipCardAbc = screen.getByRole('link', {
      name: /open kanban card aggregator design doc/i,
    }) as HTMLAnchorElement
    const evt = createEvent.click(chipCardAbc, { bubbles: true, cancelable: true })
    fireEvent(chipCardAbc, evt)
    // SPA navigation fired with the chip's href target — the chip
    // click must hit React Router, not the browser-default <a> reload.
    expect(navigate).toHaveBeenCalledWith('/kanban?card=card-abc')
    // Default browser navigation was prevented — without this the SPA
    // hard-reloads on every chip click (the original I1 bug).
    expect(evt.defaultPrevented).toBe(true)
  })

  it('omits the chip-list when the current doc item has empty implemented_by', async () => {
    renderDetail(encodeURIComponent('docs/cockpit/kanban-conventions.md'))
    // The doc title H1 + the body H1 both render "Kanban string-conventies" —
    // wait for the body to mount (which only renders after the doc fetch
    // resolves) before asserting no chip-list is present.
    await screen.findByText('The conventions body.')
    // The empty-implemented_by doc has no chip-list — the heading should
    // not appear on this page.
    expect(
      screen.queryByRole('heading', { name: /implemented by cards/i })
    ).toBeNull()
  })
})

describe('PlanDetailPage doc-content error path (regression)', () => {
  it('renders the error state when getDocContent rejects', async () => {
    vi.resetModules()
    vi.doMock('@/hooks/usePlansApi', () => ({
      usePlansApi: () => ({
        getOverview: vi.fn(async () => overview),
        getDocContent: vi.fn(async () => {
          throw new Error('boom')
        }),
      }),
    }))
    const { PlanDetailPage: ErroredPlanDetailPage } = await import(
      './PlanDetailPage'
    )
    render(
      <MemoryRouter
        initialEntries={[
          `/plans/${encodeURIComponent('docs/cockpit/plans-feature-decision.md')}`,
        ]}
      >
        <Routes>
          <Route path="plans/:filename" element={<ErroredPlanDetailPage />} />
        </Routes>
      </MemoryRouter>
    )
    // ``CardTitle`` for the error state.
    await waitFor(() =>
      expect(screen.getByText(/error/i)).toBeTruthy()
    )
    expect(screen.getByText('boom')).toBeTruthy()
    vi.doUnmock('@/hooks/usePlansApi')
    vi.resetModules()
  })
})
