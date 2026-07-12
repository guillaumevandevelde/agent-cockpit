import { useState } from 'react'
import { Pencil, Trash2, PlayCircle, Plus, Layers } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { toast } from 'sonner'
import { useFetchData } from '@/hooks/useFetchData'
import { useProjectContext } from '@/contexts/ProjectContext'
import { CLICKABLE_CARD } from '@/lib/constants'
import { RefreshButton } from '@/components/shared/RefreshButton'
import {
  applyBlueprint,
  createBlueprint,
  deleteBlueprint,
  listBlueprints,
  updateBlueprint,
} from './api'
import type { Blueprint, BlueprintCreate, BlueprintUpdate } from './types'
import { BlueprintCreateDialog } from './BlueprintCreateDialog'
import { BlueprintEditor } from './BlueprintEditor'
import { BlueprintApplyDialog } from './BlueprintApplyDialog'

export function BlueprintsPage() {
  const { activeProject } = useProjectContext()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Blueprint | null>(null)
  const [applying, setApplying] = useState<Blueprint | null>(null)
  const [pendingDelete, setPendingDelete] = useState<Blueprint | null>(null)

  const { data, loading, error, refresh } = useFetchData<Blueprint[]>(
    () => listBlueprints(),
    [],
    (msg) => toast.error(`Failed to load blueprints: ${msg}`),
  )
  const blueprints = data ?? []

  const handleCreate = async (payload: BlueprintCreate) => {
    const created = await createBlueprint(payload)
    toast.success(`Blueprint '${created.name}' created`)
    await refresh()
  }

  const handleUpdate = async (name: string, update: BlueprintUpdate) => {
    const updated = await updateBlueprint(name, update)
    toast.success(`Blueprint '${updated.name}' updated`)
    await refresh()
  }

  const handleDelete = async (bp: Blueprint) => {
    await deleteBlueprint(bp.name)
    toast.success(`Blueprint '${bp.name}' deleted`)
    await refresh()
  }

  const handleApply = async (
    name: string,
    projectPath: string,
    force: boolean,
  ) => {
    const result = await applyBlueprint(name, {
      project_path: projectPath,
      force,
    })
    if (result.skipped_existing) {
      toast.info('Skipped — .claude/ already populated (pass force to overwrite)')
    } else {
      toast.success(
        `Applied: ${result.written_files.length} files, ${result.applied_skills.length} skills, ${result.applied_agents.length} agents`,
      )
    }
    return result
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Blueprints</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Version-pinned recipes that seed a project's{' '}
            <code>.claude/</code> folder on demand. Used by{' '}
            <code>create_project_from_intake</code> and any operator who
            wants to onboard a new project with a known-good baseline.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <RefreshButton onClick={refresh} loading={loading} />
          <Button onClick={() => setCreating(true)}>
            <Plus className="mr-2 h-4 w-4" /> New blueprint
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading && blueprints.length === 0 ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : blueprints.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12">
            <Layers className="h-10 w-10 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No blueprints yet. Create one to seed a project's{' '}
              <code>.claude/</code>.
            </p>
            <Button onClick={() => setCreating(true)}>
              <Plus className="mr-2 h-4 w-4" /> New blueprint
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {blueprints.map((bp) => (
            <BlueprintCard
              key={bp.name}
              blueprint={bp}
              onEdit={() => setEditing(bp)}
              onApply={() => setApplying(bp)}
              onDelete={() => setPendingDelete(bp)}
            />
          ))}
        </div>
      )}

      <BlueprintCreateDialog
        open={creating}
        onOpenChange={setCreating}
        onCreate={handleCreate}
      />
      <BlueprintEditor
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        blueprint={editing}
        onSave={handleUpdate}
      />
      <BlueprintApplyDialog
        open={applying !== null}
        onOpenChange={(o) => !o && setApplying(null)}
        blueprint={applying}
        defaultProjectPath={activeProject?.path}
        onApply={handleApply}
      />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(o) => !o && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete blueprint?</AlertDialogTitle>
            <AlertDialogDescription>
              Blueprint <code>{pendingDelete?.name}</code> will be removed
              from the store. This doesn't affect projects that have already
              been seeded with it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingDelete) {
                  void handleDelete(pendingDelete)
                  setPendingDelete(null)
                }
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

interface BlueprintCardProps {
  blueprint: Blueprint
  onEdit: () => void
  onApply: () => void
  onDelete: () => void
}

function BlueprintCard({
  blueprint: bp,
  onEdit,
  onApply,
  onDelete,
}: BlueprintCardProps) {
  return (
    <Card className={CLICKABLE_CARD} onClick={onApply}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between">
          <CardTitle className="text-lg">{bp.name}</CardTitle>
          <div
            className="flex items-center gap-1"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <Button
              variant="ghost"
              size="icon"
              onClick={onApply}
              title="Apply to a project"
            >
              <PlayCircle className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={onEdit}
              title="Edit"
            >
              <Pencil className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="text-destructive hover:text-destructive"
              onClick={onDelete}
              title="Delete"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
        {bp.description && (
          <CardDescription className="line-clamp-2">
            {bp.description}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-1">
          <Badge variant="outline">v{bp.version}</Badge>
          {bp.skills.length > 0 && (
            <Badge variant="secondary">
              {bp.skills.length} skill{bp.skills.length === 1 ? '' : 's'}
            </Badge>
          )}
          {bp.agents.length > 0 && (
            <Badge variant="secondary">
              {bp.agents.length} agent{bp.agents.length === 1 ? '' : 's'}
            </Badge>
          )}
          {bp.statusline && <Badge variant="secondary">statusline</Badge>}
          {bp.output_style && (
            <Badge variant="secondary">output style</Badge>
          )}
          {bp.claudemd && <Badge variant="secondary">CLAUDE.md</Badge>}
        </div>
        {bp.updated_at && (
          <p className="mt-3 text-xs text-muted-foreground">
            Updated {new Date(bp.updated_at).toLocaleString()}
          </p>
        )}
      </CardContent>
    </Card>
  )
}