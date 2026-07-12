// Blueprint types — mirror backend/app/services/blueprint/__init__.py.
// These are the on-the-wire shapes for the /api/v1/blueprints endpoints.

export type SkillSource = 'user' | 'system' | 'project'

export interface BlueprintSkill {
  name: string
  source: SkillSource
  version_pin?: string | null
}

export interface BlueprintAgent {
  name: string
  body_path?: string | null
  model_default?: string | null
  tools: string[]
}

export type PermissionMode = 'default' | 'acceptEdits' | 'bypassPermissions' | 'plan'

export interface BlueprintSettings {
  permission_mode?: PermissionMode | null
  plansDirectory?: string | null
  model?: string | null
}

export interface Blueprint {
  name: string
  version: number
  description?: string | null
  created_at?: string | null
  updated_at?: string | null
  subdirs: string[]
  settings: BlueprintSettings
  skills: BlueprintSkill[]
  agents: BlueprintAgent[]
  statusline?: string | null
  output_style?: string | null
  claudemd?: string | null
}

export interface BlueprintCreate {
  name: string
  description?: string | null
  settings?: BlueprintSettings | null
  skills?: BlueprintSkill[]
  agents?: BlueprintAgent[]
  statusline?: string | null
  output_style?: string | null
  claudemd?: string | null
}

export interface BlueprintUpdate {
  description?: string | null
  settings?: BlueprintSettings | null
  skills?: BlueprintSkill[] | null
  agents?: BlueprintAgent[] | null
  statusline?: string | null
  output_style?: string | null
  claudemd?: string | null
}

export interface BlueprintListResponse {
  blueprints: Blueprint[]
}

export interface BlueprintApplyRequest {
  project_path: string
  force?: boolean
}

export interface BlueprintApplyResponse {
  blueprint_name: string
  project_path: string
  written_files: string[]
  created_dirs: string[]
  applied_skills: string[]
  applied_agents: string[]
  skipped_existing: boolean
}
