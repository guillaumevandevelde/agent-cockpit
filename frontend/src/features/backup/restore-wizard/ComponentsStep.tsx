import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Package, Puzzle } from "lucide-react";
import { type RestorePlan } from "@/types/backup";

interface ComponentsStepProps {
  plan: RestorePlan | null;
  selectAll: boolean;
  selectedFiles: Set<string>;
  skipSkills: boolean;
  skipPlugins: boolean;
  onSelectAllFiles: (checked: boolean) => void;
  onFileToggle: (file: string) => void;
  onSkipSkillsChange: (v: boolean) => void;
  onSkipPluginsChange: (v: boolean) => void;
}

export function ComponentsStep({
  plan,
  selectAll,
  selectedFiles,
  skipSkills,
  skipPlugins,
  onSelectAllFiles,
  onFileToggle,
  onSkipSkillsChange,
  onSkipPluginsChange,
}: ComponentsStepProps) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Choose which components to restore. Uncheck items you want to skip.
      </p>

      <div className="flex items-center gap-2 pb-2 border-b">
        <Checkbox
          id="select-all"
          checked={selectAll}
          onCheckedChange={(checked) => onSelectAllFiles(checked === true)}
        />
        <Label htmlFor="select-all" className="font-medium">
          Select All ({plan?.files_to_restore.length} files)
        </Label>
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between p-3 border rounded-lg">
          <div className="flex items-center gap-2">
            <Package className="h-4 w-4 text-green-600" />
            <span>Skills</span>
            <Badge variant="outline">{plan?.skills_to_restore.length || 0}</Badge>
          </div>
          <Switch
            checked={!skipSkills}
            onCheckedChange={(checked) => onSkipSkillsChange(!checked)}
          />
        </div>

        <div className="flex items-center justify-between p-3 border rounded-lg">
          <div className="flex items-center gap-2">
            <Puzzle className="h-4 w-4 text-purple-600" />
            <span>Plugins</span>
            <Badge variant="outline">{plan?.plugins_to_restore.length || 0}</Badge>
          </div>
          <Switch
            checked={!skipPlugins}
            onCheckedChange={(checked) => onSkipPluginsChange(!checked)}
          />
        </div>
      </div>

      {!selectAll && (
        <ScrollArea className="h-[200px] border rounded-lg p-3">
          <div className="space-y-2">
            {plan?.files_to_restore.map((file) => (
              <div key={file} className="flex items-center gap-2">
                <Checkbox
                  id={file}
                  checked={selectedFiles.has(file)}
                  onCheckedChange={() => onFileToggle(file)}
                />
                <Label htmlFor={file} className="font-mono text-xs truncate">
                  {file}
                </Label>
              </div>
            ))}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
