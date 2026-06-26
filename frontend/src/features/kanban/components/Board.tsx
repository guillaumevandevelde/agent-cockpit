import { useState } from "react";
import type { Card, KanbanColumn } from "../types";
import { Column } from "./Column";

export function Board({
  columns,
  cards,
  onOpen,
  onMove,
  onReorderColumns,
}: {
  columns: KanbanColumn[];
  cards: Card[];
  onOpen: (c: Card) => void;
  onMove: (cardId: string, column: string) => void;
  onReorderColumns?: (sourceId: string, targetId: string) => void;
}) {
  const [draggedColumn, setDraggedColumn] = useState<string | null>(null);

  return (
    <div className="flex gap-3 overflow-x-auto pb-4 flex-1 min-h-0">
      {columns.map((col) => (
        <Column
          key={col.id}
          column={col.name}
          kanbanColumn={col}
          cards={cards.filter((c) => c.column === col.name)}
          onOpen={onOpen}
          onDropCard={onMove}
          onDragStartColumn={setDraggedColumn}
          onDropColumn={(targetId) => {
            if (draggedColumn && draggedColumn !== targetId) {
              onReorderColumns?.(draggedColumn, targetId);
            }
            setDraggedColumn(null);
          }}
        />
      ))}
    </div>
  );
}
