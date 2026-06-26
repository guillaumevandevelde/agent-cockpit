import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useProviderContext } from "@/contexts/ProviderContext";
import { Button } from "@/components/ui/button";
import { Board } from "./components/Board";
import { CardDrawer } from "./components/CardDrawer";
import { CardEditDialog } from "./components/CardEditDialog";
import { ColumnSettingsDialog } from "./components/ColumnSettingsDialog";
import { EnableKanbanToggle } from "./components/EnableKanbanToggle";
import { ShipModeToggle } from "./components/ShipModeToggle";
import { SkipPermissionsToggle } from "./components/SkipPermissionsToggle";
import { kanbanApi } from "./api";
import type { Card, KanbanColumn } from "./types";

const FIXED_COLUMNS = new Set(["Backlog", "Impediment", "Done"]);
const DISPATCH_COLUMNS = new Set(["Backlog"]);

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

  const orphanCount = useMemo(
    () => cards.filter((c) => !FIXED_COLUMNS.has(c.column) && !c.claimed_by).length,
    [cards],
  );

  const pendingCount = useMemo(
    () => cards.filter((c) => DISPATCH_COLUMNS.has(c.column) && !c.claimed_by).length,
    [cards],
  );

  const doneCount = useMemo(
    () => cards.filter((c) => c.column === "Done").length,
    [cards],
  );

  const clearDoneColumn = async () => {
    try {
      const r = await kanbanApi.clearColumn(projectKey, "Done");
      toast.success(`Cleared ${r.cleared} card(s) from Done`);
      void reload();
    } catch {
      toast.error("Failed to clear Done column");
    }
  };

  const redispatchAll = async () => {
    try {
      const r = await kanbanApi.redispatchAll(projectPath);
      toast.success(`Re-dispatched ${r.redispatched} orphaned card(s)`);
      void reload();
    } catch {
      toast.error("Re-dispatch all failed");
    }
  };

  const dispatchAll = async () => {
    try {
      const r = await kanbanApi.dispatchAll(projectPath);
      toast.success(`Dispatched ${r.dispatched} card(s)`);
      void reload();
    } catch {
      toast.error("Dispatch all failed");
    }
  };

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
    <div className="flex flex-col h-full gap-4 overflow-hidden">
      <div className="flex items-center justify-between flex-shrink-0">
        <div>
          <h1 className="text-xl font-semibold">Kanban</h1>
          <div className="text-xs text-muted-foreground">{projectKey || "…"}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <EnableKanbanToggle projectPath={projectPath} onChanged={reload} />
          <ShipModeToggle projectKey={projectKey} />
          <SkipPermissionsToggle projectKey={projectKey} />
          <Button size="sm" variant="outline" onClick={() => setEditingColumns(true)}>
            Columns
          </Button>
          {orphanCount > 0 && (
            <Button size="sm" variant="outline" onClick={redispatchAll}>
              Redispatch all ({orphanCount})
            </Button>
          )}
          {pendingCount > 0 && (
            <Button size="sm" variant="outline" onClick={dispatchAll}>
              Dispatch all ({pendingCount})
            </Button>
          )}
          {doneCount > 0 && (
            <Button size="sm" variant="outline" className="text-destructive" onClick={clearDoneColumn}>
              Clear Done ({doneCount})
            </Button>
          )}
          <Button size="sm" onClick={() => setCreating(true)}>
            New card
          </Button>
        </div>
      </div>

      <Board
        columns={columns}
        cards={cards}
        onOpen={setOpen}
        onMove={onMove}
        onReorderColumns={async (sourceId, targetId) => {
          const source = columns.find((c) => c.id === sourceId);
          const target = columns.find((c) => c.id === targetId);
          if (!source || !target) return;

          const newColumns = [...columns];
          const sourceIdx = newColumns.findIndex((c) => c.id === sourceId);
          const targetIdx = newColumns.findIndex((c) => c.id === targetId);

          const [moved] = newColumns.splice(sourceIdx, 1);
          newColumns.splice(targetIdx, 0, moved);

          setColumns(newColumns);

          try {
            for (let i = 0; i < newColumns.length; i++) {
              await kanbanApi.updateColumn(newColumns[i].id, {
                rank: String(i).padStart(4, "0"),
              });
            }
          } catch {
            toast.error("Failed to reorder columns");
            void reload();
          }
        }}
      />

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
          onSubmit={async ({ title, description, column, priority, labels, agent, transport }) => {
            try {
              await kanbanApi.createCard({
                project_key: projectKey,
                title,
                description,
                column,
                priority,
                labels: labels.length ? labels : null,
                agent,
                transport,
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
