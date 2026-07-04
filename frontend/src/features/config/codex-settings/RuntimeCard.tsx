import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { SelectField, ToggleRow } from './CodexFormFields'
import { APPROVAL_POLICY_OPTIONS, SANDBOX_MODE_OPTIONS, SETTING_HELP } from './codexSettingsHelpers'

interface RuntimeCardProps {
  sandboxMode: string
  onSandboxModeChange: (value: string) => void
  approvalPolicy: string
  onApprovalPolicyChange: (value: string) => void
  search: boolean
  onSearchChange: (checked: boolean) => void
  strictConfig: boolean
  onStrictConfigChange: (checked: boolean) => void
  noAltScreen: boolean
  onNoAltScreenChange: (checked: boolean) => void
}

export function RuntimeCard({
  sandboxMode,
  onSandboxModeChange,
  approvalPolicy,
  onApprovalPolicyChange,
  search,
  onSearchChange,
  strictConfig,
  onStrictConfigChange,
  noAltScreen,
  onNoAltScreenChange,
}: RuntimeCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Runtime</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <SelectField
          id="codex-sandbox"
          label="Sandbox Mode"
          value={sandboxMode}
          options={SANDBOX_MODE_OPTIONS}
          onChange={onSandboxModeChange}
          help={SETTING_HELP.sandboxMode}
        />
        <SelectField
          id="codex-approval"
          label="Approval Policy"
          value={approvalPolicy}
          options={APPROVAL_POLICY_OPTIONS}
          onChange={onApprovalPolicyChange}
          help={SETTING_HELP.approvalPolicy}
        />
        <ToggleRow
          id="codex-search"
          label="Search"
          checked={search}
          onChange={onSearchChange}
          help={SETTING_HELP.search}
        />
        <ToggleRow
          id="codex-strict-config"
          label="Strict Config"
          checked={strictConfig}
          onChange={onStrictConfigChange}
          help={SETTING_HELP.strictConfig}
        />
        <ToggleRow
          id="codex-no-alt-screen"
          label="No Alt Screen"
          checked={noAltScreen}
          onChange={onNoAltScreenChange}
          help={SETTING_HELP.noAltScreen}
        />
      </CardContent>
    </Card>
  )
}
