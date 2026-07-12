import { apiClient } from '@/lib/api'
import type {
  Blueprint,
  BlueprintApplyRequest,
  BlueprintApplyResponse,
  BlueprintCreate,
  BlueprintListResponse,
  BlueprintUpdate,
} from './types'

const BASE = 'blueprints'

export async function listBlueprints(): Promise<Blueprint[]> {
  const res = await apiClient<BlueprintListResponse>(BASE)
  return res.blueprints
}

export async function getBlueprint(name: string): Promise<Blueprint> {
  return apiClient<Blueprint>(`${BASE}/${encodeURIComponent(name)}`)
}

export async function createBlueprint(data: BlueprintCreate): Promise<Blueprint> {
  return apiClient<Blueprint>(BASE, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateBlueprint(
  name: string,
  data: BlueprintUpdate,
): Promise<Blueprint> {
  return apiClient<Blueprint>(`${BASE}/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteBlueprint(name: string): Promise<void> {
  await apiClient<unknown>(`${BASE}/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}

export async function applyBlueprint(
  name: string,
  payload: BlueprintApplyRequest,
): Promise<BlueprintApplyResponse> {
  return apiClient<BlueprintApplyResponse>(
    `${BASE}/${encodeURIComponent(name)}/apply`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}
