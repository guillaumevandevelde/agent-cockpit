import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";
import type { Card, ActivityEntry } from "../types";

export function CardDrawer({
  card,
  onClose,
  onChanged,
}: {
  card: Card;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [activity, setActivity] = useState<ActivityEntry[]>([]);

  useEffect(() => {
    kanbanApi.activity(card.id).then(setActivity).catch(() => {});
  }, [card.id]);

  const act = async (fn: () => Promise<unknown>) => {
    await fn();
    onChanged();
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.LG}>
        <DialogHeader>
          <DialogTitle>{card.title}</DialogTitle>
        </DialogHeader>

        <div className="text-sm">
          <MarkdownRenderer content={card.description || "_No description_"} />
        </div>

        <div className="flex items-center gap-2 text-xs">
          {card.claimed_by ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => act(() => kanbanApi.release(card.id))}
            >
              Release ({card.claimed_by})
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => act(() => kanbanApi.claim(card.id, "me@ui"))}
            >
              Claim
            </Button>
          )}
        </div>

        <div>
          <div className="text-xs font-semibold mb-1">Deliverables</div>
          {card.deliverables.length === 0 && (
            <div className="text-xs text-muted-foreground">None</div>
          )}
          {card.deliverables.map((d) => (
            <div key={d.id} className="text-xs">
              {d.kind}: {d.ref}
            </div>
          ))}
        </div>

        <div>
          <div className="text-xs font-semibold mb-1">Activity</div>
          {activity.map((e) => (
            <div key={e.hlc} className="text-xs text-muted-foreground">
              {e.op_type} &mdash; {new Date(e.created_at).toLocaleString()}
              {e.op_type === "comment"
                ? `: ${String(e.payload.text ?? "")}`
                : ""}
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
