import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function ShipModeToggle({ projectKey }: { projectKey: string }) {
  const [mode, setMode] = useState<string | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getShipMode(projectKey)
      .then((r) => setMode(r.mode))
      .catch(() => setMode("pull-request"));
  }, [projectKey]);

  if (!projectKey || mode === null) return null;

  const isDirect = mode === "direct";
  const toggle = async () => {
    const next = isDirect ? "pull-request" : "direct";
    try {
      await kanbanApi.setShipMode(projectKey, next);
      setMode(next);
      toast.success(
        next === "direct" ? "Ship: direct to master" : "Ship: draft pull request"
      );
    } catch {
      toast.error("Failed to change ship mode");
    }
  };

  return (
    <Button
      size="sm"
      variant={isDirect ? "destructive" : "outline"}
      onClick={toggle}
      title="How the developer agent ships: draft PR (safe) or direct merge+push to master"
    >
      {isDirect ? "Ship: direct to master" : "Ship: draft PR"}
    </Button>
  );
}
