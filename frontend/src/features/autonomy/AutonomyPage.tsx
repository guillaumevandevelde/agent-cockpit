import { useState } from 'react'
import { useAutonomy } from '@/contexts/AutonomyContext'
import { AUTONOMY_MODES, type AutonomyMode } from '@/types/autonomy'
import { autonomyApi } from '@/features/autonomy/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Shield, Eye, Zap, Plus, Trash2, Star, Pencil } from 'lucide-react'
import { CLICKABLE_CARD, MODAL_SIZES } from '@/lib/constants'

const MODE_ICONS: Record<AutonomyMode, typeof Shield> = {
  plan: Eye,
  suggest: Shield,
  auto: Zap,
}

export function AutonomyPage() {
  const { active, profiles, setActiveMode, refresh } = useAutonomy()
  const [showCreate, setShowCreate] = useState(false)
  const [editingProfile, setEditingProfile] = useState<number | null>(null)
  const [formName, setFormName] = useState('')
  const [formMode, setFormMode] = useState<AutonomyMode>('suggest')
  const [formDescription, setFormDescription] = useState('')
  const [saving, setSaving] = useState(false)

  const currentMode = active?.mode ?? 'suggest'

  const handleCreate = async () => {
    if (!formName.trim()) return
    setSaving(true)
    try {
      await autonomyApi.createProfile({
        name: formName.trim(),
        mode: formMode,
        description: formDescription.trim() || undefined,
      })
      await refresh()
      setShowCreate(false)
      resetForm()
    } catch {
      // error handled by context
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    await autonomyApi.deleteProfile(id)
    await refresh()
  }

  const handleSetDefault = async (id: number) => {
    await autonomyApi.updateProfile(id, { is_default: true })
    await refresh()
  }

  const resetForm = () => {
    setFormName('')
    setFormMode('suggest')
    setFormDescription('')
    setEditingProfile(null)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Shield className="h-8 w-8" />
            Agent Autonomy
          </h1>
          <p className="text-muted-foreground">
            Control how much freedom your agents have to take actions
          </p>
        </div>
      </div>

      {/* Active Mode Selector */}
      <Card>
        <CardHeader>
          <CardTitle>Current Mode</CardTitle>
          <CardDescription>
            Choose the default autonomy level for new agent sessions
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-3">
            {(Object.entries(AUTONOMY_MODES) as [AutonomyMode, typeof AUTONOMY_MODES[AutonomyMode]][]).map(([mode, config]) => {
              const Icon = MODE_ICONS[mode]
              const isActive = currentMode === mode
              return (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setActiveMode(mode)}
                  className={`rounded-lg border-2 p-4 text-left transition-all ${
                    isActive
                      ? 'border-primary bg-primary/5 shadow-sm'
                      : 'border-border hover:border-primary/30 hover:bg-accent'
                  }`}
                >
                  <div className="flex items-center gap-3 mb-2">
                    <div className={`rounded-md p-2 ${config.color}`}>
                      <Icon className="h-5 w-5" />
                    </div>
                    <div>
                      <div className="font-semibold">{config.label}</div>
                      {isActive && <Badge variant="default" className="text-xs">Active</Badge>}
                    </div>
                  </div>
                  <p className="text-sm text-muted-foreground">{config.description}</p>
                </button>
              )
            })}
          </div>
        </CardContent>
      </Card>

      {/* Profiles */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Saved Profiles</CardTitle>
              <CardDescription>
                Reusable autonomy configurations with custom tool restrictions
              </CardDescription>
            </div>
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus className="h-4 w-4 mr-1" />
              New Profile
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {profiles.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              No saved profiles yet. Create one to define custom autonomy rules.
            </p>
          ) : (
            <div className="space-y-3">
              {profiles.map((profile) => {
                const config = AUTONOMY_MODES[profile.mode]
                const Icon = MODE_ICONS[profile.mode]
                return (
                  <div
                    key={profile.id}
                    className={CLICKABLE_CARD + ' flex items-center justify-between rounded-lg border p-4'}
                  >
                    <div className="flex items-center gap-3">
                      <div className={`rounded-md p-2 ${config.color}`}>
                        <Icon className="h-4 w-4" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{profile.name}</span>
                          {profile.is_default && (
                            <Badge variant="secondary" className="text-xs">
                              <Star className="h-3 w-3 mr-1" />
                              Default
                            </Badge>
                          )}
                          <Badge variant="outline" className="text-xs">{config.label}</Badge>
                        </div>
                        {profile.description && (
                          <p className="text-sm text-muted-foreground">{profile.description}</p>
                        )}
                        {(profile.allowed_tools || profile.denied_tools) && (
                          <div className="flex gap-2 mt-1">
                            {profile.allowed_tools && (
                              <span className="text-xs text-emerald-600">
                                Allows: {profile.allowed_tools.join(', ')}
                              </span>
                            )}
                            {profile.denied_tools && (
                              <span className="text-xs text-red-600">
                                Denies: {profile.denied_tools.join(', ')}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      {!profile.is_default && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleSetDefault(profile.id)}
                          title="Set as default"
                        >
                          <Star className="h-4 w-4" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEditingProfile(profile.id)
                          setFormName(profile.name)
                          setFormMode(profile.mode)
                          setFormDescription(profile.description ?? '')
                          setShowCreate(true)
                        }}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(profile.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Create/Edit Dialog */}
      <Dialog open={showCreate} onOpenChange={(open) => { setShowCreate(open); if (!open) resetForm() }}>
        <DialogContent className={MODAL_SIZES.SM}>
          <DialogHeader>
            <DialogTitle>{editingProfile ? 'Edit Profile' : 'New Autonomy Profile'}</DialogTitle>
            <DialogDescription>
              Define a reusable autonomy configuration
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">Name</label>
              <Input
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. Code Review"
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Mode</label>
              <Select value={formMode} onValueChange={(v) => setFormMode(v as AutonomyMode)}>
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.entries(AUTONOMY_MODES) as [AutonomyMode, typeof AUTONOMY_MODES[AutonomyMode]][]).map(([mode, config]) => (
                    <SelectItem key={mode} value={mode}>
                      {config.label} — {config.description}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-sm font-medium">Description</label>
              <Textarea
                value={formDescription}
                onChange={(e) => setFormDescription(e.target.value)}
                placeholder="Optional description for this profile"
                className="mt-1"
                rows={2}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowCreate(false); resetForm() }}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={saving || !formName.trim()}>
              {editingProfile ? 'Save Changes' : 'Create Profile'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
