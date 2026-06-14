import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient, buildEndpoint } from './api'

describe('buildEndpoint', () => {
  it('omits undefined query values', () => {
    expect(buildEndpoint('projects', { active: true, path: undefined })).toBe(
      'projects?active=true',
    )
  })
})

describe('apiClient', () => {
  afterEach(() => vi.restoreAllMocks())

  it('handles empty successful responses', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 204 }),
    )

    await expect(apiClient('projects/active', { method: 'DELETE' })).resolves.toEqual({})
  })
})
