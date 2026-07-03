import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useProviderContext } from "@/contexts/ProviderContext";
import { Button } from "@/components/ui/button";
import { Board } from "./components/Board";
import { CardDrawer } from "./components/CardDrawer";
import { CardEditDialog } from "./components/CardEditDialog";
import { ColumnSettingsDialog } from "./components/ColumnSettingsDialog";
import { EnableKanbanToggle } from "./components/EnableKanbanToggle";
import { McpHealthBadge } from "./components/McpHealthBadge";
import { ShipModeToggle } from "./components/ShipModeToggle";
import { SkipPermissionsToggle } from "./components/SkipPermissionsToggle";
import { AutodispatchToggle } from "./components/AutodispatchToggle";
import { MaxSessionsControl } from "./components/MaxSessionsControl";
import { DefaultTransportSelect } from "./components/DefaultTransportSelect";
import { DispatchPauseBanner } from "./components/DispatchPauseBanner";
import { kanbanApi } from "./api";
import type { Card, KanbanColumn } from "./types";

const FIXED_COLUMNS = new Set(["Backlog", "Impediment", "Done", "To Resume"]);
const DISPATCH_COLUMNS = new Set(["Backlog", "To Resume"]);
const POLL_INTERVAL_MS = 5000;

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
  const draggingRef = useRef(false);
  const mutatingRef = useRef(0);

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

  useEffect(() => {
    const start = () => {
      draggingRef.current = true;
    };
    const end = () => {
      draggingRef.current = false;
    };
    document.addEventListener("dragstart", start);
    document.addEventListener("dragend", end);
    document.addEventListener("drop", end);
    return () => {
      document.removeEventListener("dragstart", start);
      document.removeEventListener("dragend", end);
      document.removeEventListener("drop", end);
    };
  }, []);

  useEffect(() => {
    if (!projectKey) return;
    const id = setInterval(() => {
      if (document.hidden || draggingRef.current || mutatingRef.current > 0) return;
      void reload();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [projectKey, reload]);

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
    const card = cards.find((c) => c.id === cardId);
    const shouldDispatch =
      (card?.column === "Backlog" || card?.column === "To Resume") &&
      !FIXED_COLUMNS.has(column) &&
      !card.claimed_by?.startsWith("agent:");

    mutatingRef.current += 1;
    setCards((cs) => cs.map((c) => (c.id === cardId ? { ...c, column } : c)));
    try {
      try {
        await kanbanApi.move(cardId, column);
      } catch {
        toast.error("Failed to move card");
        void reload();
        return;
      }

      if (shouldDispatch && card) {
        try {
          const agent = card.agent ?? selectedProviderId ?? undefined;
          const r = await kanbanApi.dispatchNow(cardId, projectPath, agent);
          toast.success(`Dispatched — session ${r.session_name}`);
        } catch {
          toast.error("Dispatch failed — card may be claimed or the spawn errored");
        }
      }

      void reload();
    } finally {
      mutatingRef.current -= 1;
    }
  };

  const reorderWithin = async (cardId: string, column: string, index: number) => {
    const colCards = cards.filter((c) => c.column === column);
    const oldIndex = colCards.findIndex((c) => c.id === cardId);
    if (oldIndex === -1) return;

    const without = colCards.filter((c) => c.id !== cardId);
    const insertAt = index > oldIndex ? index - 1 : index;
    without.splice(insertAt, 0, colCards[oldIndex]);
    const orderedIds = without.map((c) => c.id);
    if (orderedIds.every((id, i) => id === colCards[i].id)) return;

    const width = Math.max(4, String(orderedIds.length).length);
    const rankOf = new Map(orderedIds.map((id, i) => [id, String(i).padStart(width, "0")]));
    mutatingRef.current += 1;
    setCards((cs) =>
      [...cs.map((c) => (rankOf.has(c.id) ? { ...c, rank: rankOf.get(c.id)! } : c))].sort(
        (a, b) => (a.rank < b.rank ? -1 : a.rank > b.rank ? 1 : 0),
      ),
    );
    try {
      await kanbanApi.reorder(projectKey, column, orderedIds);
    } catch {
      toast.error("Failed to reorder");
      void reload();
    } finally {
      mutatingRef.current -= 1;
    }
  };

  const onDropCardAt = (cardId: string, column: string, index: number) => {
    const card = cards.find((c) => c.id === cardId);
    if (!card) return;
    if (card.column === column) {
      void reorderWithin(cardId, column, index);
    } else {
      void onMove(cardId, column);
    }
  };

  if (!projectPath) return <div className="p-6">Select a project first.</div>;

  return (
    <div className="flex flex-col h-full gap-4 overflow-hidden">
      <DispatchPauseBanner />
      <div className="flex items-center justify-between flex-shrink-0">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold">Kanban</h1>
            <McpHealthBadge />
          </div>
          <div className="text-xs text-muted-foreground">{projectKey || "…"}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <EnableKanbanToggle projectPath={projectPath} onChanged={reload} />
          <ShipModeToggle projectKey={projectKey} />
          <SkipPermissionsToggle projectKey={projectKey} />
          <AutodispatchToggle projectKey={projectKey} />
          <MaxSessionsControl projectKey={projectKey} />
          <DefaultTransportSelect projectKey={projectKey} />
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
        onDropCardAt={onDropCardAt}
        onReorderColumns={async (sourceId, targetId) => {
          const source = columns.find((c) => c.id === sourceId);
          const target = columns.find((c) => c.id === targetId);
          if (!source || !target) return;

          const newColumns = [...columns];
          const sourceIdx = newColumns.findIndex((c) => c.id === sourceId);
          const targetIdx = newColumns.findIndex((c) => c.id === targetId);

          const [moved] = newColumns.splice(sourceIdx, 1);
          newColumns.splice(targetIdx, 0, moved);

          mutatingRef.current += 1;
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
          } finally {
            mutatingRef.current -= 1;
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
          projectPath={projectPath}
          onClose={() => setCreating(false)}
          onSubmit={async ({ title, description, column, priority, labels, agent, transport, resume_session_id, resume_project_folder }) => {
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
                resume_session_id,
                resume_project_folder,
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
