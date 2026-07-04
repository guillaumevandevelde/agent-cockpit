import { useMemo, useState } from 'react'
import { Save } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { updateCodexConfig } from '@/hooks/useProviders'
import type {
  CodexConfigResponse,
  CodexConfigUpdateRequest,
  CodexFeatureInventoryResponse,
} from '@/types/providers'
import { FeaturesCard } from './codex-settings/FeaturesCard'
import { GeneralCard } from './codex-settings/GeneralCard'
import { RuntimeCard } from './codex-settings/RuntimeCard'
import {
  FEATURE_NAME_PATTERN,
  booleanFeatureOverrides,
  isVisibleKnownFeature,
  optionalBoolean,
  optionalString,
  sortFeatures,
  stringValue,
  uniqueStrings,
} from './codex-settings/codexSettingsHelpers'

interface CodexSettingsEditorProps {
  config: CodexConfigResponse | null
  featureInventory: CodexFeatureInventoryResponse | null
  featureInventoryError: string | null
  onSaved: () => Promise<void> | void
}

export function CodexSettingsEditor({
  config,
  featureInventory,
  featureInventoryError,
  onSaved,
}: CodexSettingsEditorProps) {
  const summary = config?.summary
  const initialFeatures = useMemo(() => booleanFeatureOverrides(summary?.features), [summary?.features])
  const knownFeatures = useMemo(
    () => sortFeatures((featureInventory?.features ?? []).filter(isVisibleKnownFeature)),
    [featureInventory?.features],
  )
  const knownFeatureNames = useMemo(
    () => new Set(knownFeatures.map((feature) => feature.name)),
    [knownFeatures],
  )
  const [model, setModel] = useState(summary?.model ?? '')
  const [reasoning, setReasoning] = useState(summary?.model_reasoning_effort ?? '')
  const [profile, setProfile] = useState(summary?.profile ?? '')
  const [sandboxMode, setSandboxMode] = useState(summary?.sandbox_mode ?? '')
  const [approvalPolicy, setApprovalPolicy] = useState(summary?.approval_policy ?? '')
  const [search, setSearch] = useState(summary?.search ?? false)
  const [strictConfig, setStrictConfig] = useState(summary?.strict_config ?? false)
  const [noAltScreen, setNoAltScreen] = useState(summary?.no_alt_screen ?? false)
  const [features, setFeatures] = useState<Record<string, boolean>>(initialFeatures)
  const [deletedFeatures, setDeletedFeatures] = useState<Set<string>>(() => new Set())
  const [newFeature, setNewFeature] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const knownModels = uniqueStrings([
    summary?.model,
    stringValue(config?.profile_resolution?.base_summary.model),
    stringValue(config?.profile_resolution?.effective_summary.model),
    ...(config?.profile_resolution?.profiles ?? []).map((profile) => stringValue(profile.summary.model)),
  ])
  const knownProfiles = uniqueStrings([
    summary?.profile,
    summary?.profile_v2,
    ...Object.keys(summary?.profiles ?? {}),
    ...(config?.profile_resolution?.profiles ?? []).map((profile) => profile.name),
  ])
  const featureEntries = Object.entries(features).sort(([a], [b]) => a.localeCompare(b))
  const unknownFeatureEntries = featureEntries.filter(([name]) => !knownFeatureNames.has(name))
  const canAddFeature = FEATURE_NAME_PATTERN.test(newFeature.trim()) && !(newFeature.trim() in features)

  function setFeature(name: string, value: boolean) {
    setFeatures((current) => ({ ...current, [name]: value }))
    setDeletedFeatures((current) => {
      if (!current.has(name)) return current
      const next = new Set(current)
      next.delete(name)
      return next
    })
  }

  function resetFeature(name: string) {
    setFeatures((current) => {
      const next = { ...current }
      delete next[name]
      return next
    })
    setDeletedFeatures((current) => {
      if (!(name in initialFeatures)) return current
      const next = new Set(current)
      next.add(name)
      return next
    })
  }

  function addFeature() {
    const name = newFeature.trim()
    if (!FEATURE_NAME_PATTERN.test(name)) {
      toast.error('Feature names can only contain letters, numbers, underscores, and hyphens')
      return
    }
    if (name in features) {
      toast.error(`Feature "${name}" already exists`)
      return
    }
    setFeatures((current) => ({ ...current, [name]: true }))
    setDeletedFeatures((current) => {
      if (!current.has(name)) return current
      const next = new Set(current)
      next.delete(name)
      return next
    })
    setNewFeature('')
  }

  async function handleSave() {
    setSubmitting(true)
    try {
      const featureUpdates: Record<string, boolean | null> = { ...features }
      deletedFeatures.forEach((name) => {
        featureUpdates[name] = null
      })
      const request: CodexConfigUpdateRequest = {
        settings: {
          model: optionalString(model),
          model_reasoning_effort: optionalString(reasoning),
          profile: optionalString(profile),
          sandbox_mode: optionalString(sandboxMode),
          approval_policy: optionalString(approvalPolicy),
          search: optionalBoolean(search, summary?.search),
          strict_config: optionalBoolean(strictConfig, summary?.strict_config),
          no_alt_screen: optionalBoolean(noAltScreen, summary?.no_alt_screen),
        },
        features: featureUpdates,
      }
      await updateCodexConfig(request)
      toast.success('Codex config saved')
      await onSaved()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save Codex config')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-4">
      {config?.parse_error && (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">{config.parse_error}</p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        <GeneralCard
          model={model}
          onModelChange={setModel}
          reasoning={reasoning}
          onReasoningChange={setReasoning}
          profile={profile}
          onProfileChange={setProfile}
          knownModels={knownModels}
          knownProfiles={knownProfiles}
        />

        <RuntimeCard
          sandboxMode={sandboxMode}
          onSandboxModeChange={setSandboxMode}
          approvalPolicy={approvalPolicy}
          onApprovalPolicyChange={setApprovalPolicy}
          search={search}
          onSearchChange={setSearch}
          strictConfig={strictConfig}
          onStrictConfigChange={setStrictConfig}
          noAltScreen={noAltScreen}
          onNoAltScreenChange={setNoAltScreen}
        />

        <FeaturesCard
          featureInventoryError={featureInventoryError}
          newFeature={newFeature}
          onNewFeatureChange={setNewFeature}
          onAddFeature={addFeature}
          canAddFeature={canAddFeature}
          knownFeatures={knownFeatures}
          features={features}
          onSetFeature={setFeature}
          onResetFeature={resetFeature}
          unknownFeatureEntries={unknownFeatureEntries}
        />
      </div>

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={submitting || Boolean(config?.parse_error)} className="gap-2">
          <Save className="h-4 w-4" />
          {submitting ? 'Saving...' : 'Save Codex Config'}
        </Button>
      </div>
    </div>
  )
}
