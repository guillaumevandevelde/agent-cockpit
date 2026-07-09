import { describe, expect, it } from 'vitest'

import { isLeaderPrefix, keyHasCommandModifier, shortcutDirection, type LeaderKeyEventLike } from './leaderShortcuts'

const baseEvent: LeaderKeyEventLike = {
  type: 'keydown',
  key: ' ',
  code: 'Space',
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  isComposing: false,
}

describe('isLeaderPrefix', () => {
  it('is true for Ctrl+Space', () => {
    expect(isLeaderPrefix({ ...baseEvent, ctrlKey: true })).toBe(true)
  })

  it('is false without ctrl held', () => {
    expect(isLeaderPrefix(baseEvent)).toBe(false)
  })

  it('is false when Alt is also held', () => {
    expect(isLeaderPrefix({ ...baseEvent, ctrlKey: true, altKey: true })).toBe(false)
  })

  it('is false when Meta is also held', () => {
    expect(isLeaderPrefix({ ...baseEvent, ctrlKey: true, metaKey: true })).toBe(false)
  })

  it('is false when Shift is also held', () => {
    expect(isLeaderPrefix({ ...baseEvent, ctrlKey: true, shiftKey: true })).toBe(false)
  })

  it('is false for an unrelated key', () => {
    expect(isLeaderPrefix({ ...baseEvent, ctrlKey: true, key: 'a', code: 'KeyA' })).toBe(false)
  })
})

describe('keyHasCommandModifier', () => {
  it('is true when ctrl is held', () => {
    expect(keyHasCommandModifier({ ...baseEvent, ctrlKey: true })).toBe(true)
  })

  it('is true when alt is held', () => {
    expect(keyHasCommandModifier({ ...baseEvent, altKey: true })).toBe(true)
  })

  it('is true when meta is held', () => {
    expect(keyHasCommandModifier({ ...baseEvent, metaKey: true })).toBe(true)
  })

  it('is false with no modifier held', () => {
    expect(keyHasCommandModifier(baseEvent)).toBe(false)
  })
})

describe('shortcutDirection', () => {
  it('maps ArrowLeft to prev', () => {
    expect(shortcutDirection({ ...baseEvent, key: 'ArrowLeft' })).toBe('prev')
  })

  it('maps ArrowRight to next', () => {
    expect(shortcutDirection({ ...baseEvent, key: 'ArrowRight' })).toBe('next')
  })

  it('maps digits 1-4 to the numeric pane index', () => {
    expect(shortcutDirection({ ...baseEvent, key: '1' })).toBe(1)
    expect(shortcutDirection({ ...baseEvent, key: '4' })).toBe(4)
  })

  it('is null for digits outside 1-4', () => {
    expect(shortcutDirection({ ...baseEvent, key: '5' })).toBeNull()
    expect(shortcutDirection({ ...baseEvent, key: '0' })).toBeNull()
  })

  it('is null when a command modifier is held', () => {
    expect(shortcutDirection({ ...baseEvent, key: 'ArrowLeft', ctrlKey: true })).toBeNull()
  })

  it('is null for unrelated keys', () => {
    expect(shortcutDirection({ ...baseEvent, key: 'a' })).toBeNull()
  })
})
