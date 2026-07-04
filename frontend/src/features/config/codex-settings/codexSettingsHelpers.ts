import type { CodexFeatureInventoryRow } from '@/types/providers'

export const FEATURE_NAME_PATTERN = /^[A-Za-z0-9_-]+$/
export const DEFAULT_SELECT_VALUE = '__default__'
export const REASONING_EFFORT_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra High' },
]
export const SANDBOX_MODE_OPTIONS = [
  { value: 'read-only', label: 'Read Only' },
  { value: 'workspace-write', label: 'Workspace Write' },
  { value: 'danger-full-access', label: 'Danger Full Access' },
]
export const APPROVAL_POLICY_OPTIONS = [
  { value: 'untrusted', label: 'Untrusted' },
  { value: 'on-request', label: 'On Request' },
  { value: 'never', label: 'Never' },
  { value: 'on-failure', label: 'On Failure (deprecated)' },
]
export const SETTING_HELP = {
  model: 'Model the agent should use. This is open-ended; enter any Codex-supported model id.',
  reasoning: 'Controls reasoning depth for models that support it.',
  profile: 'Layers a named Codex profile config on top of the base user config.',
  sandboxMode: 'Sandbox policy for filesystem and network access during command execution.',
  approvalPolicy: 'Controls when Codex requires human approval before executing a command.',
  search: 'Enables live web search. Codex can use the native web search tool without per-call approval.',
  strictConfig: 'Errors out when config.toml contains fields this Codex version does not recognize.',
  noAltScreen: 'Runs the TUI inline instead of in an alternate screen, preserving terminal scrollback history.',
} satisfies Record<string, string>
const FEATURE_HELP: Record<string, string> = {
  apps: 'Enable ChatGPT Apps/connectors support.',
  enable_request_compression: 'Compress streaming request bodies with zstd when supported.',
  fast_mode: 'Enable model-catalog service tier selection in the TUI, including Fast-tier commands when the active model advertises them.',
  goals: 'Enable persistent objectives that keep a Codex thread working toward a defined outcome across turns.',
  hooks: 'Enable lifecycle hooks loaded from hooks.json or inline hooks config.',
  memories: 'Enable Codex Memories.',
  multi_agent: 'Enable multi-agent collaboration tools such as spawn_agent, send_input, resume_agent, wait_agent, and close_agent.',
  network_proxy: 'Enable sandboxed networking. Table-form config can set network policy options such as allowed domains.',
  personality: 'Enable personality selection controls.',
  prevent_idle_sleep: 'Prevent the machine from sleeping while a turn is actively running.',
  shell_snapshot: 'Snapshot the shell environment to speed up repeated commands.',
  shell_tool: 'Enable the default shell tool for running commands.',
  skill_mcp_dependency_install: 'Allow prompting and installing missing MCP dependencies for skills.',
  undo: 'Enable undo support.',
  unified_exec: 'Use the unified PTY-backed exec tool.',
}
const FEATURE_ORDER = [
  'goals',
  'memories',
  'hooks',
  'multi_agent',
  'fast_mode',
  'undo',
  'prevent_idle_sleep',
  'network_proxy',
  'shell_tool',
  'shell_snapshot',
  'personality',
  'unified_exec',
]

export function optionalString(value: string): string | null {
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

export function optionalBoolean(current: boolean, original: boolean | undefined): boolean | null {
  if (original !== undefined || current === true) return current
  return null
}

export function booleanFeatureOverrides(features: Record<string, unknown> | undefined): Record<string, boolean> {
  if (!features) return {}
  return Object.fromEntries(
    Object.entries(features).filter((entry): entry is [string, boolean] => typeof entry[1] === 'boolean'),
  )
}

export function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return Array.from(new Set(values.map((value) => value?.trim()).filter((value): value is string => Boolean(value))))
}

export function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim().length > 0 ? value : undefined
}

export function withCurrentOption(
  options: { value: string; label: string }[],
  current: string,
): { value: string; label: string }[] {
  if (!current || options.some((option) => option.value === current)) return options
  return [...options, { value: current, label: `${current} (custom)` }]
}

export function formatKnownValues(values: string[], emptyMessage: string): string {
  if (values.length === 0) return emptyMessage
  return `Known in this config: ${values.join(', ')}.`
}

export function featureHelp(feature: CodexFeatureInventoryRow): string {
  return FEATURE_HELP[feature.name]
    ?? (
      `No official description found. Codex reports this flag as ${feature.stage || 'unknown stage'} `
      + `and currently ${feature.enabled ? 'enabled' : 'disabled'}.`
    )
}

export function sortFeatures(features: CodexFeatureInventoryRow[]): CodexFeatureInventoryRow[] {
  const rank = new Map(FEATURE_ORDER.map((name, index) => [name, index]))
  return [...features].sort((a, b) => {
    const aRank = rank.get(a.name) ?? Number.MAX_SAFE_INTEGER
    const bRank = rank.get(b.name) ?? Number.MAX_SAFE_INTEGER
    if (aRank !== bRank) return aRank - bRank
    return a.name.localeCompare(b.name)
  })
}

export function isVisibleKnownFeature(feature: CodexFeatureInventoryRow): boolean {
  return !['removed', 'deprecated'].includes(feature.stage)
}
