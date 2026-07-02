import { useState, useEffect, useCallback } from "react";
import {
  Package,
  RefreshCw,
  Plus,
  Trash2,
  Download,
  ArrowRightLeft,
  CheckCircle2,
  AlertTriangle,
  FolderOpen,
  Boxes,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RefreshButton } from "@/components/shared/RefreshButton";
import { apiClient, buildEndpoint } from "@/lib/api";
import { useProjectContext } from "@/contexts/ProjectContext";
import { toast } from "sonner";
import type {
  ApmStatus,
  ApmDependenciesResponse,
  ApmInstallResponse,
  ApmSyncResponse,
  ApmModule,
} from "@/types/apm";

export function ApmPage() {
  const { activeProject, projects } = useProjectContext();
  const [status, setStatus] = useState<ApmStatus | null>(null);
  const [deps, setDeps] = useState<ApmDependenciesResponse | null>(null);
  const [modules, setModules] = useState<ApmModule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);

  // Dialog states
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [showSyncDialog, setShowSyncDialog] = useState(false);
  const [addForm, setAddForm] = useState({ name: "", source: "" });
  const [syncForm, setSyncForm] = useState({ target_project: "" });

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { project_path: activeProject?.path };

      const [statusRes, depsRes, modulesRes] = await Promise.all([
        apiClient<ApmStatus>(buildEndpoint("apm/status", params)),
        apiClient<ApmDependenciesResponse>(buildEndpoint("apm/deps", params)),
        apiClient<{ exists: boolean; modules: ApmModule[] }>(
          buildEndpoint("apm/modules", params)
        ),
      ]);

      setStatus(statusRes);
      setDeps(depsRes);
      setModules(modulesRes.modules || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch APM data");
    } finally {
      setLoading(false);
    }
  }, [activeProject?.path]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleInstall = async (frozen: boolean = false) => {
    setInstalling(true);
    try {
      const result = await apiClient<ApmInstallResponse>(
        buildEndpoint("apm/install", { project_path: activeProject?.path }),
        {
          method: "POST",
          body: JSON.stringify({ frozen }),
        }
      );

      if (result.success) {
        toast.success("APM install completed successfully");
        await fetchData();
      } else {
        toast.error(result.message || "APM install failed");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Install failed");
    } finally {
      setInstalling(false);
    }
  };

  const handleAddDependency = async () => {
    if (!addForm.name || !addForm.source) {
      toast.error("Please fill in all fields");
      return;
    }

    try {
      await apiClient(
        buildEndpoint("apm/deps", { project_path: activeProject?.path }),
        {
          method: "POST",
          body: JSON.stringify(addForm),
        }
      );
      toast.success("Dependency added successfully");
      setShowAddDialog(false);
      setAddForm({ name: "", source: "" });
      await fetchData();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add dependency");
    }
  };

  const handleRemoveDependency = async (name: string) => {
    try {
      await apiClient(
        buildEndpoint(`apm/deps/${encodeURIComponent(name)}`, {
          project_path: activeProject?.path,
        }),
        { method: "DELETE" }
      );
      toast.success("Dependency removed");
      await fetchData();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove dependency");
    }
  };

  const handleSync = async () => {
    if (!syncForm.target_project) {
      toast.error("Please select a target project");
      return;
    }

    try {
      const result = await apiClient<ApmSyncResponse>(
        buildEndpoint("apm/sync"),
        {
          method: "POST",
          body: JSON.stringify({
            source_project: activeProject?.path,
            target_project: syncForm.target_project,
          }),
        }
      );

      toast.success(result.message);
      setShowSyncDialog(false);
      setSyncForm({ target_project: "" });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Sync failed");
    }
  };

  // Extract dependencies as a flat list
  const dependencyList = deps?.dependencies?.apm
    ? Array.isArray(deps.dependencies.apm)
      ? deps.dependencies.apm.map((dep: unknown) => {
          if (typeof dep === "string") {
            return { name: dep, source: dep };
          }
          if (typeof dep === "object" && dep !== null) {
            const obj = dep as Record<string, unknown>;
            const key = Object.keys(obj)[0];
            return { name: key, source: String(obj[key]) };
          }
          return { name: String(dep), source: String(dep) };
        })
      : []
    : [];

  if (loading && !status) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Package className="h-8 w-8" />
            APM - Agent Package Manager
          </h1>
          <p className="text-muted-foreground mt-1">
            Manage AI agent dependencies per project
          </p>
        </div>
        <div className="flex gap-2">
          <RefreshButton onClick={fetchData} loading={loading} />
          <Button onClick={() => handleInstall(false)} disabled={installing}>
            <Download className="h-4 w-4 mr-2" />
            {installing ? "Installing..." : "Install All"}
          </Button>
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive">Error</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
        </Card>
      )}

      {/* Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-3">
            <CardDescription className="flex items-center gap-2">
              <Package className="h-4 w-4 text-primary" />
              APM Status
            </CardDescription>
            <CardTitle className="text-lg">
              {status?.apm_installed ? (
                <Badge className="bg-green-100 text-green-800">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  Installed
                </Badge>
              ) : (
                <Badge className="bg-red-100 text-red-800">
                  <AlertTriangle className="h-3 w-3 mr-1" />
                  Not Found
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardDescription className="flex items-center gap-2">
              <FolderOpen className="h-4 w-4 text-blue-500" />
              apm.yml
            </CardDescription>
            <CardTitle className="text-lg">
              {status?.apm_yml_exists ? (
                <Badge className="bg-blue-100 text-blue-800">Found</Badge>
              ) : (
                <Badge className="bg-gray-100 text-gray-800">Not Found</Badge>
              )}
            </CardTitle>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardDescription className="flex items-center gap-2">
              <Boxes className="h-4 w-4 text-purple-500" />
              Dependencies
            </CardDescription>
            <CardTitle className="text-3xl">{dependencyList.length}</CardTitle>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardDescription className="flex items-center gap-2">
              <Package className="h-4 w-4 text-orange-500" />
              Installed Modules
            </CardDescription>
            <CardTitle className="text-3xl">{modules.length}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => setShowAddDialog(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Add Dependency
        </Button>
        <Button variant="outline" onClick={() => setShowSyncDialog(true)}>
          <ArrowRightLeft className="h-4 w-4 mr-2" />
          Sync to Project
        </Button>
        <Button variant="outline" onClick={() => handleInstall(true)}>
          <Download className="h-4 w-4 mr-2" />
          Install (Frozen)
        </Button>
      </div>

      {/* Dependencies List */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Package className="h-5 w-5 text-primary" />
            Dependencies
          </CardTitle>
          <CardDescription>
            Packages defined in apm.yml for this project
          </CardDescription>
        </CardHeader>
        <CardContent>
          {dependencyList.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No dependencies configured. Click "Add Dependency" to get started.
            </div>
          ) : (
            <div className="space-y-2">
              {dependencyList.map((dep) => (
                <div
                  key={dep.name}
                  className="flex items-center justify-between p-3 border rounded-lg"
                >
                  <div className="flex items-center gap-3">
                    <Package className="h-4 w-4 text-muted-foreground" />
                    <div>
                      <div className="font-medium">{dep.name}</div>
                      <div className="text-sm text-muted-foreground">
                        {dep.source}
                      </div>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleRemoveDependency(dep.name)}
                  >
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Installed Modules */}
      {modules.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Boxes className="h-5 w-5 text-purple-500" />
              Installed Modules
            </CardTitle>
            <CardDescription>
              Packages installed in apm_modules/
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {modules.map((mod) => (
                <div
                  key={mod.name}
                  className="flex items-center gap-3 p-3 border rounded-lg"
                >
                  <Package className="h-4 w-4 text-purple-500" />
                  <div>
                    <div className="font-medium">{mod.name}</div>
                    <div className="text-sm text-muted-foreground">{mod.path}</div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Add Dependency Dialog */}
      <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Dependency</DialogTitle>
            <DialogDescription>
              Add a new package to your apm.yml
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">Package Name</label>
              <Input
                placeholder="e.g., context-engineering"
                value={addForm.name}
                onChange={(e) =>
                  setAddForm({ ...addForm, name: e.target.value })
                }
              />
            </div>
            <div>
              <label className="text-sm font-medium">Source</label>
              <Input
                placeholder="e.g., github/awesome-copilot/plugins/context-engineering"
                value={addForm.source}
                onChange={(e) =>
                  setAddForm({ ...addForm, source: e.target.value })
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAddDialog(false)}>
              Cancel
            </Button>
            <Button onClick={handleAddDependency}>Add</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Sync Dialog */}
      <Dialog open={showSyncDialog} onOpenChange={setShowSyncDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Sync Dependencies</DialogTitle>
            <DialogDescription>
              Copy dependencies from this project to another
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">Target Project</label>
              <Select
                value={syncForm.target_project}
                onValueChange={(value) =>
                  setSyncForm({ ...syncForm, target_project: value })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a project" />
                </SelectTrigger>
                <SelectContent>
                  {projects
                    .filter((p) => p.path !== activeProject?.path)
                    .map((project) => (
                      <SelectItem key={project.id} value={project.path}>
                        {project.name || project.path}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowSyncDialog(false)}>
              Cancel
            </Button>
            <Button onClick={handleSync}>Sync</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
