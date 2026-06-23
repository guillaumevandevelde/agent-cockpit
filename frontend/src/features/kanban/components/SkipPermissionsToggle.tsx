import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function SkipPermissionsToggle({ projectKey }: { projectKey: string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getSkipPermissions(projectKey)
      .then((r) => setEnabled(r.enabled))
      .catch(() => setEnabled(true));
  }, [projectKey]);

  if (!projectKey || enabled === null) return null;

  const toggle = async () => {
    const next = !enabled;
    try {
      await kanbanApi.setSkipPermissions(projectKey, next);
      setEnabled(next);
      toast.success(
        next ? "Permissions: bypass (autonomous)" : "Permissions: normal (with prompts)"
      );
    } catch {
      toast.error("Failed to change permissions setting");
    }
  };

  return (
    <Button
      size="sm"
      variant={enabled ? "destructive" : "outline"}
      onClick={toggle}
      title="Whether dispatched sessions run with --dangerously-skip-permissions (autonomous, no prompts)"
    >
      {enabled ? "Perms: bypass" : "Perms: normal"}
    </Button>
  );
}
