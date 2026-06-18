import { useEffect, useState } from 'react'
import { automationTemplatesApi } from '@/features/autonomy/api-templates'
import { TEMPLATE_CATEGORIES, type AutomationTemplate, type TemplateCategory } from '@/types/automation-templates'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
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
import {
  Workflow,
  Plus,
  Trash2,
  Play,
  Pause,
  Clock,
  Zap,
  Eye,
  Shield,
  CheckCircle,
  BookOpen,
  Gauge,
  GitPullRequest,
} from 'lucide-react'
import { CLICKABLE_CARD, MODAL_SIZES } from '@/lib/constants'

const ICON_MAP: Record<string, typeof Workflow> = {
  eye: Eye,
  shield: Shield,
  'check-circle': CheckCircle,
  'git-pull-request': GitPullRequest,
  'book-open': BookOpen,
  gauge: Gauge,
  zap: Zap,
}

function TemplateIcon({ name }: { name: string | null }) {
  const Icon = ICON_MAP[name ?? ''] ?? Workflow
  return <Icon className="h-5 w-5" />
}

export function AutomationTemplatesPage() {
  const [templates, setTemplates] = useState<AutomationTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [formName, setFormName] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formCategory, setFormCategory] = useState<TemplateCategory>('custom')
  const [formTrigger, setFormTrigger] = useState<'cron' | 'once'>('cron')
  const [formCron, setFormCron] = useState('')
  const [formMessage, setFormMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [filter, setFilter] = useState<string>('all')

  const fetchTemplates = async () => {
    setLoading(true)
    try {
      const data = await automationTemplatesApi.list()
      setTemplates(data)
    } catch {
      setTemplates([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchTemplates()
  }, [])

  const handleSeed = async () => {
    await automationTemplatesApi.seed()
    await fetchTemplates()
  }

  const handleToggle = async (template: AutomationTemplate) => {
    await automationTemplatesApi.update(template.id, { enabled: !template.enabled })
    await fetchTemplates()
  }

  const handleDelete = async (id: number) => {
    await automationTemplatesApi.delete(id)
    await fetchTemplates()
  }

  const handleCreate = async () => {
    if (!formName.trim() || !formMessage.trim()) return
    setSaving(true)
    try {
      await automationTemplatesApi.create({
        name: formName.trim(),
        description: formDescription.trim() || undefined,
        category: formCategory,
        trigger_type: formTrigger,
        cron_expr: formTrigger === 'cron' ? formCron.trim() || undefined : undefined,
        message_template: formMessage.trim(),
      })
      await fetchTemplates()
      setShowCreate(false)
      resetForm()
    } catch {
      // error handled
    } finally {
      setSaving(false)
    }
  }

  const resetForm = () => {
    setFormName('')
    setFormDescription('')
    setFormCategory('custom')
    setFormTrigger('cron')
    setFormCron('')
    setFormMessage('')
  }

  const filtered = filter === 'all'
    ? templates
    : templates.filter((t) => t.category === filter)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Workflow className="h-8 w-8" />
            Automation Templates
          </h1>
          <p className="text-muted-foreground">
            Pre-built workflow templates for common agent tasks
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleSeed}>
            <Zap className="h-4 w-4 mr-1" />
            Seed Defaults
          </Button>
          <Button size="sm" onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-1" />
            New Template
          </Button>
        </div>
      </div>

      {/* Category Filter */}
      <div className="flex gap-2 flex-wrap">
        <Button
          variant={filter === 'all' ? 'default' : 'outline'}
          size="sm"
          onClick={() => setFilter('all')}
        >
          All ({templates.length})
        </Button>
        {(Object.entries(TEMPLATE_CATEGORIES) as [TemplateCategory, { label: string; color: string }][])?.map(([cat, config]) => {
          const count = templates.filter((t) => t.category === cat).length
          if (count === 0) return null
          return (
            <Button
              key={cat}
              variant={filter === cat ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter(cat)}
            >
              {config.label} ({count})
            </Button>
          )
        })}
      </div>

      {/* Templates Grid */}
      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[...Array(6)].map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <CardDescription>Loading...</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="h-20 bg-muted rounded" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Workflow className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground">
              No templates found. Click "Seed Defaults" to load built-in templates or create your own.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((template) => {
            const catConfig = TEMPLATE_CATEGORIES[template.category] ?? TEMPLATE_CATEGORIES.general
            return (
              <div
                key={template.id}
                className={CLICKABLE_CARD + ' rounded-lg border p-4'}
              >
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div className="flex items-center gap-3">
                    <div className={`rounded-md p-2 ${catConfig.color}`}>
                      <TemplateIcon name={template.icon} />
                    </div>
                    <div>
                      <h3 className="font-medium">{template.name}</h3>
                      <div className="flex items-center gap-2 mt-0.5">
                        <Badge variant="outline" className="text-xs">{catConfig.label}</Badge>
                        {template.is_builtin && (
                          <Badge variant="secondary" className="text-xs">Built-in</Badge>
                        )}
                      </div>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleToggle(template)}
                    title={template.enabled ? 'Disable' : 'Enable'}
                  >
                    {template.enabled ? (
                      <Pause className="h-4 w-4 text-amber-500" />
                    ) : (
                      <Play className="h-4 w-4 text-emerald-500" />
                    )}
                  </Button>
                </div>

                {template.description && (
                  <p className="text-sm text-muted-foreground mb-3 line-clamp-2">
                    {template.description}
                  </p>
                )}

                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    {template.trigger_type === 'cron' ? (
                      <>
                        <Clock className="h-3 w-3" />
                        <span>Cron: {template.cron_expr}</span>
                      </>
                    ) : (
                      <>
                        <Zap className="h-3 w-3" />
                        <span>One-time trigger</span>
                      </>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Shield className="h-3 w-3" />
                    <span>Permission: {template.permission_mode}</span>
                  </div>
                  {template.tags && template.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {template.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                <p className="text-xs text-muted-foreground mt-3 line-clamp-2 border-t pt-2">
                  {template.message_template}
                </p>

                {!template.is_builtin && (
                  <div className="mt-3 pt-2 border-t">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full text-destructive"
                      onClick={() => handleDelete(template.id)}
                    >
                      <Trash2 className="h-4 w-4 mr-1" />
                      Delete
                    </Button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Create Dialog */}
      <Dialog open={showCreate} onOpenChange={(open) => { setShowCreate(open); if (!open) resetForm() }}>
        <DialogContent className={MODAL_SIZES.MD}>
          <DialogHeader>
            <DialogTitle>New Automation Template</DialogTitle>
            <DialogDescription>
              Create a reusable workflow template for agent tasks
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="text-sm font-medium">Name</label>
                <Input
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="e.g. Weekly Security Audit"
                  className="mt-1"
                />
              </div>
              <div>
                <label className="text-sm font-medium">Category</label>
                <Select value={formCategory} onValueChange={(v) => setFormCategory(v as TemplateCategory)}>
                  <SelectTrigger className="mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(TEMPLATE_CATEGORIES).map(([cat, config]) => (
                      <SelectItem key={cat} value={cat}>{config.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">Description</label>
              <Input
                value={formDescription}
                onChange={(e) => setFormDescription(e.target.value)}
                placeholder="Brief description of what this template does"
                className="mt-1"
              />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="text-sm font-medium">Trigger</label>
                <Select value={formTrigger} onValueChange={(v) => setFormTrigger(v as 'cron' | 'once')}>
                  <SelectTrigger className="mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cron">Recurring (cron)</SelectItem>
                    <SelectItem value="once">One-time</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {formTrigger === 'cron' && (
                <div>
                  <label className="text-sm font-medium">Cron Expression</label>
                  <Input
                    value={formCron}
                    onChange={(e) => setFormCron(e.target.value)}
                    placeholder="0 9 * * 1-5"
                    className="mt-1"
                  />
                </div>
              )}
            </div>
            <div>
              <label className="text-sm font-medium">Message Template</label>
              <Textarea
                value={formMessage}
                onChange={(e) => setFormMessage(e.target.value)}
                placeholder="What should the agent do when this template fires?"
                className="mt-1"
                rows={4}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowCreate(false); resetForm() }}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={saving || !formName.trim() || !formMessage.trim()}>
              Create Template
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
