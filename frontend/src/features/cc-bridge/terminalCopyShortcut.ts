/** Minimal shape of the KeyboardEvent fields we need, so this stays testable without jsdom. */
export interface CopyKeyEventLike {
  type: string
  key: string
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
  altKey: boolean
}

/**
 * xterm.js intercepts every keydown and forwards it to the PTY, calling
 * preventDefault() along the way — including Ctrl+C/Cmd+C. That blocks the
 * browser's native copy even when text is selected. When this returns true,
 * the caller should let the browser handle the event instead of xterm.
 */
export function isNativeCopyShortcut(event: CopyKeyEventLike, hasSelection: boolean): boolean {
  if (event.type !== 'keydown' || !hasSelection) return false
  if (event.shiftKey || event.altKey) return false
  if (!event.ctrlKey && !event.metaKey) return false
  return event.key.toLowerCase() === 'c'
}
