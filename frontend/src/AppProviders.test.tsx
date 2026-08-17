// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

import { AppProviders } from './AppProviders'
import { useProjectContext } from '@/contexts/ProjectContext'
import { useProviderContext } from '@/contexts/ProviderContext'
import { useAttention } from '@/contexts/AttentionContext'

function makeProjectProbe() {
  return function ProjectProbe() {
    const ctx = useProjectContext()
    return <span data-testid="project-probe">{ctx ? 'wired' : 'missing'}</span>
  }
}

function makeProviderProbe() {
  return function ProviderProbe() {
    const ctx = useProviderContext()
    return <span data-testid="provider-probe">{ctx ? 'wired' : 'missing'}</span>
  }
}

function makeAttentionProbe() {
  return function AttentionProbe() {
    const ctx = useAttention()
    return <span data-testid="attention-probe">{typeof ctx === 'object' && ctx !== null && 'toggle' in ctx ? 'wired' : 'missing'}</span>
  }
}

describe('AppProviders', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('mounts children without runtime errors when no router is present', () => {
    // DashboardProvider does no auto-fetch (it watches activeProject + selectedProviderId),
    // but the providers lower in the chain might. Mock fetch so an unrelated fetcher
    // cannot turn an unrelated test run into a red one.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))

    expect(() =>
      render(
        <AppProviders>
          <span data-testid="child">hello</span>
        </AppProviders>,
      ),
    ).not.toThrow()

    expect(screen.getByTestId('child')).toHaveTextContent('hello')
  })

  it('exposes the four global contexts to a deep child', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
    const ProjectProbe = makeProjectProbe()
    const ProviderProbe = makeProviderProbe()
    const AttentionProbe = makeAttentionProbe()

    render(
      <AppProviders>
        <ProjectProbe />
        <ProviderProbe />
        <AttentionProbe />
      </AppProviders>,
    )

    expect(screen.getByTestId('project-probe')).toHaveTextContent('wired')
    expect(screen.getByTestId('provider-probe')).toHaveTextContent('wired')
    expect(screen.getByTestId('attention-probe')).toHaveTextContent('wired')
  })
})
