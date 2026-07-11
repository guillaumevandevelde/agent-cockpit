import { useState } from "react";
import type { Card, KanbanColumn } from "../types";
import { Column, type CardMeta } from "./Column";

export function Board({
  columns,
  cards,
  onOpen,
  onDropCardAt,
  onReorderColumns,
  cardMeta,
  projectPath,
}: {
  columns: KanbanColumn[];
  cards: Card[];
  onOpen: (c: Card) => void;
  onDropCardAt: (cardId: string, column: string, index: number) => void;
  onReorderColumns?: (sourceId: string, targetId: string) => void;
  cardMeta?: Map<string, CardMeta>;
  // Threaded down to Column → CardItem so the Impediment `dispatch_failed`
  // badge can render a Redispatch quick-action.
  projectPath?: string;
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
          onDropCardAt={onDropCardAt}
          onDragStartColumn={setDraggedColumn}
          onDropColumn={(targetId) => {
            if (draggedColumn && draggedColumn !== targetId) {
              onReorderColumns?.(draggedColumn, targetId);
            }
            setDraggedColumn(null);
          }}
          cardMeta={cardMeta}
          projectPath={projectPath}
        />
      ))}
    </div>
  );
}
