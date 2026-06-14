import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { useProjectContext } from "@/contexts/ProjectContext";
import { Button } from "@/components/ui/button";
import { Board } from "./components/Board";
import { CardDrawer } from "./components/CardDrawer";
import { CardEditDialog } from "./components/CardEditDialog";
import { EnableKanbanToggle } from "./components/EnableKanbanToggle";
import { kanbanApi } from "./api";
import type { Card, Column as Col } from "./types";

export default function KanbanPage() {
  const { activeProject } = useProjectContext();
  const projectPath = activeProject?.path ?? "";
  const [projectKey, setProjectKey] = useState<string>("");
  const [cards, setCards] = useState<Card[]>([]);
  const [open, setOpen] = useState<Card | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    if (!projectKey) return;
    try {
      const { items } = await kanbanApi.listCards(projectKey);
      setCards(items);
      setOpen((prev) =>
        prev ? (items.find((c) => c.id === prev.id) ?? null) : null
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

  const onMove = async (cardId: string, column: Col) => {
    setCards((cs) => cs.map((c) => (c.id === cardId ? { ...c, column } : c)));
    try {
      await kanbanApi.move(cardId, column);
    } catch {
      toast.error("Failed to move card");
    } finally {
      void reload(); // reconcile optimistic state with the server
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
          <Button size="sm" onClick={() => setCreating(true)}>
            New card
          </Button>
        </div>
      </div>

      <Board cards={cards} onOpen={setOpen} onMove={onMove} />

      {open && (
        <CardDrawer card={open} onClose={() => setOpen(null)} onChanged={reload} />
      )}
      {creating && (
        <CardEditDialog
          open
          onClose={() => setCreating(false)}
          onSubmit={async ({ title, description }) => {
            try {
              await kanbanApi.createCard({ project_key: projectKey, title, description });
              setCreating(false);
              void reload();
            } catch {
              toast.error("Failed to create card");
            }
          }}
        />
      )}
    </div>
  );
}
