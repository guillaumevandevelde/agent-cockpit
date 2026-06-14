import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function EnableKanbanToggle({
  projectPath,
  onChanged,
}: {
  projectPath: string;
  onChanged: () => void;
}) {
  return (
    <div className="flex gap-2">
      <Button
        size="sm"
        variant="outline"
        onClick={async () => {
          await kanbanApi.enable(projectPath);
          onChanged();
        }}
      >
        Enable kanban (register MCP)
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={async () => {
          await kanbanApi.disable(projectPath);
          onChanged();
        }}
      >
        Disable
      </Button>
    </div>
  );
}
