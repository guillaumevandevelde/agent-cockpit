/**
 * Hook for the read-only "Plans & Specs" overview.
 *
 * Optie B (kanban card 9e33a359, stap 2). Two endpoints:
 *
 *   ``getOverview()``         — the B+C aggregator (kanban card 885d0b61)
 *   ``getDocContent(path)``   — single ``docs/cockpit/*.md`` body
 *
 * The legacy CRUD surface (``listPlans`` / ``getPlan`` / ``createPlan`` /
 * ``updatePlan`` / ``deletePlan`` / ``searchPlans``) was deleted with
 * kanban card 9e33a359: every write method was dead — no component called
 * it. ``getStats()`` (the ``kanban_plans``-backed ``/plans/stats``
 * Dashboard tile) was removed with kanban card 528c5ca2, which phased out
 * the ``kanban_plans`` table + route entirely; the Dashboard now derives
 * its plan count from ``getOverview()`` instead (see
 * ``DashboardContext.tsx``). The ``/plans`` ``GET`` is now sourced from
 * ``/plans/overview`` (no separation between B and C at the call site
 * needed), and the ``/plans`` detail route is now the
 * ``/plans/overview/docs/{path}`` route.
 */
import { useCallback } from 'react'
import { apiClient, buildEndpoint } from '@/lib/api'
import { useProjectContext } from '@/contexts/ProjectContext'
import type {
  DocContentResponse,
  PlansOverviewResponse,
} from '@/types/plans'

export function usePlansApi() {
  const { activeProject } = useProjectContext()

  const getOverview = useCallback(async (): Promise<PlansOverviewResponse> => {
    return apiClient<PlansOverviewResponse>(
      buildEndpoint('plans/overview', { project_path: activeProject?.path })
    )
  }, [activeProject?.path])

  const getDocContent = useCallback(async (relPath: string): Promise<DocContentResponse> => {
    return apiClient<DocContentResponse>(
      `plans/overview/docs/${relPath}`
    )
  }, [])

  return {
    getOverview,
    getDocContent,
  }
}