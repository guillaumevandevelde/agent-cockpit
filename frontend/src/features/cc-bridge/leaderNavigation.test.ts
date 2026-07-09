import { describe, expect, it } from 'vitest'

import { resolveLeaderNavigationTarget } from './leaderNavigation'

const targets = ['a', 'b', 'c', 'd']

describe('resolveLeaderNavigationTarget', () => {
  it('returns the next target, wrapping around at the end', () => {
    expect(resolveLeaderNavigationTarget(targets, 'a', 'next')).toBe('b')
    expect(resolveLeaderNavigationTarget(targets, 'd', 'next')).toBe('a')
  })

  it('returns the previous target, wrapping around at the start', () => {
    expect(resolveLeaderNavigationTarget(targets, 'b', 'prev')).toBe('a')
    expect(resolveLeaderNavigationTarget(targets, 'a', 'prev')).toBe('d')
  })

  it('jumps to a numbered pane', () => {
    expect(resolveLeaderNavigationTarget(targets, 'a', 3)).toBe('c')
  })

  it('returns null for an out-of-range numbered jump', () => {
    expect(resolveLeaderNavigationTarget(targets, 'a', 9)).toBeNull()
  })

  it('returns null when the source target is not displayed', () => {
    expect(resolveLeaderNavigationTarget(targets, 'zzz', 'next')).toBeNull()
  })

  it('returns null when there is only the source pane and nowhere to wrap', () => {
    expect(resolveLeaderNavigationTarget(['a'], 'a', 'next')).toBe('a')
  })

  it('returns null for an empty list', () => {
    expect(resolveLeaderNavigationTarget([], 'a', 'next')).toBeNull()
  })
})
