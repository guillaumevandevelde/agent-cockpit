import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient, buildEndpoint } from './api'
import { API_BASE_URL } from './constants'

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

  // Regression: callers used to pass an absolute '/api/v1/...' path, which
  // apiClient prefixed again into '/api/v1//api/v1/...' — a 404 Starlette does
  // not normalise away (the MCP Server page's "Failed to load tokens" toast).
  // The eslint `no-restricted-syntax` guard in frontend/eslint.config.js keeps
  // the absolute form out; this test pins the prefixing contract it relies on.
  it('prefixes the relative endpoint with API_BASE_URL exactly once', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(null, { status: 204 }))

    await apiClient('mcp-server/tokens')

    expect(fetchSpy.mock.calls[0][0]).toBe(`${API_BASE_URL}mcp-server/tokens`)
    expect(fetchSpy.mock.calls[0][0]).not.toContain('/api/v1//api/v1/')
  })
})
