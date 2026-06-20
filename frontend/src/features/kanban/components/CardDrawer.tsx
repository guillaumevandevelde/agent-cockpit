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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { MODAL_SIZES } from "@/lib/constants";
import { useProviderContext } from "@/contexts/ProviderContext";
import { kanbanApi } from "../api";
import { mailApi } from "@/features/mailbox/api";
import type { Message } from "@/features/mailbox/types";
import { CardEditDialog } from "./CardEditDialog";
import { CardRunTab } from "./CardRunTab";
import type { Card, ActivityEntry, KanbanColumn } from "../types";

const AUTO = "__auto__"; // sentinel: agent chosen by column default

export function CardDrawer({
  card,
  projectPath,
  columns,
  onClose,
  onChanged,
}: {
  card: Card;
  projectPath: string;
  columns: KanbanColumn[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const { selectedProviderId } = useProviderContext();
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [mail, setMail] = useState<Message[]>([]);
  const [editing, setEditing] = useState(false);

  // A dispatched agent run is bound to the card via the `agent:<session>` claim.
  const runSession = card.claimed_by?.startsWith("agent:")
    ? card.claimed_by.slice("agent:".length)
    : null;

  useEffect(() => {
    kanbanApi.activity(card.id).then(setActivity).catch(() => {});
    mailApi
      .forCard(card.project_key, card.id)
      .then((r) => setMail(r.messages))
      .catch(() => setMail([]));
  }, [card.id, card.project_key]);

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
      const agent = card.agent || selectedProviderId;
      const r = await kanbanApi.dispatchNow(card.id, projectPath, agent);
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
            <SelectTrigger className="h-8 w-[140px]">
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
            Dispatch
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

        <Tabs defaultValue={runSession ? "run" : "deliverables"}>
          <TabsList>
            <TabsTrigger value="deliverables">Deliverables</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
            <TabsTrigger value="mail">
              Mail{mail.length > 0 ? ` (${mail.length})` : ""}
            </TabsTrigger>
            {runSession && <TabsTrigger value="run">Run</TabsTrigger>}
          </TabsList>

          <TabsContent value="deliverables">
            {card.deliverables.length === 0 && (
              <div className="text-xs text-muted-foreground">None</div>
            )}
            {card.deliverables.map((d) => (
              <div key={d.id} className="text-xs">
                {d.kind}: {d.ref}
              </div>
            ))}
          </TabsContent>

          <TabsContent value="activity">
            {activity.map((e) => (
              <div key={e.hlc} className="text-xs text-muted-foreground">
                {e.op_type} &mdash; {new Date(e.created_at).toLocaleString()}
                {e.op_type === "comment"
                  ? `: ${String(e.payload.text ?? "")}`
                  : ""}
              </div>
            ))}
          </TabsContent>

          <TabsContent value="mail">
            {mail.length === 0 && (
              <div className="text-xs text-muted-foreground">
                No mail references this card.
              </div>
            )}
            {mail.map((m) => (
              <div key={m.id} className="border-b py-2 text-xs last:border-b-0">
                <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
                  <span className="font-medium text-foreground">{m.from_handle}</span>
                  <span>&rarr;</span>
                  <span>{m.to_handle ?? "team"}</span>
                  <span className="rounded bg-muted px-1">{m.kind}</span>
                  <span>{m.status}</span>
                  <span className="ml-auto">
                    {new Date(m.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="mt-1 font-medium">{m.subject}</div>
                {m.body && (
                  <div className="mt-1">
                    <MarkdownRenderer content={m.body} />
                  </div>
                )}
              </div>
            ))}
          </TabsContent>

          {runSession && (
            <TabsContent value="run">
              <CardRunTab sessionName={runSession} projectPath={projectPath} />
            </TabsContent>
          )}
        </Tabs>

        {editing && (
          <CardEditDialog
            open
            initial={{
              title: card.title,
              description: card.description,
              priority: card.priority,
              labels: card.labels,
            }}
            columns={columns.map((c) => c.name)}
            defaultAgent={card.agent}
            onClose={() => setEditing(false)}
            onSubmit={async ({ title, description, priority, labels, agent }) => {
              try {
                await kanbanApi.updateCard(card.id, {
                  title,
                  description,
                  priority,
                  labels: labels.length ? labels : null,
                  agent,
                });
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
