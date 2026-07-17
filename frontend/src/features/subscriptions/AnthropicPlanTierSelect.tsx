import { useEffect, useState } from 'react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { fetchAnthropicPlanTierOptions, fetchAnthropicPlanTier, setAnthropicPlanTier } from './api'
import { CUSTOM_PLAN_TIER, type AnthropicPlanTierOption } from './types'

export function AnthropicPlanTierSelect({ onChange }: { onChange: () => void }) {
  const [options, setOptions] = useState<AnthropicPlanTierOption[]>([])
  const [tier, setTier] = useState<string | null>(null)
  const [customInput, setCustomInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchAnthropicPlanTierOptions(), fetchAnthropicPlanTier()])
      .then(([opts, current]) => {
        if (cancelled) return
        setOptions(opts.tiers)
        setTier(current.tier)
        if (current.custom_limit_tokens != null) setCustomInput(String(current.custom_limit_tokens))
      })
      .catch(() => {
        // Honest empty state: the row above already shows "no signal".
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleTierChange(value: string) {
    setTier(value)
    setError(null)
    if (value === CUSTOM_PLAN_TIER) return // wait for the number input + Save
    setSaving(true)
    try {
      await setAnthropicPlanTier(value, null)
      onChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save plan tier')
    } finally {
      setSaving(false)
    }
  }

  async function handleCustomSave() {
    const value = Number(customInput)
    if (!Number.isFinite(value) || value <= 0) {
      setError('Enter a positive token budget')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await setAnthropicPlanTier(CUSTOM_PLAN_TIER, value)
      onChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save plan tier')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mb-3 pl-1 space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={tier ?? undefined} onValueChange={handleTierChange} disabled={saving}>
          <SelectTrigger className="h-8 w-48 text-xs">
            <SelectValue placeholder="Select plan tier" />
          </SelectTrigger>
          <SelectContent>
            {options.map((opt) => (
              <SelectItem key={opt.key} value={opt.key}>
                {opt.label}
              </SelectItem>
            ))}
            <SelectItem value={CUSTOM_PLAN_TIER}>Custom</SelectItem>
          </SelectContent>
        </Select>
        {tier === CUSTOM_PLAN_TIER && (
          <>
            <Input
              type="number"
              min={1}
              placeholder="tokens per 5h"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              className="h-8 w-36 text-xs"
            />
            <Button size="sm" className="h-8" onClick={handleCustomSave} disabled={saving}>
              Save
            </Button>
          </>
        )}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <p className="text-xs text-muted-foreground">
        Tier token budgets are community estimates, not published by Anthropic — verify before trusting the
        percentage, or pick Custom and enter your own number.
      </p>
    </div>
  )
}
