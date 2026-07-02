import { useState } from 'react';
import { useProjectContext } from '@/contexts/ProjectContext';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Folder } from 'lucide-react';
import { MODAL_SIZES } from '@/lib/constants';
import { DirectoryBrowserDialog } from './DirectoryBrowserDialog';

interface AddProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded?: () => void;
}

/** Derive a sensible default project name from the last segment of a path. */
function basename(path: string): string {
  return path.replace(/\/+$/, '').split('/').pop() ?? '';
}

export function AddProjectDialog({ open, onOpenChange, onAdded }: AddProjectDialogProps) {
  const { addProject } = useProjectContext();
  const [path, setPath] = useState('');
  const [name, setName] = useState('');
  // Track whether the user has hand-edited the name so we stop auto-filling it.
  const [nameTouched, setNameTouched] = useState(false);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyPath = (next: string) => {
    setPath(next);
    if (!nameTouched) {
      setName(basename(next));
    }
  };

  const reset = () => {
    setPath('');
    setName('');
    setNameTouched(false);
    setError(null);
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const handleSubmit = async () => {
    const trimmedPath = path.trim();
    const trimmedName = name.trim();
    if (!trimmedPath) {
      setError('Please enter or browse to a folder path');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await addProject({ name: trimmedName || basename(trimmedPath), path: trimmedPath });
      onAdded?.();
      handleOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add project');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className={MODAL_SIZES.SM}>
          <DialogHeader>
            <DialogTitle>Add Folder Manually</DialogTitle>
            <DialogDescription>
              Track any folder as a project, even if it has no Claude Code configuration yet.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="project-path">Folder path</Label>
              <div className="flex gap-2">
                <Input
                  id="project-path"
                  type="text"
                  value={path}
                  onChange={(e) => applyPath(e.target.value)}
                  placeholder="~/dev/richtlijnen-rag"
                  onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
                />
                <Button variant="outline" onClick={() => setBrowserOpen(true)} title="Browse directories">
                  <Folder className="h-4 w-4" />
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="project-name">Name</Label>
              <Input
                id="project-name"
                type="text"
                value={name}
                onChange={(e) => { setName(e.target.value); setNameTouched(true); }}
                placeholder="Project name"
                onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
              />
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={saving || !path.trim()}>
              {saving ? 'Adding…' : 'Add Project'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DirectoryBrowserDialog
        open={browserOpen}
        onOpenChange={setBrowserOpen}
        initialPath={path}
        onSelect={applyPath}
      />
    </>
  );
}
