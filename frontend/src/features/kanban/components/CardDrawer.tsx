import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";
import { CardEditDialog } from "./CardEditDialog";
import type { Card, ActivityEntry } from "../types";

const AUTO = "__auto__"; // sentinel: agent chosen by column default

export function CardDrawer({
  card,
  projectPath,
  onClose,
  onChanged,
}: {
  card: Card;
  projectPath: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    kanbanApi.activity(card.id).then(setActivity).catch(() => {});
  }, [card.id]);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi
      .agents(projectPath)
      .then((r) => setAgents(r.agents))
      .catch(() => setAgents([]));
  }, [projectPath]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      onChanged();
    } catch {
      // e.g. a 409 when the card was already claimed elsewhere
      toast.error("Action failed — the card may have changed; reloading");
      onChanged();
    }
  };

  const setAgent = async (value: string) => {
    const agent = value === AUTO ? null : value;
    try {
      await kanbanApi.updateCard(card.id, { agent });
      onChanged();
    } catch {
      toast.error("Failed to set agent");
    }
  };

  const dispatchNow = async () => {
    try {
      const r = await kanbanApi.dispatchNow(card.id, projectPath);
      toast.success(`Dispatched — session ${r.session_name}`);
      onChanged();
    } catch {
      toast.error("Dispatch failed — card may be claimed or the spawn errored");
      onChanged();
    }
  };

  const remove = async () => {
    try {
      await kanbanApi.deleteCard(card.id);
      toast.success("Card deleted");
      onChanged();
      onClose();
    } catch {
      toast.error("Failed to delete card");
    }
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

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Select value={card.agent ?? AUTO} onValueChange={setAgent}>
            <SelectTrigger className="h-8 w-[200px]">
              <SelectValue placeholder="Agent" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={AUTO}>Auto (by column)</SelectItem>
              {agents.map((a) => (
                <SelectItem key={a} value={a}>
                  {a}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" onClick={dispatchNow}>
            Dispatch now
          </Button>
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            Edit
          </Button>
          {card.claimed_by ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => act(() => kanbanApi.release(card.id))}
            >
              Release ({card.claimed_by})
            </Button>
          ) : (
            <Button size="sm" onClick={() => act(() => kanbanApi.claim(card.id, "me@ui"))}>
              Claim
            </Button>
          )}
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" variant="destructive">
                Delete
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete this card?</AlertDialogTitle>
                <AlertDialogDescription>
                  &ldquo;{card.title}&rdquo; and its deliverables will be permanently
                  removed. This cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={remove}>Delete</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
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

        {editing && (
          <CardEditDialog
            open
            projectPath={projectPath}
            initial={{
              title: card.title,
              description: card.description,
              agent: card.agent,
            }}
            onClose={() => setEditing(false)}
            onSubmit={async ({ title, description, agent }) => {
              try {
                await kanbanApi.updateCard(card.id, { title, description, agent });
                setEditing(false);
                onChanged();
              } catch {
                toast.error("Failed to update card");
              }
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
