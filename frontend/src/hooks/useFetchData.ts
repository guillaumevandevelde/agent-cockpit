import { useState, useEffect, useCallback, useRef, type DependencyList } from 'react'

export interface UseFetchDataResult<T> {
  data: T | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useFetchData<T>(
  fetcher: () => Promise<T>,
  deps: DependencyList,
  onError?: (message: string) => void
): UseFetchDataResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)

  const onErrorRef = useRef(onError)
  useEffect(() => {
    onErrorRef.current = onError
  })

  // Memoize the caller's fetcher against the caller-supplied deps, mirroring the
  // inline `useCallback(async () => {...}, [deps])` boilerplate this hook replaces.
  // eslint-disable-next-line react-hooks/use-memo
  const run = useCallback(fetcher, deps)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await run())
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      setError(message)
      onErrorRef.current?.(message)
    } finally {
      setLoading(false)
    }
  }, [run])

  useEffect(() => {
    load()
  }, [load])

  return { data, loading, error, refresh: load }
}
