import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, SelectField } from './CodexFormFields'
import { REASONING_EFFORT_OPTIONS, SETTING_HELP, formatKnownValues } from './codexSettingsHelpers'

interface GeneralCardProps {
  model: string
  onModelChange: (value: string) => void
  reasoning: string
  onReasoningChange: (value: string) => void
  profile: string
  onProfileChange: (value: string) => void
  knownModels: string[]
  knownProfiles: string[]
}

export function GeneralCard({
  model,
  onModelChange,
  reasoning,
  onReasoningChange,
  profile,
  onProfileChange,
  knownModels,
  knownProfiles,
}: GeneralCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>General</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Field
          id="codex-model"
          label="Model"
          value={model}
          placeholder="default"
          onChange={onModelChange}
          help={SETTING_HELP.model}
          description={`${formatKnownValues(knownModels, 'No model ids detected in this config.')} You can enter any Codex-supported model id.`}
        />
        <SelectField
          id="codex-reasoning"
          label="Reasoning Effort"
          value={reasoning}
          options={REASONING_EFFORT_OPTIONS}
          onChange={onReasoningChange}
          help={SETTING_HELP.reasoning}
        />
        <Field
          id="codex-profile"
          label="Profile"
          value={profile}
          placeholder="default"
          onChange={onProfileChange}
          help={SETTING_HELP.profile}
          description={`${formatKnownValues(knownProfiles, 'No named profiles detected in this config.')} You can enter any Codex profile name.`}
        />
      </CardContent>
    </Card>
  )
}
