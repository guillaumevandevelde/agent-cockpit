/**
 * Hook for the read-only "Plans & Specs" overview.
 *
 * Optie B (kanban card 9e33a359, stap 2). Three endpoints:
 *
 *   ``getOverview()``         — the B+C aggregator (kanban card 885d0b61)
 *   ``getDocContent(path)``   — single ``docs/cockpit/*.md`` body
 *   ``getStats()``            — kept for the Dashboard tile; still reads
 *                               ``kanban_plans`` until chore card 528c5ca2
 *                               phases that store out.
 *
 * The legacy CRUD surface (``listPlans`` / ``getPlan`` / ``createPlan`` /
 * ``updatePlan`` / ``deletePlan`` / ``searchPlans``) was deleted with
 * this card: every write method was dead — no component called it. The
 * ``/plans`` ``GET`` is now sourced from ``/plans/overview`` (no
 * separation between B and C at the call site needed), and the ``/plans``
 * detail route is now the ``/plans/overview/docs/{path}`` route.
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

  /**
   * Dashboard tile. Still hits ``/plans/stats`` — that endpoint is
   * ``kanban_plans``-backed and is intentionally left alone here so the
   * chore card 528c5ca2 (phase-out) can land as a separate, focused
   * change. Once that card ships, this signature can drop.
   *
   * Typed locally with an inline shape because ``types/plans.ts`` is now
   * focused exclusively on the read-only overview contract; the legacy
   * kanban_plans schema is on its way out and didn't earn a permanent
   * spot in the shared types file.
   */
  const getStats = useCallback(async () => {
    return apiClient<{
      total_plans: number
      oldest_date: string | null
      newest_date: string | null
      total_size_bytes: number
    }>(
      buildEndpoint('plans/stats', { project_path: activeProject?.path })
    )
  }, [activeProject?.path])

  return {
    getOverview,
    getDocContent,
    getStats,
  }
}