import { useState, useCallback } from 'react'
import { apiClient, buildEndpoint } from '@/lib/api'

interface FileEntry {
  name: string
  path: string
  is_dir: boolean
}

interface DirectoryListing {
  path: string
  parent: string | null
  entries: FileEntry[]
}

export function useFileBrowser() {
  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [loading, setLoading] = useState(false)

  const navigate = useCallback(async (path?: string) => {
    setLoading(true)
    try {
      const data = await apiClient<DirectoryListing>(buildEndpoint('files', path ? { path } : undefined))
      setListing(data)
    } finally {
      setLoading(false)
    }
  }, [])

  return { listing, loading, navigate }
}
