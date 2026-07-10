import { useState, useEffect, useCallback, useRef } from 'react'
import type { RunGroup, AgentSession } from './types'
import { fetchTeams } from './api'

const POLL_INTERVAL = 5000

export function useTeams() {
  const [teams, setTeams] = useState<RunGroup[]>([])
  const [ungrouped, setUngrouped] = useState<AgentSession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await fetchTeams()
      setTeams(data.teams)
      setUngrouped(data.ungrouped)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch teams')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    intervalRef.current = setInterval(refresh, POLL_INTERVAL)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [refresh])

  return { teams, ungrouped, loading, error, refresh }
}
