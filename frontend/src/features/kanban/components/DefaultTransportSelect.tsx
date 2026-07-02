import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { kanbanApi } from "../api";

export function DefaultTransportSelect({ projectKey }: { projectKey: string }) {
  const [transport, setTransport] = useState<string | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getDefaultTransport(projectKey)
      .then((r) => setTransport(r.transport))
      .catch(() => setTransport("worktree"));
  }, [projectKey]);

  if (!projectKey || transport === null) return null;

  const onChange = async (next: string) => {
    const prev = transport;
    setTransport(next);
    try {
      await kanbanApi.setDefaultTransport(projectKey, next);
      toast.success(`Default transport: ${next}`);
    } catch {
      setTransport(prev);
      toast.error("Failed to set default transport");
    }
  };

  return (
    <div className="inline-flex items-center gap-2">
      <Select value={transport} onValueChange={onChange}>
        <SelectTrigger
          className="h-8 w-[170px]"
          title="Transport that card 'auto' resolves to for this project"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="worktree">Transport: worktree</SelectItem>
          <SelectItem value="sandcastle">Transport: sandcastle</SelectItem>
        </SelectContent>
      </Select>
      {transport === "sandcastle" && (
        <a href="/sandcastle" className="text-xs text-muted-foreground underline">
          Configure sandcastle
        </a>
      )}
    </div>
  );
}
