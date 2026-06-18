export type TemplateTriggerType = 'cron' | 'once' | 'event'
export type TemplateCategory = 'review' | 'monitor' | 'quality' | 'deploy' | 'custom' | 'general'

export interface AutomationTemplate {
  id: number
  name: string
  description: string | null
  category: TemplateCategory
  icon: string | null
  trigger_type: TemplateTriggerType
  cron_expr: string | null
  message_template: string
  target_projects: string[] | null
  permission_mode: string
  enabled: boolean
  is_builtin: boolean
  tags: string[] | null
  created_at: string
  updated_at: string
}

export const TEMPLATE_CATEGORIES: Record<TemplateCategory, { label: string; color: string }> = {
  review: { label: 'Review', color: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300' },
  monitor: { label: 'Monitor', color: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300' },
  quality: { label: 'Quality', color: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300' },
  deploy: { label: 'Deploy', color: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300' },
  custom: { label: 'Custom', color: 'bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-300' },
  general: { label: 'General', color: 'bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-300' },
}
