import { useEffect, useState } from "react";
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
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi
      .mcpStatus(projectPath)
      .then((r) => setEnabled(r.enabled))
      .catch(() => setEnabled(false));
  }, [projectPath]);

  if (!projectPath || enabled === null) return null;

  return (
    <div className="flex gap-2">
      <Button
        size="sm"
        variant={enabled ? "default" : "outline"}
        onClick={async () => {
          try {
            await kanbanApi.enable(projectPath);
            setEnabled(true);
            toast.success("Kanban enabled (MCP registered)");
            onChanged();
          } catch {
            toast.error("Failed to enable kanban");
          }
        }}
      >
        {enabled ? "MCP: enabled" : "Enable MCP"}
      </Button>
      {enabled && (
        <Button
          size="sm"
          variant="ghost"
          onClick={async () => {
            try {
              await kanbanApi.disable(projectPath);
              setEnabled(false);
              toast.success("Kanban disabled");
              onChanged();
            } catch {
              toast.error("Failed to disable kanban");
            }
          }}
        >
          Disable
        </Button>
      )}
    </div>
  );
}
