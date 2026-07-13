import { apiClient, buildEndpoint } from '@/lib/api'
import type {
  SecurityProfile,
  SecurityProfileDeleteResponse,
  SecurityProfilePatch,
  SecurityProfilePayload,
} from './types'

const BASE = 'security/profiles'

function endpoint(projectPath: string): string {
  return buildEndpoint(BASE, { project_path: projectPath })
}

export async function getSecurityProfile(
  projectPath: string
): Promise<SecurityProfile> {
  return apiClient<SecurityProfile>(endpoint(projectPath))
}

export async function putSecurityProfile(
  projectPath: string,
  payload: SecurityProfilePayload
): Promise<SecurityProfile> {
  return apiClient<SecurityProfile>(endpoint(projectPath), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function patchSecurityProfile(
  projectPath: string,
  payload: SecurityProfilePatch
): Promise<SecurityProfile> {
  return apiClient<SecurityProfile>(endpoint(projectPath), {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteSecurityProfile(
  projectPath: string
): Promise<SecurityProfileDeleteResponse> {
  return apiClient<SecurityProfileDeleteResponse>(endpoint(projectPath), {
    method: 'DELETE',
  })
}
