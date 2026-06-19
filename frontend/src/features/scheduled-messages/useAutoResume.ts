import { useState, useEffect, useCallback } from 'react'
import { getAutoResume, setAutoResume } from './api'

export function useAutoResume(cwd: string | null) {
  const [enabled, setEnabled] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!cwd) return
    setLoading(true)
    getAutoResume(cwd)
      .then((res) => setEnabled(res.enabled))
      .catch(() => setEnabled(false))
      .finally(() => setLoading(false))
  }, [cwd])

  const toggle = useCallback(async () => {
    if (!cwd) return
    setLoading(true)
    try {
      const res = await setAutoResume(cwd, !enabled)
      setEnabled(res.enabled)
    } finally {
      setLoading(false)
    }
  }, [cwd, enabled])

  return { enabled, loading, toggle }
}
