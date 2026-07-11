import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";
import { PROVIDERS, PROVIDER_LABELS, DEFAULT_MODEL_SUGGESTIONS, modelSuggestionsForProvider } from "../types";
import type { KanbanColumn } from "../types";

const BACKLOG_COLUMN = "Backlog";
const DEFAULT_PROVIDER_SENTINEL = "__default__";

export function ColumnSettingsDialog({
  open,
  projectKey,
  projectPath,
  columns,
  onClose,
  onChanged,
}: {
  open: boolean;
  projectKey: string;
  projectPath: string;
  columns: KanbanColumn[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<KanbanColumn[]>(columns);
  const [availableAgents, setAvailableAgents] = useState<string[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editAgent, setEditAgent] = useState<string>("");
  const [editProvider, setEditProvider] = useState<string>(DEFAULT_PROVIDER_SENTINEL);
  const [editModel, setEditModel] = useState<string>("");
  const [editMaxSessions, setEditMaxSessions] = useState<number | null>(null);
  const [modelOptions, setModelOptions] = useState<string[]>([...DEFAULT_MODEL_SUGGESTIONS]);

  useEffect(() => {
    setItems(columns);
  }, [columns]);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi.agents(projectPath).then((r) => setAvailableAgents(r.agents));
  }, [projectPath]);

  useEffect(() => {
    if (!open) return;
    kanbanApi.getModelOptions()
      .then((r) => { if (Array.isArray(r?.options)) setModelOptions(r.options); })
      .catch(() => {});
  }, [open]);

  const handleRefreshModels = async () => {
    try {
      const r = await kanbanApi.refreshModelOptions();
      if (Array.isArray(r?.options)) setModelOptions(r.options);
    } catch {
      toast.error("Failed to refresh model list");
    }
  };

  const handleCreate = async () => {
    if (!selectedAgent) {
      toast.error("Select an agent first");
      return;
    }
    const name = selectedAgent;
    if (items.some((c) => c.name === name)) {
      toast.error("Column for this agent already exists");
      return;
    }
    try {
      const col = await kanbanApi.createColumn({
        project_key: projectKey,
        name,
        default_agent: name,
      });
      setItems((prev) => [...prev, col]);
      setSelectedAgent("");
      onChanged();
    } catch {
      toast.error("Failed to create column");
    }
  };

  const handleUpdate = async (id: string) => {
    const agent = editAgent.trim() || null;
    const provider = editProvider === DEFAULT_PROVIDER_SENTINEL ? null : editProvider;
    const model = editModel.trim() || null;
    try {
      const col = await kanbanApi.updateColumn(id, {
        default_agent: agent,
        default_provider: provider,
        default_model: model,
        max_sessions: editMaxSessions,
      });
      setItems((prev) => prev.map((c) => (c.id === id ? col : c)));
      setEditingId(null);
      onChanged();
    } catch {
      toast.error("Failed to update column");
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (name === BACKLOG_COLUMN) {
      toast.error("Cannot delete the Backlog column");
      return;
    }
    try {
      await kanbanApi.deleteColumn(id);
      setItems((prev) => prev.filter((c) => c.id !== id));
      onChanged();
    } catch {
      toast.error("Failed to delete column");
    }
  };

  const isBacklog = (name: string) => name === BACKLOG_COLUMN;
  const usedAgents = items.map((c) => c.default_agent).filter(Boolean);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Column Settings</DialogTitle>
          <DialogDescription>
            The Backlog column is always present. Add columns by selecting an
            agent from the dropdown — the column will take the agent's name.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 max-h-80 overflow-y-auto">
          {items.map((col) => (
            <div
              key={col.id}
              className="flex items-center gap-2 p-2 rounded border"
            >
              {editingId === col.id ? (
                <>
                  <div className="flex-1">
                    <div className="text-sm font-medium">{col.name}</div>
                  </div>
                  <Select
                    value={editAgent}
                    onValueChange={setEditAgent}
                  >
                    <SelectTrigger className="w-48">
                      <SelectValue placeholder="Default agent" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">None</SelectItem>
                      {availableAgents.map((a) => (
                        <SelectItem key={a} value={a}>
                          {a}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={editProvider}
                    onValueChange={setEditProvider}
                  >
                    <SelectTrigger className="w-40">
                      <SelectValue placeholder="Provider" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={DEFAULT_PROVIDER_SENTINEL}>Default (Anthropic)</SelectItem>
                      {PROVIDERS.map((p) => (
                        <SelectItem key={p} value={p}>
                          {PROVIDER_LABELS[p]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <div className="flex flex-col gap-1">
                    <label htmlFor={`default-model-${col.id}`} className="sr-only">
                      Default model
                    </label>
                    <input
                      id={`default-model-${col.id}`}
                      list={`model-suggestions-${col.id}`}
                      className="h-8 w-32 rounded border bg-background px-2 text-sm"
                      placeholder="Default model"
                      value={editModel}
                      onChange={(e) => setEditModel(e.target.value)}
                    />
                    <datalist id={`model-suggestions-${col.id}`}>
                      {modelSuggestionsForProvider(
                        editProvider === DEFAULT_PROVIDER_SENTINEL ? null : editProvider,
                        modelOptions,
                      ).map((m) => (
                        <option key={m} value={m} />
                      ))}
                    </datalist>
                    <button
                      type="button"
                      className="text-[10px] text-muted-foreground hover:text-foreground text-left"
                      onClick={handleRefreshModels}
                    >
                      Refresh
                    </button>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      className="h-7 w-7 rounded border text-sm hover:bg-accent disabled:opacity-30"
                      onClick={() => setEditMaxSessions(Math.max(1, (editMaxSessions ?? 0) - 1))}
                      disabled={(editMaxSessions ?? 0) <= 1}
                      title="Decrease max sessions"
                    >−</button>
                    <span className="w-12 text-center text-xs tabular-nums">
                      {editMaxSessions ?? "∞"}
                    </span>
                    <button
                      className="h-7 w-7 rounded border text-sm hover:bg-accent"
                      onClick={() => setEditMaxSessions((editMaxSessions ?? 0) + 1)}
                      title="Increase max sessions"
                    >+</button>
                    <button
                      className="ml-1 h-7 rounded border px-2 text-[10px] hover:bg-accent"
                      onClick={() => setEditMaxSessions(0)}
                      title="No per-column limit (use project cap)"
                    >∞</button>
                  </div>
                  <Button size="sm" onClick={() => handleUpdate(col.id)}>
                    Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                    Cancel
                  </Button>
                </>
              ) : (
                <>
                  <div className="flex-1">
                    <div className="text-sm font-medium">{col.name}</div>
                    {col.default_agent && (
                      <div className="text-xs text-muted-foreground">
                        Agent: {col.default_agent}
                      </div>
                    )}
                    {col.default_provider && (
                      <div className="text-xs text-muted-foreground">
                        Provider: {PROVIDER_LABELS[col.default_provider] ?? col.default_provider}
                      </div>
                    )}
                    {col.default_model && (
                      <div className="text-xs text-muted-foreground">
                        Model: {col.default_model}
                      </div>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground tabular-nums mr-2" title="Max concurrent sessions">
                    {col.max_sessions != null && col.max_sessions > 0 ? `max ${col.max_sessions}` : "∞"}
                  </div>
                  {!isBacklog(col.name) && (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditingId(col.id);
                          setEditAgent(col.default_agent ?? "");
                          setEditProvider(col.default_provider ?? DEFAULT_PROVIDER_SENTINEL);
                          setEditModel(col.default_model ?? "");
                          setEditMaxSessions(col.max_sessions ?? 0);
                        }}
                      >
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive"
                        onClick={() => handleDelete(col.id, col.name)}
                      >
                        Delete
                      </Button>
                    </>
                  )}
                </>
              )}
            </div>
          ))}
        </div>

        <div className="flex gap-2 pt-2 border-t">
          <Select value={selectedAgent} onValueChange={setSelectedAgent}>
            <SelectTrigger className="flex-1">
              <SelectValue placeholder="Select agent to add column" />
            </SelectTrigger>
            <SelectContent>
              {availableAgents
                .filter((a) => !usedAgents.includes(a))
                .map((a) => (
                  <SelectItem key={a} value={a}>
                    {a}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          <Button onClick={handleCreate} disabled={!selectedAgent}>
            Add Column
          </Button>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
