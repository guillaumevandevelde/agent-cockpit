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
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";
import type { KanbanColumn } from "../types";

export function ColumnSettingsDialog({
  open,
  projectKey,
  columns,
  onClose,
  onChanged,
}: {
  open: boolean;
  projectKey: string;
  columns: KanbanColumn[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<KanbanColumn[]>(columns);
  const [newName, setNewName] = useState("");
  const [newAgent, setNewAgent] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editAgent, setEditAgent] = useState("");

  useEffect(() => {
    setItems(columns);
  }, [columns]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    if (items.some((c) => c.name === name)) {
      toast.error("Column name already exists");
      return;
    }
    try {
      const col = await kanbanApi.createColumn({
        project_key: projectKey,
        name,
        default_agent: newAgent.trim() || null,
      });
      setItems((prev) => [...prev, col]);
      setNewName("");
      setNewAgent("");
      onChanged();
    } catch {
      toast.error("Failed to create column");
    }
  };

  const handleUpdate = async (id: string) => {
    const name = editName.trim();
    if (!name) return;
    try {
      const col = await kanbanApi.updateColumn(id, {
        name,
        default_agent: editAgent.trim() || null,
      });
      setItems((prev) => prev.map((c) => (c.id === id ? col : c)));
      setEditingId(null);
      onChanged();
    } catch {
      toast.error("Failed to update column");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await kanbanApi.deleteColumn(id);
      setItems((prev) => prev.filter((c) => c.id !== id));
      onChanged();
    } catch {
      toast.error("Failed to delete column");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Column Settings</DialogTitle>
          <DialogDescription>
            Manage kanban columns and their default agent assignments.
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
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="flex-1"
                  />
                  <Input
                    value={editAgent}
                    onChange={(e) => setEditAgent(e.target.value)}
                    placeholder="Default agent"
                    className="w-40"
                  />
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
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditingId(col.id);
                      setEditName(col.name);
                      setEditAgent(col.default_agent ?? "");
                    }}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive"
                    onClick={() => handleDelete(col.id)}
                  >
                    Delete
                  </Button>
                </>
              )}
            </div>
          ))}
        </div>

        <div className="flex gap-2 pt-2 border-t">
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Column name"
            className="flex-1"
          />
          <Input
            value={newAgent}
            onChange={(e) => setNewAgent(e.target.value)}
            placeholder="Default agent (optional)"
            className="w-48"
          />
          <Button onClick={handleCreate} disabled={!newName.trim()}>
            Add
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
