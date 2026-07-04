import type { ReactNode } from 'react'
import { HelpCircle, RotateCcw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { CodexFeatureInventoryRow } from '@/types/providers'
import { DEFAULT_SELECT_VALUE, withCurrentOption } from './codexSettingsHelpers'

export function HelpIcon({ text }: { text: string }) {
  return (
    <span
      className="inline-flex cursor-help text-muted-foreground"
      title={text}
      aria-label={text}
      tabIndex={0}
    >
      <HelpCircle className="h-3.5 w-3.5" />
    </span>
  )
}

export function LabelWithHelp({
  htmlFor,
  children,
  help,
  className,
}: {
  htmlFor?: string
  children: ReactNode
  help?: string
  className?: string
}) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <Label htmlFor={htmlFor} className={className}>
        {children}
      </Label>
      {help && <HelpIcon text={help} />}
    </div>
  )
}

export function Field({
  id,
  label,
  value,
  placeholder,
  onChange,
  description,
  help,
}: {
  id: string
  label: string
  value: string
  placeholder?: string
  onChange: (value: string) => void
  description?: string
  help?: string
}) {
  return (
    <div className="space-y-1.5">
      <LabelWithHelp htmlFor={id} help={help}>
        {label}
      </LabelWithHelp>
      <Input
        id={id}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  )
}

export function SelectField({
  id,
  label,
  value,
  options,
  onChange,
  help,
}: {
  id: string
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
  help?: string
}) {
  return (
    <div className="space-y-1.5">
      <LabelWithHelp htmlFor={id} help={help}>
        {label}
      </LabelWithHelp>
      <Select
        value={value || DEFAULT_SELECT_VALUE}
        onValueChange={(nextValue) => onChange(nextValue === DEFAULT_SELECT_VALUE ? '' : nextValue)}
      >
        <SelectTrigger id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={DEFAULT_SELECT_VALUE}>Default</SelectItem>
          {withCurrentOption(options, value).map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

export function ToggleRow({
  id,
  label,
  checked,
  onChange,
  trailing,
  help,
}: {
  id: string
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  trailing?: ReactNode
  help?: string
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
      <LabelWithHelp htmlFor={id} help={help} className="text-sm font-medium">
        {label}
      </LabelWithHelp>
      <div className="flex items-center gap-2">
        {trailing}
        <Switch id={id} checked={checked} onCheckedChange={onChange} />
      </div>
    </div>
  )
}

export function FeatureToggleRow({
  feature,
  checked,
  explicit,
  onChange,
  onReset,
  help,
}: {
  feature: CodexFeatureInventoryRow
  checked: boolean
  explicit: boolean
  onChange: (checked: boolean) => void
  onReset: () => void
  help: string
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
      <div className="min-w-0">
        <LabelWithHelp htmlFor={`codex-feature-${feature.name}`} help={help} className="block truncate text-sm font-medium">
          {feature.name}
        </LabelWithHelp>
        <div className="mt-1 flex flex-wrap gap-1.5">
          <Badge variant="outline" className="text-xs">
            {feature.stage || 'unknown'}
          </Badge>
          {explicit && (
            <Badge variant="secondary" className="text-xs">
              configured
            </Badge>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {explicit && (
          <Button type="button" variant="ghost" size="icon" onClick={onReset} title="Use Codex default">
            <RotateCcw className="h-4 w-4" />
          </Button>
        )}
        <Switch id={`codex-feature-${feature.name}`} checked={checked} onCheckedChange={onChange} />
      </div>
    </div>
  )
}
