import { Plus, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import type { CodexFeatureInventoryRow } from '@/types/providers'
import { FeatureToggleRow, ToggleRow } from './CodexFormFields'
import { featureHelp } from './codexSettingsHelpers'

interface FeaturesCardProps {
  featureInventoryError: string | null
  newFeature: string
  onNewFeatureChange: (value: string) => void
  onAddFeature: () => void
  canAddFeature: boolean
  knownFeatures: CodexFeatureInventoryRow[]
  features: Record<string, boolean>
  onSetFeature: (name: string, value: boolean) => void
  onResetFeature: (name: string) => void
  unknownFeatureEntries: [string, boolean][]
}

export function FeaturesCard({
  featureInventoryError,
  newFeature,
  onNewFeatureChange,
  onAddFeature,
  canAddFeature,
  knownFeatures,
  features,
  onSetFeature,
  onResetFeature,
  unknownFeatureEntries,
}: FeaturesCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Features</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {featureInventoryError && (
          <p className="rounded-md border border-destructive/50 p-3 text-sm text-destructive">
            {featureInventoryError}
          </p>
        )}
        <div className="flex gap-2">
          <Input
            value={newFeature}
            placeholder="feature_name"
            onChange={(event) => onNewFeatureChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                onAddFeature()
              }
            }}
          />
          <Button type="button" variant="outline" size="icon" onClick={onAddFeature} disabled={!canAddFeature}>
            <Plus className="h-4 w-4" />
          </Button>
        </div>
        <div className="max-h-72 space-y-2 overflow-auto pr-1">
          {knownFeatures.map((feature) => {
            const explicit = feature.name in features
            return (
              <FeatureToggleRow
                key={feature.name}
                feature={feature}
                checked={features[feature.name] ?? feature.enabled}
                explicit={explicit}
                onChange={(checked) => onSetFeature(feature.name, checked)}
                onReset={() => onResetFeature(feature.name)}
                help={featureHelp(feature)}
              />
            )
          })}
          {unknownFeatureEntries.length === 0 && knownFeatures.length === 0 ? (
            <p className="rounded-md border p-3 text-sm text-muted-foreground">No feature flags configured.</p>
          ) : (
            unknownFeatureEntries.map(([name, enabled]) => (
              <ToggleRow
                key={name}
                id={`codex-feature-${name}`}
                label={name}
                checked={enabled}
                onChange={(checked) => onSetFeature(name, checked)}
                help="This feature flag is configured in config.toml but is not present in the active Codex feature inventory."
                trailing={
                  <Button type="button" variant="ghost" size="icon" onClick={() => onResetFeature(name)} title="Use Codex default">
                    <RotateCcw className="h-4 w-4" />
                  </Button>
                }
              />
            ))
          )}
        </div>
      </CardContent>
    </Card>
  )
}
