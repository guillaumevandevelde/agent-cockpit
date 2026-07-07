import { describe, expect, it } from 'vitest'

import { isNativeCopyShortcut } from './terminalCopyShortcut'

const baseEvent = {
  type: 'keydown',
  key: 'c',
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
}

describe('isNativeCopyShortcut', () => {
  it('is true for Ctrl+C with an active selection', () => {
    expect(isNativeCopyShortcut({ ...baseEvent, ctrlKey: true }, true)).toBe(true)
  })

  it('is true for Cmd+C (metaKey) with an active selection', () => {
    expect(isNativeCopyShortcut({ ...baseEvent, metaKey: true }, true)).toBe(true)
  })

  it('is false without an active selection, so Ctrl+C still sends SIGINT', () => {
    expect(isNativeCopyShortcut({ ...baseEvent, ctrlKey: true }, false)).toBe(false)
  })

  it('is false when neither ctrl nor meta is held', () => {
    expect(isNativeCopyShortcut(baseEvent, true)).toBe(false)
  })

  it('is false for other ctrl chords like Ctrl+Shift+C', () => {
    expect(isNativeCopyShortcut({ ...baseEvent, ctrlKey: true, shiftKey: true }, true)).toBe(false)
  })

  it('is false for unrelated keys', () => {
    expect(isNativeCopyShortcut({ ...baseEvent, ctrlKey: true, key: 'v' }, true)).toBe(false)
  })

  it('is false for keyup events', () => {
    expect(isNativeCopyShortcut({ ...baseEvent, ctrlKey: true, type: 'keyup' }, true)).toBe(false)
  })

  it('is case-insensitive on the key value', () => {
    expect(isNativeCopyShortcut({ ...baseEvent, ctrlKey: true, key: 'C' }, true)).toBe(true)
  })
})
