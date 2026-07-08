// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { SubscriptionUsageCard } from './SubscriptionUsageCard'
import type { SubscriptionUsageResponse } from './types'

// `toBeInTheDocument` would require `@testing-library/jest-dom`, which isn't
// installed in this repo. `getByTestId` already throws when the element is
// missing, so asserting truthiness inside `waitFor` is sufficient — and we
// avoid the dependency.
function assertInDocument(el: Element | null) {
  expect(el).toBeTruthy()
}

vi.mock('./api', () => ({
  fetchSubscriptionUsage: vi.fn(),
  fetchAnthropicPlanTier: vi.fn(),
  setAnthropicPlanTier: vi.fn(),
}))

import { fetchSubscriptionUsage } from './api'

const happy: SubscriptionUsageResponse = {
  provider: 'anthropic',
  plan_label: 'max_5x',
  periods: [
    {
      label: '5h rate',
      used: 50_000,
      limit: 220_000,
      unit: 'tokens',
      reset_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
      source: 'local',
      note: null,
    },
    {
      label: 'Weekly',
      used: 1_000_000,
      limit: null,
      unit: 'tokens',
      reset_at: null,
      source: 'local',
      note: null,
    },
  ],
  fetched_at: new Date().toISOString(),
  error: null,
  error_code: null,
}

describe('SubscriptionUsageCard', () => {
  beforeEach(() => {
    vi.mocked(fetchSubscriptionUsage).mockReset()
  })

  it('renders happy-path periods', async () => {
    vi.mocked(fetchSubscriptionUsage).mockResolvedValue(happy)
    render(
      <SubscriptionUsageCard provider="anthropic" title="Anthropic" description="desc" />,
    )
    await waitFor(() => {
      assertInDocument(screen.getByTestId('period-row-5h rate'))
      assertInDocument(screen.getByTestId('period-row-Weekly'))
    })
  })

  it('renders the not_configured state for minimax', async () => {
    vi.mocked(fetchSubscriptionUsage).mockResolvedValue({
      ...happy,
      provider: 'minimax',
      plan_label: null,
      periods: [],
      error: 'Minimax API key not configured.',
      error_code: 'not_configured',
    })
    render(<SubscriptionUsageCard provider="minimax" title="Minimax" description="d" />)
    await waitFor(() => {
      assertInDocument(screen.getByText(/Set your API key/))
    })
  })

  it('renders the plan_unknown state for anthropic', async () => {
    vi.mocked(fetchSubscriptionUsage).mockResolvedValue({
      ...happy,
      provider: 'anthropic',
      plan_label: null,
      periods: [],
      error: 'Pick a tier',
      error_code: 'plan_unknown',
    })
    render(<SubscriptionUsageCard provider="anthropic" title="Anthropic" description="d" />)
    await waitFor(() => {
      assertInDocument(screen.getByText(/Pick your plan/))
    })
  })

  it('renders the error badge for unauthorized', async () => {
    vi.mocked(fetchSubscriptionUsage).mockResolvedValue({
      ...happy,
      provider: 'minimax',
      plan_label: null,
      periods: [],
      error: 'Minimax rejected the API key.',
      error_code: 'unauthorized',
    })
    render(<SubscriptionUsageCard provider="minimax" title="Minimax" description="d" />)
    await waitFor(() => {
      const badge = screen.getByTestId('error-badge')
      expect(badge.textContent).toMatch(/rejected/)
    })
  })
})