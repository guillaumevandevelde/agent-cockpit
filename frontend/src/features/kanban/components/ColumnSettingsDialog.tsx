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
import type { KanbanColumn } from "../types";

const BACKLOG_COLUMN = "Backlog";

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

  useEffect(() => {
    setItems(columns);
  }, [columns]);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi.agents(projectPath).then((r) => setAvailableAgents(r.agents));
  }, [projectPath]);

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
    try {
      const col = await kanbanApi.updateColumn(id, {
        default_agent: agent,
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
                  </div>
                  {!isBacklog(col.name) && (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditingId(col.id);
                          setEditAgent(col.default_agent ?? "");
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
