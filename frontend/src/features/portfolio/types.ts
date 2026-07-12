export interface PortfolioTotals {
  backlog: number
  todo: number
  doing: number
  impediment: number
  done_24h: number
}

export interface PortfolioProject {
  id: number | null
  name: string
  kind: string
  project_key: string
  autodispatch_enabled: boolean
  totals: PortfolioTotals
  last_activity: string | null
  last_dispatch: string | null
}

export interface PortfolioOverview {
  projects: PortfolioProject[]
  totals: PortfolioTotals
}
