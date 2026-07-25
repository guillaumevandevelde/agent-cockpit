import { useState } from "react";
import { Loader2, Square, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { appsApi } from "../appsApi";
import type { RunInstance, RunStatus } from "../types";

const STATUS_VARIANT: Record<RunStatus, BadgeProps["variant"]> = {
  pending: "outline",
  starting: "outline",
  healthy: "default",
  unhealthy: "secondary",
  failed: "destructive",
  stopped: "outline",
};

// Live preview surface for a RunService instance (kanban-card d2689f2d).
// Renders the bound URL in an iframe and a Stop-preview control that calls
// DELETE /api/v1/runs/app/{id}. Mounted inline inside CardDrawer's Done
// section once a run reaches ``healthy``.
export function PreviewPane({
  instance,
  onStopped,
  fillArea,
}: {
  instance: RunInstance;
  onStopped: () => void;
  /**
   * Opt-in for the drawer body that already owns scrolling (kanban-kaart
   * 72476d8e…). Switches the iframe from a fixed viewport height
   * (`h-[50vh]`) to `flex-1 h-full` so it fills the body and the body
   * itself stops scrolling. Caller is responsible for giving the iframe
   * a flex-column parent that has a bounded height (the body in full-area
   * mode does this).
   */
  fillArea?: boolean;
}) {
  const [stopping, setStopping] = useState(false);
  const stop = async () => {
    setStopping(true);
    try {
      await appsApi.stopRun(instance.instance_id);
      onStopped();
    } catch {
      toast.error("Stop failed — the run may already be gone");
    } finally {
      setStopping(false);
    }
  };

  return (
    <div
      className="rounded-md border p-3 text-sm space-y-2"
      data-testid="preview-pane"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Badge
            variant={STATUS_VARIANT[instance.status] ?? "outline"}
            data-testid="preview-status-badge"
            data-status={instance.status}
          >
            {instance.status === "starting" && (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden="true" />
            )}
            {instance.status}
          </Badge>
          <span className="font-mono text-xs break-all" data-testid="preview-url">
            {instance.url}
          </span>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={stop}
          disabled={stopping || instance.status === "stopped"}
          data-testid="preview-stop-button"
        >
          <Square className="mr-1 h-3 w-3" aria-hidden="true" />
          {stopping ? "Stopping…" : "Stop preview"}
        </Button>
      </div>

      {instance.status === "failed" ? (
        <div className="flex items-start gap-2 text-xs text-destructive">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <div className="font-medium">Preview failed</div>
            <div className="text-muted-foreground">
              {instance.error ?? "Unknown error"}
            </div>
          </div>
        </div>
      ) : instance.status === "stopped" ? (
        // The bind no longer exists, so pointing an iframe at ``instance.url``
        // just shows a browser "connection refused" tab — confusingly framed
        // as the preview UI. Render a static status block instead and let
        // the parent "Run this branch" control re-spawn a fresh instance.
        <div
          className="flex items-start gap-2 text-xs text-muted-foreground"
          data-testid="preview-stopped-message"
        >
          <Square
            className="mt-0.5 h-4 w-4 shrink-0"
            aria-hidden="true"
            fill="currentColor"
          />
          <div>
            <div className="font-medium">Preview stopped</div>
            <div className="text-muted-foreground">
              The instance has been torn down. Start a new run above to
              preview the branch again.
            </div>
          </div>
        </div>
      ) : (
        <iframe
          src={instance.url}
          title={`Preview ${instance.instance_id}`}
          sandbox="allow-scripts allow-same-origin"
          className={cn(fillArea ? "flex-1 min-h-0" : "h-[50vh]", "w-full rounded-md border bg-background")}
          data-testid="preview-pane-iframe"
        />
      )}
    </div>
  );
}