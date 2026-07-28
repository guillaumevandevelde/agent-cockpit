import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { Card, Column as Col, KanbanColumn } from "../types";
import type { ReadyState } from "./ReadyStateBadge";
import { CardItem } from "./CardItem";

export interface CardMeta {
  readyState: ReadyState;
  blockerTitles: string[];
  // Dangling `depends_on` ids (deps whose card no longer exists). Populated
  // only for `readyState === "missing_dep"`; drives the badge tooltip.
  missingDepIds?: string[];
  // Operator-set `metadata.gated_on` string. Populated only for
  // `readyState === "gated"`; drives the badge tooltip so the operator can
  // see WHAT the card is waiting on, not just that it is.
  gatedOn?: string;
  // ISO timestamp of when the current hold started (backend `held_since`).
  // Drives the "(held 5d)" suffix in the badge tooltip — the one signal that
  // separates a healthy temporary wait from a dead one.
  heldSince?: string;
}

// Per-parent subtask rollup (done/total counts among cards whose
// `parent_card_id` matches the parent). Threaded down to CardItem for the
// compact "N/M subtasks" counter — kanban card 81797046.
export interface SubtaskSummary {
  done: number;
  total: number;
}

export function Column({
  column,
  kanbanColumn,
  cards,
  onOpen,
  onDropCardAt,
  onDragStartColumn,
  onDropColumn,
  cardMeta,
  subtaskCounts,
  projectPath,
  onPromote,
  collapsed = false,
  onToggleCollapsed,
}: {
  column: Col;
  kanbanColumn?: KanbanColumn;
  cards: Card[];
  onOpen: (c: Card) => void;
  onDropCardAt: (cardId: string, column: Col, index: number) => void;
  onDragStartColumn?: (columnId: string) => void;
  onDropColumn?: (targetColumnId: string) => void;
  cardMeta?: Map<string, CardMeta>;
  subtaskCounts?: Map<string, SubtaskSummary>;
  // Threaded down to CardItem so the dispatch_failed → Redispatch
  // quick-action can call kanbanApi.redispatch without bouncing through
  // the drawer. Optional for backwards compat with tests that don't care.
  projectPath?: string;
  // Inceptie-pipeline entry point. CardItem renders the Promote button
  // only when this is set AND the card's column is "intake" — keeps the
  // button out of the way on every other column.
  onPromote?: (c: Card) => void;
  // Rail mode: the lane shrinks to a ~40px vertical strip that still shows the
  // name + count and still accepts card drops. Seven full-width lanes never fit
  // a laptop viewport (7 × 224px + gaps = 1640px), so the lanes an operator is
  // not using right now give their width back to the ones they are.
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  const [dropIndex, setDropIndex] = useState<number | null>(null);

  const clearDrag = () => {
    setDragOver(false);
    setDropIndex(null);
  };

  return (
    <div
      className={`${
        collapsed
          ? "w-10 shrink-0 cursor-pointer"
          // Expanded lanes split the board's width evenly (`flex-1` is
          // `flex: 1 1 0%`), floored at `min-w-52` = 208px. So a lane is as wide
          // as there is room for — 293px with five lanes at 1920x1080, 208px
          // once the lanes would otherwise be squeezed thinner than that — and
          // only below that floor does the board scroll sideways. The previous
          // fixed `min-w-64` (256px) put that floor above what a laptop viewport
          // can hold, which is why the board scrolled horizontally at every
          // realistic width.
          : "flex-1 min-w-52"
      } bg-muted/40 rounded-lg p-2 transition-colors flex flex-col min-h-0 ${
        dragOver ? "ring-2 ring-primary/50" : ""
      }`}
      data-testid={`kanban-column-${column}`}
      data-collapsed={collapsed ? "true" : "false"}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) clearDrag();
      }}
      onDrop={(e) => {
        e.preventDefault();
        const data = e.dataTransfer.getData("text/plain");
        if (data.startsWith("column:")) {
          const sourceId = data.replace("column:", "");
          if (sourceId && kanbanColumn && sourceId !== kanbanColumn.id) {
            onDropColumn?.(kanbanColumn.id);
          }
        } else if (data) {
          onDropCardAt(data, column, dropIndex ?? cards.length);
        }
        clearDrag();
      }}
    >
      {collapsed ? (
        // Rail: vertical name + count, click anywhere to reopen. The lane keeps
        // its drop handlers (see the outer div) so dragging a card onto a closed
        // lane still moves it there.
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="flex flex-1 min-h-0 w-full flex-col items-center gap-2 rounded text-xs font-semibold uppercase text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          data-testid={`kanban-column-expand-${column}`}
          title={`${column} (${cards.length}) — klik om uit te klappen`}
        >
          <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span className="shrink-0 tabular-nums">{cards.length}</span>
          <span className="[writing-mode:vertical-rl] truncate">{column}</span>
        </button>
      ) : (
        <>
      <div
        draggable={!!kanbanColumn}
        onDragStart={(e) => {
          if (kanbanColumn) {
            e.dataTransfer.setData("text/plain", `column:${kanbanColumn.id}`);
            onDragStartColumn?.(kanbanColumn.id);
          }
        }}
        className="flex items-center gap-1 px-1 pb-2 text-xs font-semibold uppercase text-muted-foreground cursor-grab active:cursor-grabbing flex-shrink-0"
      >
        {/* The name truncates, the count never does — a header reading
            "AWAITING SUBTASKS (1…" is worse than a truncated name. */}
        <span className="min-w-0 truncate" title={column}>
          {column}
        </span>
        <span className="shrink-0 tabular-nums">({cards.length})</span>
        <span className="flex-1" />
        {onToggleCollapsed && (
          <button
            type="button"
            onClick={(e) => {
              // The header is a drag handle for column reordering; a click on
              // the collapse chevron must not start or be read as that drag.
              e.stopPropagation();
              onToggleCollapsed();
            }}
            className="shrink-0 rounded p-0.5 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            data-testid={`kanban-column-collapse-${column}`}
            title={`${column} inklappen`}
            aria-label={`${column} inklappen`}
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
      </div>
      <div className="overflow-y-auto flex-1 min-h-0">
        {cards.map((c, i) => {
          const meta = cardMeta?.get(c.id);
          return (
            <div
              key={c.id}
              draggable
              onDragStart={(e) => e.dataTransfer.setData("text/plain", c.id)}
              onDragOver={(e) => {
                e.preventDefault();
                const rect = e.currentTarget.getBoundingClientRect();
                const after = e.clientY - rect.top > rect.height / 2;
                setDropIndex(after ? i + 1 : i);
              }}
            >
              {dropIndex === i && <div className="h-0.5 bg-primary rounded mb-2" />}
              <CardItem
                card={c}
                onOpen={onOpen}
                readyState={meta?.readyState}
                blockerTitles={meta?.blockerTitles}
                missingDepIds={meta?.missingDepIds}
                gatedOn={meta?.gatedOn}
                heldSince={meta?.heldSince}
                subtasks={subtaskCounts?.get(c.id)}
                projectPath={projectPath}
                onPromote={onPromote}
              />
            </div>
          );
        })}
        {dropIndex === cards.length && <div className="h-0.5 bg-primary rounded mb-2" />}
      </div>
        </>
      )}
    </div>
  );
}
