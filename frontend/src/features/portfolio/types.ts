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
  // Surfaced from the backend PortfolioService: True when stale_detection has
  // flagged the project (Backlog sits past the threshold). stale_since is the
  // ISO timestamp of the most recent flag — useful for "flagged Nm ago" UI.
  stale: boolean
  stale_since: string | null
}

export interface PortfolioOverview {
  projects: PortfolioProject[]
  totals: PortfolioTotals
}
