import { useState } from "react";
import type { Card, Column as Col, KanbanColumn } from "../types";
import type { ReadyState } from "./ReadyStateBadge";
import { CardItem } from "./CardItem";

export interface CardMeta {
  readyState: ReadyState;
  blockerTitles: string[];
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
  projectPath,
  onPromote,
}: {
  column: Col;
  kanbanColumn?: KanbanColumn;
  cards: Card[];
  onOpen: (c: Card) => void;
  onDropCardAt: (cardId: string, column: Col, index: number) => void;
  onDragStartColumn?: (columnId: string) => void;
  onDropColumn?: (targetColumnId: string) => void;
  cardMeta?: Map<string, CardMeta>;
  // Threaded down to CardItem so the dispatch_failed → Redispatch
  // quick-action can call kanbanApi.redispatch without bouncing through
  // the drawer. Optional for backwards compat with tests that don't care.
  projectPath?: string;
  // Inceptie-pipeline entry point. CardItem renders the Promote button
  // only when this is set AND the card's column is "intake" — keeps the
  // button out of the way on every other column.
  onPromote?: (c: Card) => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  const [dropIndex, setDropIndex] = useState<number | null>(null);

  const clearDrag = () => {
    setDragOver(false);
    setDropIndex(null);
  };

  return (
    <div
      className={`flex-1 min-w-56 bg-muted/40 rounded-lg p-2 transition-colors flex flex-col min-h-0 ${
        dragOver ? "ring-2 ring-primary/50" : ""
      }`}
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
      <div
        draggable={!!kanbanColumn}
        onDragStart={(e) => {
          if (kanbanColumn) {
            e.dataTransfer.setData("text/plain", `column:${kanbanColumn.id}`);
            onDragStartColumn?.(kanbanColumn.id);
          }
        }}
        className="px-1 pb-2 text-xs font-semibold uppercase text-muted-foreground cursor-grab active:cursor-grabbing flex-shrink-0"
      >
        {column} <span className="ml-1">({cards.length})</span>
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
                projectPath={projectPath}
                onPromote={onPromote}
              />
            </div>
          );
        })}
        {dropIndex === cards.length && <div className="h-0.5 bg-primary rounded mb-2" />}
      </div>
    </div>
  );
}
