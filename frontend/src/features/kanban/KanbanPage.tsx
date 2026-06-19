import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useProviderContext } from "@/contexts/ProviderContext";
import { Button } from "@/components/ui/button";
import { Board } from "./components/Board";
import { CardDrawer } from "./components/CardDrawer";
import { CardEditDialog } from "./components/CardEditDialog";
import { ColumnSettingsDialog } from "./components/ColumnSettingsDialog";
import { EnableKanbanToggle } from "./components/EnableKanbanToggle";
import { AutodispatchToggle } from "./components/AutodispatchToggle";
import { ShipModeToggle } from "./components/ShipModeToggle";
import { kanbanApi } from "./api";
import type { Card, KanbanColumn } from "./types";

export default function KanbanPage() {
  const { activeProject } = useProjectContext();
  const { selectedProviderId } = useProviderContext();
  const projectPath = activeProject?.path ?? "";
  const [projectKey, setProjectKey] = useState<string>("");
  const [columns, setColumns] = useState<KanbanColumn[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [open, setOpen] = useState<Card | null>(null);
  const [creating, setCreating] = useState(false);
  const [editingColumns, setEditingColumns] = useState(false);

  const reload = useCallback(async () => {
    if (!projectKey) return;
    try {
      const [colRes, cardRes] = await Promise.all([
        kanbanApi.listColumns(projectKey),
        kanbanApi.listCards(projectKey),
      ]);
      setColumns(colRes.columns);
      setCards(cardRes.items);
      setOpen((prev) =>
        prev ? (cardRes.items.find((c) => c.id === prev.id) ?? null) : null
      );
    } catch {
      toast.error("Failed to load board");
    }
  }, [projectKey]);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi.projectKey(projectPath).then((r) => setProjectKey(r.project_key));
  }, [projectPath]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const onMove = async (cardId: string, column: string) => {
    setCards((cs) => cs.map((c) => (c.id === cardId ? { ...c, column } : c)));
    try {
      await kanbanApi.move(cardId, column);
    } catch {
      toast.error("Failed to move card");
    } finally {
      void reload();
    }
  };

  if (!projectPath) return <div className="p-6">Select a project first.</div>;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Kanban</h1>
          <div className="text-xs text-muted-foreground">{projectKey || "…"}</div>
        </div>
        <div className="flex gap-2">
          <EnableKanbanToggle projectPath={projectPath} onChanged={reload} />
          <AutodispatchToggle projectKey={projectKey} />
          <ShipModeToggle projectKey={projectKey} />
          <Button size="sm" variant="outline" onClick={() => setEditingColumns(true)}>
            Columns
          </Button>
          <Button size="sm" onClick={() => setCreating(true)}>
            New card
          </Button>
        </div>
      </div>

      <Board columns={columns} cards={cards} onOpen={setOpen} onMove={onMove} />

      {open && (
        <CardDrawer
          card={open}
          projectPath={projectPath}
          columns={columns}
          onClose={() => setOpen(null)}
          onChanged={reload}
        />
      )}
      {creating && (
        <CardEditDialog
          open
          columns={columns.map((c) => c.name)}
          defaultAgent={selectedProviderId}
          onClose={() => setCreating(false)}
          onSubmit={async ({ title, description, column, priority, labels, agent }) => {
            try {
              await kanbanApi.createCard({
                project_key: projectKey,
                title,
                description,
                column,
                priority,
                labels: labels.length ? labels : null,
                agent,
              });
              setCreating(false);
              void reload();
            } catch {
              toast.error("Failed to create card");
            }
          }}
        />
      )}
      {editingColumns && (
        <ColumnSettingsDialog
          open
          projectKey={projectKey}
          projectPath={projectPath}
          columns={columns}
          onClose={() => setEditingColumns(false)}
          onChanged={reload}
        />
      )}
    </div>
  );
}
