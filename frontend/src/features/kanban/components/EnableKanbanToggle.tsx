import { toast } from "sonner";
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
          try {
            await kanbanApi.enable(projectPath);
            toast.success("Kanban enabled (MCP registered)");
            onChanged();
          } catch {
            toast.error("Failed to enable kanban");
          }
        }}
      >
        Enable kanban (register MCP)
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={async () => {
          try {
            await kanbanApi.disable(projectPath);
            toast.success("Kanban disabled");
            onChanged();
          } catch {
            toast.error("Failed to disable kanban");
          }
        }}
      >
        Disable
      </Button>
    </div>
  );
}
