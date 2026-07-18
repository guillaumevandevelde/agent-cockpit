import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useProviderContext } from "@/contexts/ProviderContext";
import { Button } from "@/components/ui/button";
import { Board } from "./components/Board";
import type { CardMeta } from "./components/Column";
import { CardDrawer } from "./components/CardDrawer";
import { CardEditDialog } from "./components/CardEditDialog";
import { ColumnSettingsDialog } from "./components/ColumnSettingsDialog";
import { EnableKanbanToggle } from "./components/EnableKanbanToggle";
import { McpHealthBadge } from "./components/McpHealthBadge";
import { ShipModeToggle } from "./components/ShipModeToggle";
import { SkipPermissionsToggle } from "./components/SkipPermissionsToggle";
import { AutodispatchToggle } from "./components/AutodispatchToggle";
import { DefaultTransportSelect } from "./components/DefaultTransportSelect";
import { SubscriptionToolbarButton } from "./components/SubscriptionToolbarButton";
import { DispatchPauseBanner } from "./components/DispatchPauseBanner";
import { WorkTypeMappingDialog } from "./components/WorkTypeMappingDialog";
import { PromoteToProjectDialog } from "./components/PromoteToProjectDialog";
import { kanbanApi } from "./api";
import type { Card, KanbanColumn } from "./types";

const FIXED_COLUMNS = new Set(["intake", "Backlog", "Impediment", "Awaiting Subtasks", "Done", "To Resume"]);
const DISPATCH_COLUMNS = new Set(["Backlog", "To Resume"]);
const POLL_INTERVAL_MS = 5000;
const AGENT_CLAIM_PREFIX = "agent:";

export default function KanbanPage() {
  const { activeProject } = useProjectContext();
  const { selectedProviderId } = useProviderContext();
  const projectPath = activeProject?.path ?? "";
  const [projectKey, setProjectKey] = useState<string>("");
  const [columns, setColumns] = useState<KanbanColumn[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [cardsLoaded, setCardsLoaded] = useState(false);
  const [open, setOpen] = useState<Card | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [editingColumns, setEditingColumns] = useState(false);
  const [editingWorkTypeMappings, setEditingWorkTypeMappings] = useState(false);
  // Inceptie-pipeline (kanban card c33b2f14): the intake card currently
  // selected for promotion. Set by `onPromote` (passed down to CardItem
  // via Board → Column); the dialog reads it and renders a confirmation.
  const [promotingCard, setPromotingCard] = useState<Card | null>(null);
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
      setCardsLoaded(true);
      setOpen((prev) => {
        if (!prev) return null;
        // A card opened via `?card=` deep-link (card-references-analysis
        // §2.4/§D2) can belong to a different project than the one this
        // poll just reloaded — leave it alone rather than treating "not in
        // this project's list" as "card was deleted".
        if (prev.project_key && prev.project_key !== projectKey) return prev;
        return cardRes.items.find((c) => c.id === prev.id) ?? null;
      });
    } catch {
      toast.error("Failed to load board");
    }
  }, [projectKey]);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi.projectKey(projectPath).then((r) => setProjectKey(r.project_key));
  }, [projectPath]);

  // Deep-link (`?card=<id>`) — card-references-analysis §2.4/§D2. Opening a
  // card always pushes a history entry so browser-back closes the drawer
  // instead of leaving the kanban page; closing replaces the entry so it
  // doesn't leave a forward-navigation ghost.
  const openCard = useCallback(
    (card: Card) => {
      setOpen(card);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("card", card.id);
          return next;
        },
        { replace: false }
      );
    },
    [setSearchParams]
  );

  const closeCard = useCallback(() => {
    setOpen(null);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("card");
        return next;
      },
      { replace: true }
    );
  }, [setSearchParams]);

  // Reconciles the URL -> drawer state whenever it changes for a reason
  // other than `openCard`/`closeCard` themselves: initial page load with a
  // `?card=` param, browser back/forward, or a pasted/edited URL. The board
  // only ever loads one project's cards at a time, so a card from another
  // project falls back to the project-agnostic `getCard` lookup (AC3). An
  // id that resolves to neither is reported and the param is cleared (AC4)
  // rather than left as a silent no-op.
  useEffect(() => {
    const cardParam = searchParams.get("card");
    if (cardParam === (open?.id ?? null)) return;

    if (!cardParam) {
      setOpen(null);
      return;
    }

    const local = cards.find((c) => c.id === cardParam);
    if (local) {
      setOpen(local);
      return;
    }
    // Wait for the current project's own list before falling back to the
    // project-agnostic lookup, so a same-project card isn't fetched twice.
    if (!cardsLoaded) return;

    let cancelled = false;
    kanbanApi
      .getCard(cardParam)
      .then((card) => {
        if (!cancelled) setOpen(card);
      })
      .catch(() => {
        if (cancelled) return;
        toast.error(`Card ${cardParam.slice(0, 8)}… not found`);
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.delete("card");
            return next;
          },
          { replace: true }
        );
      });
    return () => {
      cancelled = true;
    };
  }, [searchParams, cards, cardsLoaded, open, setSearchParams]);

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
    const handleVisibility = () => {
      if (!document.hidden && !draggingRef.current && mutatingRef.current === 0) {
        void reload();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
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

  // Per-card operational state for the ReadyStateBadge. Mirrors the backend
  // `meets_dep_prerequisites` semantic so the UI badge and the dispatcher's
  // own dep check agree: a card is "ready" iff every entry in `depends_on`
  // is present and Done. Precedence, highest first: completed (column ===
  // "Done") → impeded (column === "Impediment") → dependent (parked in
  // "Awaiting Subtasks", waiting on its own children) → in_progress
  // (claimed_by starts with "agent:") → dependent (open depends_on) →
  // ready. The column-based terminal/parked states win over the claim so a
  // card with a stale claim sitting in Done/Impediment/Awaiting Subtasks
  // doesn't show as in_progress (analyse-levenscyclus-decision.md §3/§5).
  const cardMeta = useMemo(() => {
    const cardsById = new Map(cards.map((c) => [c.id, c]));
    const meta = new Map<string, CardMeta>();
    for (const card of cards) {
      if (card.column === "Done") {
        meta.set(card.id, { readyState: "completed", blockerTitles: [] });
        continue;
      }
      if (card.column === "Impediment") {
        meta.set(card.id, { readyState: "impeded", blockerTitles: [] });
        continue;
      }
      if (card.column === "Awaiting Subtasks") {
        // Parked parent (analyse-levenscyclus-decision.md §3/§5): waiting
        // on its own children, not on `depends_on` — same "dependent"
        // state, different blocker source.
        const pendingChildren = cards.filter(
          (c) => c.parent_card_id === card.id && c.column !== "Done",
        );
        meta.set(card.id, {
          readyState: "dependent",
          blockerTitles: pendingChildren.map((c) => c.title),
        });
        continue;
      }
      if (card.claimed_by?.startsWith(AGENT_CLAIM_PREFIX)) {
        meta.set(card.id, { readyState: "in_progress", blockerTitles: [] });
        continue;
      }
      const deps = card.depends_on ?? [];
      const blockerTitles: string[] = [];
      for (const depId of deps) {
        const parent = cardsById.get(depId);
        if (!parent || parent.column !== "Done") {
          blockerTitles.push(parent?.title ?? "(missing)");
        }
      }
      meta.set(card.id, {
        readyState: blockerTitles.length === 0 ? "ready" : "dependent",
        blockerTitles,
      });
    }
    return meta;
  }, [cards]);

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
          <DefaultTransportSelect projectKey={projectKey} />
          <SubscriptionToolbarButton projectKey={projectKey} />
          <Button size="sm" variant="outline" onClick={() => setEditingColumns(true)}>
            Columns
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setEditingWorkTypeMappings(true)}
          >
            Work Types
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
        cardMeta={cardMeta}
        onOpen={openCard}
        onDropCardAt={onDropCardAt}
        projectPath={projectPath}
        onPromote={setPromotingCard}
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
          onClose={closeCard}
          onChanged={reload}
        />
      )}
      {creating && (
        <CardEditDialog
          open
          defaultAgent={selectedProviderId}
          projectKey={projectKey}
          projectPath={projectPath}
          onClose={() => setCreating(false)}
          onSubmit={async ({ title, description, priority, labels, work_type, agent, model, column_overrides, transport, resume_session_id, resume_project_folder, scheduled_at, analyst_agent_id, executor_agent_id }) => {
            try {
              await kanbanApi.createCard({
                project_key: projectKey,
                title,
                description,
                priority,
                labels: labels.length ? labels : null,
                work_type,
                agent,
                model,
                column_overrides,
                transport,
                resume_session_id,
                resume_project_folder,
                scheduled_at,
                analyst_agent_id,
                executor_agent_id,
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
      {editingWorkTypeMappings && (
        <WorkTypeMappingDialog
          open
          projectKey={projectKey}
          onClose={() => setEditingWorkTypeMappings(false)}
          onChanged={reload}
        />
      )}
      {promotingCard && (
        <PromoteToProjectDialog
          open
          intakeCardId={promotingCard.id}
          intakeCardTitle={promotingCard.title}
          defaultTargetPath={(() => {
            // Suggest `<parent-of-active-project>/<slug-from-title>`. We don't
            // know the actual parent's path here without an extra API call —
            // use the active project path itself as a sensible fallback; the
            // operator can edit before confirming.
            const base = activeProject?.path ?? "/tmp";
            return `${base}/${promotingCard.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
          })()}
          onClose={() => setPromotingCard(null)}
          onPromoted={() => void reload()}
        />
      )}
    </div>
  );
}
