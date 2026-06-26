import { useState } from "react";
import type { Card, Column as Col, KanbanColumn } from "../types";
import { CardItem } from "./CardItem";

export function Column({
  column,
  kanbanColumn,
  cards,
  onOpen,
  onDropCard,
  onDragStartColumn,
  onDropColumn,
}: {
  column: Col;
  kanbanColumn?: KanbanColumn;
  cards: Card[];
  onOpen: (c: Card) => void;
  onDropCard: (cardId: string, column: Col) => void;
  onDragStartColumn?: (columnId: string) => void;
  onDropColumn?: (targetColumnId: string) => void;
}) {
  const [dragOver, setDragOver] = useState(false);

  return (
    <div
      className={`flex-1 min-w-56 bg-muted/40 rounded-lg p-2 transition-colors flex flex-col min-h-0 ${
        dragOver ? "ring-2 ring-primary/50" : ""
      }`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const data = e.dataTransfer.getData("text/plain");
        if (data.startsWith("column:")) {
          const sourceId = data.replace("column:", "");
          if (sourceId && kanbanColumn && sourceId !== kanbanColumn.id) {
            onDropColumn?.(kanbanColumn.id);
          }
        } else {
          onDropCard(data, column);
        }
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
        {cards.map((c) => (
          <div
            key={c.id}
            draggable
            onDragStart={(e) => e.dataTransfer.setData("text/plain", c.id)}
          >
            <CardItem card={c} onOpen={onOpen} />
          </div>
        ))}
      </div>
    </div>
  );
}
