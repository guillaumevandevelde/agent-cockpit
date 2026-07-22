import { API_BASE_URL } from './constants'

const TOKEN_STORAGE_KEY = 'claude-cockpit-api-token'

function getApiToken(): string | undefined {
  if (typeof window === 'undefined') return undefined
  return window.sessionStorage.getItem(TOKEN_STORAGE_KEY) || undefined
}

export function apiHeaders(headers?: HeadersInit): Headers {
  const result = new Headers(headers)
  if (!result.has('Content-Type')) result.set('Content-Type', 'application/json')
  const token = getApiToken()
  if (token) result.set('Authorization', `Bearer ${token}`)
  return result
}

export function apiTokenQuery(): string {
  const token = getApiToken()
  return token ? `api_token=${encodeURIComponent(token)}` : ''
}

export async function apiFetch(url: string, options?: RequestInit): Promise<Response> {
  const tokenBeforeRequest = getApiToken()
  let response = await fetch(url, { ...options, headers: apiHeaders(options?.headers) })
  if (response.status !== 401 || typeof window === 'undefined') return response

  if (!tokenBeforeRequest && getApiToken()) {
    return fetch(url, { ...options, headers: apiHeaders(options?.headers) })
  }

  const token = window.prompt('Agent Cockpit API token')
  if (!token) return response

  window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
  response = await fetch(url, { ...options, headers: apiHeaders(options?.headers) })
  return response
}

/**
 * Build an API endpoint URL with optional query parameters.
 * Filters out undefined values automatically.
 */
export function buildEndpoint(
  endpoint: string,
  params?: Record<string, string | number | boolean | undefined>
): string {
  if (!params) return endpoint;

  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) {
      searchParams.append(key, String(value));
    }
  });

  const queryString = searchParams.toString();
  return queryString ? `${endpoint}?${queryString}` : endpoint;
}

export interface ApiError {
  message?: string
  detail?: string | { msg?: string } | Array<{ msg?: string }>
}

function apiErrorMessage(error: ApiError, fallback = 'An error occurred'): string {
  if (error.message) return error.message
  if (typeof error.detail === 'string') return error.detail
  if (Array.isArray(error.detail)) {
    const messages = error.detail.map((item) => item.msg).filter(Boolean)
    if (messages.length > 0) return messages.join(', ')
  }
  if (error.detail && typeof error.detail === 'object' && 'msg' in error.detail && error.detail.msg) {
    return error.detail.msg
  }
  return fallback
}

export class ApiClient {
  private async request<T>(
    endpoint: string,
    options?: RequestInit
  ): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`

    try {
      const response = await apiFetch(url, options)

      if (!response.ok) {
        const error: ApiError = await response.json().catch(() => ({
          message: `HTTP ${response.status}: ${response.statusText}`,
        }))
        throw new Error(apiErrorMessage(error))
      }

      return response.json()
    } catch (error) {
      if (error instanceof Error) {
        throw error
      }
      throw new Error('An unknown error occurred', { cause: error })
    }
  }

  async get<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, { method: 'GET' })
  }

  async post<T>(endpoint: string, data?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async put<T>(endpoint: string, data?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  }

  async delete<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, { method: 'DELETE' })
  }
}

export const api = new ApiClient()

/**
 * POST a multipart/form-data body (e.g. a file upload). Unlike apiClient this
 * never sets Content-Type — the browser adds it with the multipart boundary —
 * while still carrying the API token when one is set.
 */
export async function apiUpload<T>(endpoint: string, formData: FormData): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`
  const headers = new Headers()
  const token = getApiToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(url, { method: 'POST', body: formData, headers })
  if (!response.ok) {
    const error: ApiError = await response.json().catch(() => ({
      message: `HTTP ${response.status}: ${response.statusText}`,
    }))
    throw new Error(apiErrorMessage(error))
  }
  return response.json()
}

/** Absolute URL for a GET endpoint, with the API token appended when set —
 * usable directly as an `<img src>` where the fetch-based auth retry can't run. */
export function apiAssetUrl(endpoint: string): string {
  const query = apiTokenQuery()
  return `${API_BASE_URL}${endpoint}${query ? `?${query}` : ''}`
}

// Helper function for simpler API calls
export async function apiClient<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`

  try {
    const response = await apiFetch(url, options)

    // Handle 204 No Content responses
    if (response.status === 204) {
      return {} as T
    }

    if (!response.ok) {
      const error: ApiError = await response.json().catch(() => ({
        message: `HTTP ${response.status}: ${response.statusText}`,
      }))
      throw new Error(apiErrorMessage(error))
    }

    return response.json()
  } catch (error) {
    if (error instanceof Error) {
      throw error
    }
    throw new Error('An unknown error occurred', { cause: error })
  }
}
