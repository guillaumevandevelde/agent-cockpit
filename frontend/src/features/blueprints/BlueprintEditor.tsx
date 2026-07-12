import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { MODAL_SIZES } from '@/lib/constants'
import { Plus, X } from 'lucide-react'
import type {
  Blueprint,
  BlueprintAgent,
  BlueprintSettings,
  BlueprintSkill,
  BlueprintUpdate,
  PermissionMode,
  SkillSource,
} from './types'

interface BlueprintEditorProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  blueprint: Blueprint | null
  onSave: (name: string, update: BlueprintUpdate) => Promise<void>
}

const PERMISSION_MODES: { value: PermissionMode; label: string }[] = [
  { value: 'default', label: 'Default' },
  { value: 'acceptEdits', label: 'Accept Edits' },
  { value: 'bypassPermissions', label: 'Bypass Permissions' },
  { value: 'plan', label: 'Plan' },
]

const SKILL_SOURCES: { value: SkillSource; label: string }[] = [
  { value: 'project', label: 'Project (materialised locally)' },
  { value: 'user', label: 'User (reference only)' },
  { value: 'system', label: 'System (reference only)' },
]

export function BlueprintEditor({
  open,
  onOpenChange,
  blueprint,
  onSave,
}: BlueprintEditorProps) {
  const [description, setDescription] = useState('')
  const [claudemd, setClaudemd] = useState('')
  const [statusline, setStatusline] = useState('')
  const [outputStyle, setOutputStyle] = useState('')
  const [settings, setSettings] = useState<BlueprintSettings>({})
  const [skills, setSkills] = useState<BlueprintSkill[]>([])
  const [agents, setAgents] = useState<BlueprintAgent[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset form when the edited blueprint changes.
  useEffect(() => {
    if (blueprint) {
      setDescription(blueprint.description ?? '')
      setClaudemd(blueprint.claudemd ?? '')
      setStatusline(blueprint.statusline ?? '')
      setOutputStyle(blueprint.output_style ?? '')
      setSettings(blueprint.settings)
      setSkills(blueprint.skills)
      setAgents(blueprint.agents)
      setError(null)
    }
  }, [blueprint])

  const handleSave = async () => {
    if (!blueprint) return
    setSaving(true)
    setError(null)
    try {
      const update: BlueprintUpdate = {
        description: description.trim() || null,
        claudemd: claudemd || null,
        statusline: statusline || null,
        output_style: outputStyle.trim() || null,
        settings,
        skills,
        agents,
      }
      await onSave(blueprint.name, update)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save blueprint')
    } finally {
      setSaving(false)
    }
  }

  if (!blueprint) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={MODAL_SIZES.LG}>
        <DialogHeader>
          <DialogTitle>Edit Blueprint: {blueprint.name}</DialogTitle>
          <DialogDescription>
            Adjust the recipe that <code>apply()</code> will materialise on
            the target project.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="general" className="w-full">
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="general">General</TabsTrigger>
            <TabsTrigger value="skills">
              Skills
              {skills.length > 0 && (
                <Badge variant="secondary" className="ml-2">
                  {skills.length}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="agents">
              Agents
              {agents.length > 0 && (
                <Badge variant="secondary" className="ml-2">
                  {agents.length}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>

          {/* General */}
          <TabsContent value="general" className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="bp-edit-description">Description</Label>
              <Textarea
                id="bp-edit-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="min-h-[60px]"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="bp-edit-claudemd">CLAUDE.md</Label>
              <Textarea
                id="bp-edit-claudemd"
                value={claudemd}
                onChange={(e) => setClaudemd(e.target.value)}
                placeholder="# Project context&#10;..."
                className="min-h-[120px] font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">
                Leave empty to skip. Will be written to{' '}
                <code>&lt;project&gt;/CLAUDE.md</code> on apply.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="bp-edit-statusline">Statusline Script</Label>
              <Textarea
                id="bp-edit-statusline"
                value={statusline}
                onChange={(e) => setStatusline(e.target.value)}
                placeholder='#!/bin/sh&#10;echo "[$MODEL_DISPLAY] $PWD"'
                className="min-h-[100px] font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">
                Written to <code>.claude/statusline.sh</code>. Reference it
                from your settings if you wire statusLine manually.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="bp-edit-output-style">Output Style Name</Label>
              <Input
                id="bp-edit-output-style"
                value={outputStyle}
                onChange={(e) => setOutputStyle(e.target.value)}
                placeholder="concise"
              />
              <p className="text-xs text-muted-foreground">
                Writes <code>.claude/output-styles/&lt;name&gt;.md</code> as
                a stub on apply.
              </p>
            </div>
          </TabsContent>

          {/* Skills */}
          <TabsContent value="skills" className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Project-scoped skills are materialised under{' '}
              <code>.claude/skills/&lt;name&gt;/SKILL.md</code>. User /
              system skills are recorded in the audit but not copied — the
              user already has them.
            </p>
            {skills.map((skill, idx) => (
              <div
                key={idx}
                className="flex flex-wrap items-end gap-2 rounded-md border p-3"
              >
                <div className="flex-1 space-y-1">
                  <Label className="text-xs">Name</Label>
                  <Input
                    value={skill.name}
                    onChange={(e) => {
                      const next = [...skills]
                      next[idx] = { ...skill, name: e.target.value }
                      setSkills(next)
                    }}
                    placeholder="frontend"
                  />
                </div>
                <div className="w-48 space-y-1">
                  <Label className="text-xs">Source</Label>
                  <Select
                    value={skill.source}
                    onValueChange={(v) => {
                      const next = [...skills]
                      next[idx] = { ...skill, source: v as SkillSource }
                      setSkills(next)
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SKILL_SOURCES.map((s) => (
                        <SelectItem key={s.value} value={s.value}>
                          {s.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="w-32 space-y-1">
                  <Label className="text-xs">Version Pin</Label>
                  <Input
                    value={skill.version_pin ?? ''}
                    onChange={(e) => {
                      const next = [...skills]
                      next[idx] = { ...skill, version_pin: e.target.value || null }
                      setSkills(next)
                    }}
                    placeholder="1.0.0"
                  />
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setSkills(skills.filter((_, i) => i !== idx))}
                  title="Remove skill"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setSkills([
                  ...skills,
                  { name: '', source: 'project', version_pin: null },
                ])
              }
            >
              <Plus className="mr-1 h-4 w-4" /> Add skill
            </Button>
          </TabsContent>

          {/* Agents */}
          <TabsContent value="agents" className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Each agent becomes{' '}
              <code>.claude/agents/&lt;name&gt;.md</code> with{' '}
              <code>model</code> and <code>allowed-tools</code> recorded in
              the YAML frontmatter.
            </p>
            {agents.map((agent, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-md border p-3"
              >
                <div className="flex items-end gap-2">
                  <div className="flex-1 space-y-1">
                    <Label className="text-xs">Name</Label>
                    <Input
                      value={agent.name}
                      onChange={(e) => {
                        const next = [...agents]
                        next[idx] = { ...agent, name: e.target.value }
                        setAgents(next)
                      }}
                      placeholder="planner"
                    />
                  </div>
                  <div className="w-32 space-y-1">
                    <Label className="text-xs">Model</Label>
                    <Input
                      value={agent.model_default ?? ''}
                      onChange={(e) => {
                        const next = [...agents]
                        next[idx] = {
                          ...agent,
                          model_default: e.target.value || null,
                        }
                        setAgents(next)
                      }}
                      placeholder="opus"
                    />
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setAgents(agents.filter((_, i) => i !== idx))}
                    title="Remove agent"
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Tools (comma-separated)</Label>
                  <Input
                    value={agent.tools.join(', ')}
                    onChange={(e) => {
                      const next = [...agents]
                      next[idx] = {
                        ...agent,
                        tools: e.target.value
                          .split(',')
                          .map((t) => t.trim())
                          .filter(Boolean),
                      }
                      setAgents(next)
                    }}
                    placeholder="Read, Write, Glob"
                  />
                </div>
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setAgents([
                  ...agents,
                  { name: '', model_default: null, tools: [] },
                ])
              }
            >
              <Plus className="mr-1 h-4 w-4" /> Add agent
            </Button>
          </TabsContent>

          {/* Settings */}
          <TabsContent value="settings" className="space-y-4">
            <p className="text-sm text-muted-foreground">
              These land in <code>.claude/settings.json</code>. CC reads{' '}
              <code>permissions.defaultMode</code>; we re-nest the form
              value automatically.
            </p>
            <div className="space-y-2">
              <Label htmlFor="bp-permission-mode">Permission Mode</Label>
              <Select
                value={settings.permission_mode ?? 'default'}
                onValueChange={(v) =>
                  setSettings({
                    ...settings,
                    permission_mode: v as PermissionMode,
                  })
                }
              >
                <SelectTrigger id="bp-permission-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PERMISSION_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="bp-model">Default Model</Label>
              <Input
                id="bp-model"
                value={settings.model ?? ''}
                onChange={(e) =>
                  setSettings({ ...settings, model: e.target.value || null })
                }
                placeholder="opus"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="bp-plans-dir">Plans Directory</Label>
              <Input
                id="bp-plans-dir"
                value={settings.plansDirectory ?? ''}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    plansDirectory: e.target.value || null,
                  })
                }
                placeholder="~/.claude/plans"
              />
            </div>
          </TabsContent>
        </Tabs>

        {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}