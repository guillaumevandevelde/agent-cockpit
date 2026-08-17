// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

import { AppProviders } from './AppProviders'
import { useProjectContext } from '@/contexts/ProjectContext'
import { useProviderContext } from '@/contexts/ProviderContext'
import { useAttention } from '@/contexts/AttentionContext'
import { useTheme } from '@/contexts/ThemeContext'

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

function makeThemeProbe() {
  return function ThemeProbe() {
    // useTheme() throws "useTheme must be used within a ThemeProvider" when
    // the provider is missing from the chain — the only check we need.
    const { theme, toggleTheme } = useTheme()
    return (
      <span data-testid="theme-probe" onClick={toggleTheme} data-theme={theme}>
        wired
      </span>
    )
  }
}

describe('AppProviders', () => {
  beforeEach(() => {
    // ThemeProvider reads window.matchMedia() during initial state; jsdom
    // does not implement it. Stub so AppProviders mounts cleanly across
    // every test in this file.
    vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
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

  it('exposes the theme context so a preview that renders a ThemeToggle does not crash', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
    const ThemeProbe = makeThemeProbe()

    expect(() =>
      render(
        <AppProviders>
          <ThemeProbe />
        </AppProviders>,
      ),
    ).not.toThrow()

    // useTheme() returns { theme, toggleTheme } when the provider is in the chain;
    // the probe's data-theme attribute reflects the current theme value.
    expect(screen.getByTestId('theme-probe')).toHaveAttribute('data-theme', 'dark')
  })
})
