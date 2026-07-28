import { useCallback, useState } from "react";
import type { Card, KanbanColumn } from "../types";
import { Column, type CardMeta, type SubtaskSummary } from "./Column";

// Explicit per-column collapse choices, keyed by column id. Only choices the
// operator actually made are stored; a column absent from the map falls back to
// the "empty lanes start collapsed" default below, so adding a column to the
// board never needs a storage migration.
const COLLAPSE_STORAGE_KEY = "kanban-collapsed-columns";

function readCollapseOverrides(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(COLLAPSE_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(
        ([, v]) => typeof v === "boolean",
      ),
    ) as Record<string, boolean>;
  } catch {
    // Corrupt/blocked storage must never take the board down with it.
    return {};
  }
}

export function Board({
  columns,
  cards,
  onOpen,
  onDropCardAt,
  onReorderColumns,
  cardMeta,
  subtaskCounts,
  projectPath,
  onPromote,
}: {
  columns: KanbanColumn[];
  cards: Card[];
  onOpen: (c: Card) => void;
  onDropCardAt: (cardId: string, column: string, index: number) => void;
  onReorderColumns?: (sourceId: string, targetId: string) => void;
  cardMeta?: Map<string, CardMeta>;
  subtaskCounts?: Map<string, SubtaskSummary>;
  // Threaded down to Column → CardItem so the Impediment `dispatch_failed`
  // badge can render a Redispatch quick-action.
  projectPath?: string;
  // Inceptie-pipeline entry point — threaded to intake cards so the
  // Promote-to-project button can open the dialog at the page level.
  onPromote?: (c: Card) => void;
}) {
  const [draggedColumn, setDraggedColumn] = useState<string | null>(null);
  const [collapseOverrides, setCollapseOverrides] = useState(readCollapseOverrides);

  const toggleCollapsed = useCallback((columnId: string, currentlyCollapsed: boolean) => {
    setCollapseOverrides((prev) => {
      const next = { ...prev, [columnId]: !currentlyCollapsed };
      try {
        localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // Persisting is a convenience; an in-memory toggle still works.
      }
      return next;
    });
  }, []);

  return (
    <div className="flex gap-3 overflow-x-auto pb-4 flex-1 min-h-0">
      {columns.map((col) => {
        const columnCards = cards.filter((c) => c.column === col.name);
        // Default: a lane with nothing in it starts as a rail. On the real board
        // two of seven lanes are routinely empty (reviewer, analyst) while
        // Backlog overflows vertically — the empty ones were each holding 224px
        // hostage and pushing the busy lanes off-screen. An explicit choice
        // (either direction) always wins over the default.
        const collapsed = collapseOverrides[col.id] ?? columnCards.length === 0;
        return (
          <Column
            key={col.id}
            column={col.name}
            kanbanColumn={col}
            cards={columnCards}
            onOpen={onOpen}
            onDropCardAt={onDropCardAt}
            onDragStartColumn={setDraggedColumn}
            onDropColumn={(targetId) => {
              if (draggedColumn && draggedColumn !== targetId) {
                onReorderColumns?.(draggedColumn, targetId);
              }
              setDraggedColumn(null);
            }}
            cardMeta={cardMeta}
            subtaskCounts={subtaskCounts}
            projectPath={projectPath}
            onPromote={onPromote}
            collapsed={collapsed}
            onToggleCollapsed={() => toggleCollapsed(col.id, collapsed)}
          />
        );
      })}
    </div>
  );
}
