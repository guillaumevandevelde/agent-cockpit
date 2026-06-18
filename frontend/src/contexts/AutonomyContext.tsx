import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { autonomyApi } from '@/features/autonomy/api'
import type { ActiveAutonomy, AutonomyMode, AutonomyProfile } from '@/types/autonomy'

interface AutonomyContextValue {
  active: ActiveAutonomy | null
  profiles: AutonomyProfile[]
  loading: boolean
  error: string | null
  setActiveMode: (mode: AutonomyMode) => Promise<void>
  refresh: () => Promise<void>
}

const AutonomyContext = createContext<AutonomyContextValue | undefined>(undefined)

export function AutonomyProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<ActiveAutonomy | null>(null)
  const [profiles, setProfiles] = useState<AutonomyProfile[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [activeData, profilesData] = await Promise.all([
        autonomyApi.getActive().catch(() => ({ mode: 'suggest' as AutonomyMode, profile_name: 'Built-in', description: 'Interactive approval' })),
        autonomyApi.listProfiles().catch(() => []),
      ])
      setActive(activeData)
      setProfiles(profilesData)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load autonomy data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  const setActiveMode = useCallback(async (mode: AutonomyMode) => {
    await autonomyApi.setActive(mode)
    setActive((prev) => prev ? { ...prev, mode } : { mode, profile_name: 'Built-in', description: null })
  }, [])

  const value = useMemo<AutonomyContextValue>(() => ({
    active,
    profiles,
    loading,
    error,
    setActiveMode,
    refresh: fetchAll,
  }), [active, profiles, loading, error, setActiveMode, fetchAll])

  return (
    <AutonomyContext.Provider value={value}>
      {children}
    </AutonomyContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAutonomy() {
  const context = useContext(AutonomyContext)
  if (!context) {
    throw new Error('useAutonomy must be used within AutonomyProvider')
  }
  return context
}
