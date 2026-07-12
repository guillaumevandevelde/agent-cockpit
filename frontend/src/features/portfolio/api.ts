import { apiClient } from '@/lib/api'
import type { PortfolioOverview } from './types'

export async function fetchPortfolioOverview(): Promise<PortfolioOverview> {
  return apiClient<PortfolioOverview>('portfolio/overview')
}
