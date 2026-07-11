import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Rocket } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";

/**
 * Confirmation dialog for the inceptie-pipeline "Promote to project" action
 * (kanban card c33b2f14 / facet A of platform-as-app-factory). The dialog
 * collects the only two pieces of input the action needs beyond the
 * pre-known intake card id: a project name (for the Project row + the
 * kanban-DB `slug:` project_key) and an absolute target path (where the new
 * project's repo + `.claude/` seed will live).
 *
 * The button is mounted on intake cards in `CardItem.tsx`; the parent Kanban
 * page owns the open/close state so this dialog stays free of board-level
 * concerns.
 */
export function PromoteToProjectDialog({
  open,
  intakeCardId,
  intakeCardTitle,
  defaultTargetPath,
  onClose,
  onPromoted,
}: {
  open: boolean;
  intakeCardId: string | null;
  intakeCardTitle: string;
  /** Pre-fill for `target_path` — typically `<activeProject.parent>/<slug>` */
  defaultTargetPath: string;
  onClose: () => void;
  onPromoted: (result: {
    project_id: number;
    new_project_key: string;
    first_card_id: string;
  }) => void;
}) {
  const [projectName, setProjectName] = useState("");
  const [targetPath, setTargetPath] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Reset state when the dialog opens with a fresh intake card so the
  // previous card's name/path doesn't linger.
  useEffect(() => {
    if (open) {
      setProjectName(intakeCardTitle);
      setTargetPath(defaultTargetPath);
      setSubmitting(false);
    }
  }, [open, intakeCardTitle, defaultTargetPath]);

  const submit = async () => {
    if (!intakeCardId || submitting) return;
    if (!projectName.trim() || !targetPath.trim()) {
      toast.error("Project name and target path are required");
      return;
    }
    setSubmitting(true);
    try {
      const r = await kanbanApi.createProjectFromIntake({
        intake_card_id: intakeCardId,
        project_name: projectName.trim(),
        target_path: targetPath.trim(),
      });
      toast.success(
        `Promoted to new project (${r.new_project_key}) — first card ${r.first_card_id.slice(0, 8)}…`,
      );
      onPromoted(r);
      onClose();
    } catch (err) {
      toast.error(`Promote failed: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Rocket className="h-4 w-4" aria-hidden="true" />
            Promote intake card to new project
          </DialogTitle>
          <DialogDescription>
            This is the inceptie-pipeline action from
            <code className="mx-1 rounded bg-muted px-1">docs/cockpit/product-inceptie-pipeline.md §4 optie 2</code>.
            The intake card moves to Done; a new project is registered and its
            first kanban card lands in Backlog with autodispatch enabled.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="promote-project-name">Project name</Label>
            <Input
              id="promote-project-name"
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              placeholder="my-new-app"
              data-testid="promote-project-name"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="promote-target-path">Target path</Label>
            <Input
              id="promote-target-path"
              value={targetPath}
              onChange={(e) => setTargetPath(e.target.value)}
              placeholder="/abs/path/to/new-project"
              data-testid="promote-target-path"
            />
            <p className="text-xs text-muted-foreground">
              Absolute path on this device. Must not exist yet — the action
              refuses to clobber. The new project's git repo (no remote yet)
              and a minimal <code>.claude/CLAUDE.md</code> placeholder will
              be seeded here.
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={submitting}
            data-testid="promote-submit"
          >
            {submitting ? "Promoting…" : "Promote"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
