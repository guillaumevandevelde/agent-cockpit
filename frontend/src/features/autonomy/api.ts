import { apiClient, buildEndpoint } from '@/lib/api'
import type { AutonomyProfile, ActiveAutonomy, AutonomyMode } from '@/types/autonomy'

export const autonomyApi = {
  listProfiles: () =>
    apiClient<AutonomyProfile[]>(buildEndpoint('autonomy/profiles')),

  getProfile: (id: number) =>
    apiClient<AutonomyProfile>(buildEndpoint(`autonomy/profiles/${id}`)),

  createProfile: (data: { name: string; mode: AutonomyMode; description?: string; is_default?: boolean }) =>
    apiClient<AutonomyProfile>(buildEndpoint('autonomy/profiles'), {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateProfile: (id: number, data: Partial<{ name: string; mode: AutonomyMode; description: string; is_default: boolean }>) =>
    apiClient<AutonomyProfile>(buildEndpoint(`autonomy/profiles/${id}`), {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  deleteProfile: (id: number) =>
    apiClient<{ deleted: boolean }>(buildEndpoint(`autonomy/profiles/${id}`), {
      method: 'DELETE',
    }),

  getActive: () =>
    apiClient<ActiveAutonomy>(buildEndpoint('autonomy/active')),

  setActive: (mode: AutonomyMode) =>
    apiClient<{ mode: AutonomyMode }>(buildEndpoint('autonomy/active'), {
      method: 'PUT',
      body: JSON.stringify({ mode }),
    }),
}
