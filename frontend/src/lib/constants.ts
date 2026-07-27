export const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1/'

export const CLICKABLE_CARD = 'cursor-pointer border-2 hover:border-primary/50 transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'

export const MODAL_SIZES = {
  SM: 'max-w-2xl max-h-[80vh] overflow-y-auto',
  MD: 'max-w-3xl max-h-[85vh] overflow-y-auto',
  LG: 'max-w-4xl max-h-[90vh] overflow-y-auto',
  // Wider variant for content-heavy drawers (Kanban CardDrawer): callers that
  // own their body scrollbar still need to override `overflow-y-auto` here,
  // see CardDrawer's `overflow-hidden` + own `flex-1 overflow-auto` body.
  XL: 'max-w-6xl max-h-[90vh] overflow-y-auto',
} as const
