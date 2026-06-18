import { apiClient, buildEndpoint } from '@/lib/api'
import type { AutomationTemplate } from '@/types/automation-templates'

export const automationTemplatesApi = {
  list: () =>
    apiClient<AutomationTemplate[]>(buildEndpoint('automation-templates')),

  get: (id: number) =>
    apiClient<AutomationTemplate>(buildEndpoint(`automation-templates/${id}`)),

  create: (data: { name: string; description?: string; category?: string; trigger_type?: string; cron_expr?: string; message_template: string; target_projects?: string[]; permission_mode?: string; tags?: string[] }) =>
    apiClient<AutomationTemplate>(buildEndpoint('automation-templates'), {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (id: number, data: Partial<{ name: string; description: string; enabled: boolean; cron_expr: string; message_template: string }>) =>
    apiClient<AutomationTemplate>(buildEndpoint(`automation-templates/${id}`), {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  delete: (id: number) =>
    apiClient<{ deleted: boolean }>(buildEndpoint(`automation-templates/${id}`), {
      method: 'DELETE',
    }),

  seed: () =>
    apiClient<{ seeded: number; total: number }>(buildEndpoint('automation-templates/seed'), {
      method: 'POST',
    }),
}
