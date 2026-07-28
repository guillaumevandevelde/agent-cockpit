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
      spec_doc: 'docs/cockpit/plans-feature-decision.md',
    },
    {
      deliverable_id: 'deliv-card-2',
      kind: 'plan_ref',
      card_id: 'card-def',
      card_title: 'Optie B implementation',
      excerpt: '{"parent_card_id": "x"}',
      created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      // No spec_doc anchor — should render without an inline doclink.
      spec_doc: null,
    },
  ],
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
      // No cards claim this doc — should render the heading without chips.
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
    // ``Aggregator design doc`` appears twice on the rendered page (B-row
    // title + C-row implemented_by chip). Scope the B-row by its button
    // role + aria-label, then assert both B-rows are present.
    const aggregatorRow = await screen.findByRole('button', {
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
    // ``docs/cockpit/plans-feature-decision.md`` appears twice on the
    // rendered page (C-row path text + B-row inline spec_doc link text).
    // Scope via the doc-row button aria-label.
    const plansDocRow = await screen.findByRole('button', {
      name: /open doc # plans feature — analyse/i,
    })
    const conventionsDocRow = screen.getByRole('button', {
      name: /open doc # kanban string-conventies/i,
    })
    expect(plansDocRow.textContent).toContain(
      'docs/cockpit/plans-feature-decision.md'
    )
    expect(conventionsDocRow.textContent).toContain(
      'docs/cockpit/kanban-conventions.md'
    )
  })

  it('filters both sections by the search query', async () => {
    renderPlans()
    await screen.findByRole('button', {
      name: /open kanban card aggregator design doc/i,
    })

    const search = screen.getByPlaceholderText(/filter by title or path/i)
    // 'kanban-conventions' is the unique substring of the second C-doc's
    // path. Neither B card title nor B spec_doc contain this substring
    // (B spec_doc is the plans-feature-decision.md path) — so this query
    // exercises the cross-section filter without leaking through the new
    // spec_doc correlation field.
    fireEvent.change(search, { target: { value: 'kanban-conventions' } })

    // Wait for C to filter (the plans-feature doc must disappear).
    await waitFor(() =>
      expect(
        screen.queryByRole('button', {
          name: /open doc # plans feature — analyse/i,
        })
      ).toBeNull()
    )
    expect(
      screen.getByRole('button', {
        name: /open doc # kanban string-conventies/i,
      })
    ).toBeTruthy()
    // B section is fully filtered out — no card titles or spec_docs
    // contain 'kanban-conventions'.
    expect(
      screen.queryByRole('button', {
        name: /open kanban card aggregator design doc/i,
      })
    ).toBeNull()
    expect(
      screen.queryByRole('button', {
        name: /open kanban card optie b implementation/i,
      })
    ).toBeNull()
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

describe('PlansPage B↔C correlation (Optie B, kanban plan 2026-07-28-plans-b-c-correlation Task 2)', () => {
  it('renders an "Implemented by cards" chip-list on a C row that has implemented_by entries', async () => {
    renderPlans()
    const plansDocRow = await screen.findByRole('button', {
      name: /open doc # plans feature — analyse/i,
    })
    // Heading + chip-list live on the same C row.
    expect(
      screen.getByRole('heading', { name: /implemented by cards/i })
    ).toBeTruthy()
    // The chip-list surfaces both back-references by title.
    expect(plansDocRow.textContent).toContain('Aggregator design doc')
    expect(plansDocRow.textContent).toContain('B↔C correlation follow-up')
  })

  it('omits the "Implemented by cards" chip-list when implemented_by is empty', async () => {
    renderPlans()
    await screen.findByRole('button', {
      name: /open doc # kanban string-conventies/i,
    })
    // Only the populated C row renders the chip-list heading; the empty
    // doc row stays chip-less (the only such heading is the populated one).
    expect(
      screen.getAllByRole('heading', { name: /implemented by cards/i }).length
    ).toBe(1)
  })

  it('renders a clickable doclink on a B row when spec_doc is set', async () => {
    renderPlans()
    const aggregatorRow = await screen.findByRole('button', {
      name: /open kanban card aggregator design doc/i,
    })
    // Find the inline anchor inside the B-row. The link's aria-label
    // names the doc path so it's uniquely scoped to this row.
    const docLink = screen.getByRole('link', {
      name: /open spec doc docs\/cockpit\/plans-feature-decision\.md/i,
    })
    expect(docLink).toBeTruthy()
    expect(docLink.getAttribute('href')).toBe(
      '/plans/docs%2Fcockpit%2Fplans-feature-decision.md'
    )
    // The doclink is rendered inside the B-row card.
    expect(aggregatorRow.contains(docLink)).toBe(true)
  })

  it('omits the inline doclink when spec_doc is null', async () => {
    renderPlans()
    const optieBRow = await screen.findByRole('button', {
      name: /open kanban card optie b implementation/i,
    })
    // The "Optie B implementation" row has spec_doc=null — there must be
    // no inline anchor on its row.
    const inlineDocLinksInRow = optieBRow.querySelectorAll('a[href^="/plans/"]')
    expect(inlineDocLinksInRow.length).toBe(0)
  })

  it('renders chip links that navigate to /kanban?card=<id>', async () => {
    renderPlans()
    const plansDocRow = await screen.findByRole('button', {
      name: /open doc # plans feature — analyse/i,
    })
    // Two implemented_by chips on the populated C row; both are anchor
    // elements that route to /kanban?card=<id>.
    const chipCardAbc = plansDocRow.querySelector(
      'a[href="/kanban?card=card-abc"]'
    )
    const chipCardGhi = plansDocRow.querySelector(
      'a[href="/kanban?card=card-ghi"]'
    )
    expect(chipCardAbc).toBeTruthy()
    expect(chipCardGhi).toBeTruthy()
    // Chips live inside a CLICKABLE_CARD row — without stopPropagation,
    // a chip click would bubble to the row's onClick and navigate to
    // /plans/<encoded-path>. We assert the chip anchors are inside a
    // parent that has a button role — i.e., they would bubble without
    // an explicit stopPropagation. The implementation owns the
    // stopPropagation contract on the inner click handler.
    expect(chipCardAbc?.closest('[role="button"]')).toBeTruthy()
    expect(chipCardGhi?.closest('[role="button"]')).toBeTruthy()
  })
})
