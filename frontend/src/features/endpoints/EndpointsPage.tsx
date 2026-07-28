import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, RefreshCw, Server, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useProjectContext } from "@/contexts/ProjectContext";
import { fetchEndpoints, upsertEndpoint, deleteEndpoint } from "@/features/cc-bridge/api";
import { kanbanApi } from "@/features/kanban/api";
import type { EndpointResponse, EndpointUpsertRequest } from "@/features/cc-bridge/types";
import { EndpointDialog } from "./EndpointDialog";

// CRUD page for project-scoped Anthropic-compatible endpoints. The dispatcher
// resolves the `endpoint_name` on a pool entry / column override against this
// list at dispatch time (see backend/app/services/agentic_cli/endpoints.py).
// Without a page here, the SubscriptionPoolDialog / CardEditDialog empty-state
// hint that links to /endpoints would 404. Built alongside kanban card
// d628054b261442c98892c7b7b17251b9 — see docs/cockpit/9router-integratie-analyse.md §K3.
export function EndpointsPage() {
  const { activeProject } = useProjectContext();
  const projectPath = activeProject?.path ?? null;
  const [projectKey, setProjectKey] = useState<string | null>(null);

  const [endpoints, setEndpoints] = useState<EndpointResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [showDialog, setShowDialog] = useState(false);
  const [editing, setEditing] = useState<EndpointResponse | null>(null);

  useEffect(() => {
    if (!projectPath) {
      setProjectKey(null);
      return;
    }
    let cancelled = false;
    kanbanApi.projectKey(projectPath).then((r) => {
      if (!cancelled) setProjectKey(r.project_key);
    }).catch(() => {
      if (!cancelled) setProjectKey(null);
    });
    return () => { cancelled = true; };
  }, [projectPath]);

  const load = useCallback(async () => {
    if (!projectKey) return;
    setLoading(true);
    try {
      const data = await fetchEndpoints(projectKey);
      setEndpoints(data.endpoints ?? []);
    } catch {
      toast.error("Failed to load endpoints");
    } finally {
      setLoading(false);
    }
  }, [projectKey]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSave(request: EndpointUpsertRequest) {
    if (!projectKey) return;
    await upsertEndpoint(projectKey, request);
    toast.success(`Saved endpoint "${request.name}"`);
    setShowDialog(false);
    setEditing(null);
    await load();
  }

  async function handleDelete(ep: EndpointResponse) {
    if (!projectKey) return;
    if (!confirm(`Delete endpoint "${ep.name}"?`)) return;
    try {
      await deleteEndpoint(projectKey, ep.name);
      toast.success(`Deleted endpoint "${ep.name}"`);
      await load();
    } catch {
      toast.error("Failed to delete endpoint");
    }
  }

  function handleEdit(ep: EndpointResponse) {
    setEditing(ep);
    setShowDialog(true);
  }

  function handleAdd() {
    setEditing(null);
    setShowDialog(true);
  }

  if (!projectKey) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Select an active project to manage its endpoints.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Endpoints</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Anthropic-compatible endpoints available to subscription pool entries and column overrides.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={load}
            disabled={loading}
            aria-label="Refresh endpoints"
          >
            <RefreshCw className={cn("h-4 w-4 mr-1", loading && "animate-spin")} />
            Refresh
          </Button>
          <Button size="sm" onClick={handleAdd}>
            <Plus className="h-4 w-4 mr-1" />
            Add endpoint
          </Button>
        </div>
      </div>

      {loading && endpoints.length === 0 ? (
        <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
          Loading endpoints...
        </div>
      ) : endpoints.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground">
          <Server className="h-12 w-12 mb-4 opacity-20" />
          <p>No endpoints configured yet.</p>
          <p className="mt-1">Add one to make it available as a subscription pool entry.</p>
          <Button variant="outline" size="sm" className="mt-4" onClick={handleAdd}>
            <Plus className="h-4 w-4 mr-1" />
            Add endpoint
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {endpoints.map((ep) => (
            <Card
              key={ep.name}
              className="cursor-pointer transition-colors border-2 hover:border-primary/50 focus-visible:ring-2"
              onClick={() => handleEdit(ep)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  handleEdit(ep);
                }
              }}
              tabIndex={0}
              role="button"
              aria-label={`Edit endpoint ${ep.name}`}
            >
              <CardHeader className="p-4 pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base truncate">{ep.name}</CardTitle>
                  <button
                    type="button"
                    aria-label={`Delete endpoint ${ep.name}`}
                    className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground/50 hover:text-destructive transition-colors"
                    onClick={(e) => { e.stopPropagation(); void handleDelete(ep); }}
                    onKeyDown={(e) => e.stopPropagation()}
                    title="Delete endpoint"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </CardHeader>
              <CardContent className="p-4 pt-2">
                <div className="space-y-1 text-xs text-muted-foreground font-mono break-all">
                  <p title={ep.base_url}>{ep.base_url}</p>
                  <p>{ep.model}</p>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <Badge variant="outline" className={cn(
                    ep.credential_configured
                      ? "bg-green-500/10 text-green-600 border-green-500/20"
                      : "bg-muted text-muted-foreground",
                  )}>
                    {ep.credential_configured ? "Credential configured" : "No credential"}
                  </Badge>
                  {ep.credential_name && (
                    <span className="text-xs text-muted-foreground font-mono truncate" title={ep.credential_name}>
                      {ep.credential_name}
                    </span>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <EndpointDialog
        open={showDialog}
        onOpenChange={(open) => {
          setShowDialog(open);
          if (!open) setEditing(null);
        }}
        onSave={handleSave}
        editEndpoint={editing}
      />
    </div>
  );
}