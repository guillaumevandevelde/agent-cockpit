import type { LucideIcon } from 'lucide-react'

export interface PaletteItem {
  id: string
  group: string
  title: string
  subtitle?: string
  icon: LucideIcon
  keywords?: string[]
  onSelect: () => void
}
