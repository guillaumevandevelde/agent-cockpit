import { useEffect, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { fetchAnthropicPlanTier, setAnthropicPlanTier } from './api'
import type { PlanTier } from './types'

const TIER_OPTIONS: { value: PlanTier; label: string }[] = [
  { value: 'pro', label: 'Pro' },
  { value: 'max_5x', label: 'Max 5x' },
  { value: 'max_20x', label: 'Max 20x' },
  { value: 'team', label: 'Team' },
]

interface AnthropicCredentialsCardProps {
  onTierChanged?: () => void
}

export function AnthropicCredentialsCard({ onTierChanged }: AnthropicCredentialsCardProps) {
  const [tier, setTier] = useState<PlanTier | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchAnthropicPlanTier()
      .then((res) => {
        if (!cancelled) {
          setTier(res.tier)
          setLoaded(true)
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleChange(next: string) {
    const newTier = next as PlanTier
    setSaving(true)
    try {
      const res = await setAnthropicPlanTier(newTier)
      setTier(res.tier)
      onTierChanged?.()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card data-testid="anthropic-credentials-card">
      <CardHeader>
        <CardTitle>Anthropic</CardTitle>
        <CardDescription>
          Pick your Anthropic plan so we can show 5h and weekly leftover against its limits.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Select value={tier ?? ''} onValueChange={handleChange} disabled={!loaded || saving}>
          <SelectTrigger data-testid="anthropic-plan-trigger">
            <SelectValue placeholder={loaded ? 'Choose your plan' : 'Loading...'} />
          </SelectTrigger>
          <SelectContent>
            {TIER_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          These limits may have shifted since the last Anthropic plan change — verify at
          anthropic.com before trusting the percentages.
        </p>
      </CardContent>
    </Card>
  )
}
