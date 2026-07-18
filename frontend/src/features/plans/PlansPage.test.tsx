// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import type { PlansOverviewResponse } from '@/types/plans'

const overview: PlansOverviewResponse = {
  project_key: 'git:github.com/example/repo',
  cards: [
    {
      deliverable_id: 'deliv-card-1',
      kind: 'plan',
      card_id: 'card-abc',
      card_title: 'Aggregator design doc',
      excerpt: '# Plan\n\nThe plan body excerpt.',
      created_at: new Date(Date.now() - 60_000).toISOString(),
    },
    {
      deliverable_id: 'deliv-card-2',
      kind: 'plan_ref',
      card_id: 'card-def',
      card_title: 'Optie B implementation',
      excerpt: '{"parent_card_id": "x"}',
      created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    },
  ],
  docs: [
    {
      path: 'docs/cockpit/plans-feature-decision.md',
      title: '# Plans feature — analyse & richting',
      modified_at: new Date(Date.now() - 60 * 60_000).toISOString(),
      size_bytes: 12345,
    },
    {
      path: 'docs/cockpit/kanban-conventions.md',
      title: '# Kanban string-conventies',
      modified_at: new Date(Date.now() - 24 * 60 * 60_000).toISOString(),
      size_bytes: 4096,
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
    getDocContent: vi.fn(async () => ({
      path: 'docs/cockpit/foo.md',
      title: '# Foo',
      content: '# Foo\n\nbody',
      modified_at: '2026-07-17T00:00:00Z',
      size_bytes: 100,
    })),
  }),
}))

const { PlansPage } = await import('./PlansPage')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderPlans() {
  return render(
    <MemoryRouter initialEntries={['/plans']}>
      <PlansPage />
    </MemoryRouter>
  )
}

describe('PlansPage aggregator rendering', () => {
  it('renders the two-section header and the project key', async () => {
    renderPlans()
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: /plans & specs/i })
      ).toBeTruthy()
    )
    // The project key is rendered inside ``<p>Project: <key></p>`` so the
    // text node is split between "Project: " and the key — use a regex
    // matcher against the full paragraph content instead of strict equality.
    expect(
      screen.getByText((_, el) => el?.tagName === 'P' && /git:github\.com\/example\/repo/.test(el.textContent ?? ''))
    ).toBeTruthy()
    expect(
      screen.getByRole('heading', { level: 2, name: /from kanban cards/i })
    ).toBeTruthy()
    expect(
      screen.getByRole('heading', { level: 2, name: /from cockpit docs/i })
    ).toBeTruthy()
  })

  it('renders every B-section deliverable as a clickable row', async () => {
    renderPlans()
    await waitFor(() =>
      expect(screen.getByText('Aggregator design doc')).toBeTruthy()
    )
    expect(screen.getByText('Optie B implementation')).toBeTruthy()
    // Each row is a button-role element — a11y-friendly landmark.
    const aggregatorRow = screen.getByRole('button', {
      name: /open kanban card aggregator design doc/i,
    })
    const optieBRow = screen.getByRole('button', {
      name: /open kanban card optie b implementation/i,
    })
    expect(aggregatorRow).toBeTruthy()
    expect(optieBRow).toBeTruthy()
    // Both kinds surface in the section so the user can tell which
    // deliverable they're clicking.
    expect(aggregatorRow.textContent).toContain('plan')
    expect(optieBRow.textContent).toContain('plan_ref')
  })

  it('renders every C-section doc with its path + title', async () => {
    renderPlans()
    await waitFor(() =>
      expect(
        screen.getByText('docs/cockpit/plans-feature-decision.md')
      ).toBeTruthy()
    )
    expect(
      screen.getByText('docs/cockpit/kanban-conventions.md')
    ).toBeTruthy()
    // Doc rows are clickable (open detail page).
    expect(
      screen.getByRole('button', {
        name: /open doc # plans feature — analyse/i,
      })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: /open doc # kanban string-conventies/i })
    ).toBeTruthy()
  })

  it('filters both sections by the search query', async () => {
    renderPlans()
    await waitFor(() =>
      expect(screen.getByText('Aggregator design doc')).toBeTruthy()
    )

    const search = screen.getByPlaceholderText(/filter by title or path/i)
    // 'plans-feature' appears in only the C doc with that suffix, not in
    // any B card title, so this query exercises the cross-section filter.
    fireEvent.change(search, { target: { value: 'plans-feature' } })

    // Wait for C to filter (the kanban-conventions doc must disappear).
    await waitFor(() =>
      expect(
        screen.queryByText('docs/cockpit/kanban-conventions.md')
      ).toBeNull()
    )
    expect(
      screen.getByText('docs/cockpit/plans-feature-decision.md')
    ).toBeTruthy()
    // B section is fully filtered out — no card titles contain 'plans-feature'.
    expect(screen.queryByText('Aggregator design doc')).toBeNull()
    expect(screen.queryByText('Optie B implementation')).toBeNull()
  })

  it('shows per-section empty copy when both sections are empty', async () => {
    // Drive a separate factory scope via vi.doMock so the per-test override
    // doesn't fight the hoisted top-of-file mock. We re-import the page
    // *inside* the test so the side-effect-only module-mock goes first.
    vi.resetModules()
    vi.doMock('@/hooks/usePlansApi', () => ({
      usePlansApi: () => ({
        getOverview: vi.fn(async () => ({
          project_key: 'slug:empty',
          cards: [],
          docs: [],
        })),
        getDocContent: vi.fn(),
      }),
    }))
    const { PlansPage: EmptyPlansPage } = await import('./PlansPage')
    render(
      <MemoryRouter initialEntries={['/plans']}>
        <EmptyPlansPage />
      </MemoryRouter>
    )
    expect(
      await screen.findByText(/no card plans in this project yet/i)
    ).toBeTruthy()
    expect(await screen.findByText(/no docs in docs\/cockpit\//i)).toBeTruthy()
    vi.doUnmock('@/hooks/usePlansApi')
    vi.resetModules()
  })
})
