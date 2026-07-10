import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useProviders } from '@/hooks/useProviders'
import type { AgenticCliId, AgenticCliStatus } from '@/types/providers'

interface ProviderContextValue {
  providers: AgenticCliStatus[]
  loading: boolean
  error: string | null
  selectedProviderId: AgenticCliId
  selectedProvider: AgenticCliStatus | null
  setSelectedProviderId: (providerId: AgenticCliId) => void
  refreshProviders: () => void
}

const DEFAULT_PROVIDER: AgenticCliId = 'claude-code'
const STORAGE_KEY = 'claude-cockpit:selected-provider'

const ProviderContext = createContext<ProviderContextValue | undefined>(undefined)

function readStoredProvider(): AgenticCliId {
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (stored === 'codex-cli' || stored === 'claude-code' || stored === 'mimo-code') {
    return stored
  }
  return DEFAULT_PROVIDER
}

export function ProviderProvider({ children }: { children: ReactNode }) {
  const { providers, loading, error, refresh } = useProviders()
  const [selectedProviderId, setSelectedProviderIdState] = useState<AgenticCliId>(readStoredProvider)

  useEffect(() => {
    if (providers.length === 0) return

    const installed = providers.filter((provider) => provider.installed)
    const selectedExists = providers.some((provider) => provider.id === selectedProviderId)
    if (!selectedExists) {
      setSelectedProviderIdState(DEFAULT_PROVIDER)
      return
    }
    if (installed.length === 1 && selectedProviderId !== installed[0].id) {
      setSelectedProviderIdState(installed[0].id)
    }
  }, [providers, selectedProviderId])

  const setSelectedProviderId = useCallback((providerId: AgenticCliId) => {
    window.localStorage.setItem(STORAGE_KEY, providerId)
    setSelectedProviderIdState(providerId)
  }, [])

  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.id === selectedProviderId) ?? null,
    [providers, selectedProviderId],
  )

  const value = useMemo<ProviderContextValue>(() => ({
    providers,
    loading,
    error,
    selectedProviderId,
    selectedProvider,
    setSelectedProviderId,
    refreshProviders: refresh,
  }), [providers, loading, error, selectedProviderId, selectedProvider, setSelectedProviderId, refresh])

  return (
    <ProviderContext.Provider value={value}>
      {children}
    </ProviderContext.Provider>
  )
}

export function useProviderContext() {
  const context = useContext(ProviderContext)
  if (!context) {
    throw new Error('useProviderContext must be used within ProviderProvider')
  }
  return context
}
