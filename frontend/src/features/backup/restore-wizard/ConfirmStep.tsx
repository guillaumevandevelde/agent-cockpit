import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { AlertTriangle } from "lucide-react";
import { type Backup, type RestorePlan } from "@/types/backup";

interface ConfirmStepProps {
  backup: Backup;
  plan: RestorePlan | null;
  selectAll: boolean;
  selectedFiles: Set<string>;
  skipSkills: boolean;
  skipPlugins: boolean;
  skipKanbanDb: boolean;
  installDependencies: boolean;
  dryRun: boolean;
  onDryRunChange: (v: boolean) => void;
}

export function ConfirmStep({
  backup,
  plan,
  selectAll,
  selectedFiles,
  skipSkills,
  skipPlugins,
  skipKanbanDb,
  installDependencies,
  dryRun,
  onDryRunChange,
}: ConfirmStepProps) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">Review your restore settings:</p>

      <div className="p-4 bg-muted rounded-lg space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-muted-foreground">Backup:</span>
          <span className="font-medium">{backup.name}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Files:</span>
          <span className="font-medium">
            {selectAll ? plan?.files_to_restore.length : selectedFiles.size} files
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Skip skills:</span>
          <span className="font-medium">{skipSkills ? "Yes" : "No"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Skip plugins:</span>
          <span className="font-medium">{skipPlugins ? "Yes" : "No"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Skip kanban board:</span>
          <span className="font-medium">{skipKanbanDb ? "Yes" : "No"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Install deps:</span>
          <span className="font-medium">
            {installDependencies && plan?.has_dependencies ? "Yes" : "No"}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2 p-3 border rounded-lg">
        <Checkbox
          id="dry-run"
          checked={dryRun}
          onCheckedChange={(checked) => onDryRunChange(checked === true)}
        />
        <Label htmlFor="dry-run" className="cursor-pointer">
          <span className="font-medium">Dry run</span>
          <p className="text-xs text-muted-foreground">
            Preview what would be restored without making changes
          </p>
        </Label>
      </div>

      {!dryRun && (
        <div className="flex items-center gap-2 p-3 bg-destructive/10 rounded-lg">
          <AlertTriangle className="h-4 w-4 text-destructive" />
          <span className="text-sm text-destructive">
            Existing files will be overwritten. This cannot be undone.
          </span>
        </div>
      )}
    </div>
  );
}
