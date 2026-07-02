// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor, act, cleanup } from '@testing-library/react'

import { useFetchData } from './useFetchData'

afterEach(() => cleanup())

describe('useFetchData', () => {
  it('starts in a loading state', () => {
    const { result } = renderHook(() =>
      useFetchData(() => new Promise<number>(() => {}), [])
    )

    expect(result.current.loading).toBe(true)
    expect(result.current.data).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('resolves data and clears loading on success', async () => {
    const { result } = renderHook(() =>
      useFetchData(() => Promise.resolve({ items: [1, 2, 3] }), [])
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual({ items: [1, 2, 3] })
    expect(result.current.error).toBeNull()
  })

  it('captures the message of a rejected fetcher', async () => {
    const { result } = renderHook(() =>
      useFetchData(() => Promise.reject(new Error('boom')), [])
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('boom')
    expect(result.current.data).toBeNull()
  })

  it('falls back to a generic message for non-Error rejections', async () => {
    const { result } = renderHook(() =>
      useFetchData(() => Promise.reject('nope'), [])
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('An unknown error occurred')
  })

  it('re-runs the fetcher when refresh is called', async () => {
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce('first')
      .mockResolvedValueOnce('second')

    const { result } = renderHook(() => useFetchData(fetcher, []))

    await waitFor(() => expect(result.current.data).toBe('first'))

    await act(async () => {
      result.current.refresh()
    })

    await waitFor(() => expect(result.current.data).toBe('second'))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('re-runs the fetcher when deps change', async () => {
    const fetcher = vi.fn((id: number) => Promise.resolve(`item-${id}`))

    const { result, rerender } = renderHook(
      ({ id }) => useFetchData(() => fetcher(id), [id]),
      { initialProps: { id: 1 } }
    )

    await waitFor(() => expect(result.current.data).toBe('item-1'))

    rerender({ id: 2 })

    await waitFor(() => expect(result.current.data).toBe('item-2'))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('invokes onError with the message when the fetcher rejects', async () => {
    const onError = vi.fn()
    const { result } = renderHook(() =>
      useFetchData(() => Promise.reject(new Error('boom')), [], onError)
    )

    await waitFor(() => expect(result.current.error).toBe('boom'))
    expect(onError).toHaveBeenCalledWith('boom')
  })

  it('does not invoke onError on a successful fetch', async () => {
    const onError = vi.fn()
    const { result } = renderHook(() =>
      useFetchData(() => Promise.resolve('ok'), [], onError)
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(onError).not.toHaveBeenCalled()
  })

  it('clears a previous error on a successful refresh', async () => {
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce('ok')

    const { result } = renderHook(() => useFetchData(fetcher, []))

    await waitFor(() => expect(result.current.error).toBe('boom'))

    await act(async () => {
      result.current.refresh()
    })

    await waitFor(() => expect(result.current.error).toBeNull())
    expect(result.current.data).toBe('ok')
  })
})
