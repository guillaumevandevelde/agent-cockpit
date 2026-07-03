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
import { CardEditDialog } from "./CardEditDialog";
import { CardRunTab } from "./CardRunTab";
import type { Card, ActivityEntry, KanbanColumn, Gate } from "../types";

const GATE_POLL_INTERVAL_MS = 3000;

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
  const { selectedProviderId, providers } = useProviderContext();
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [editing, setEditing] = useState(false);
  const [gates, setGates] = useState<Gate[]>([]);
  const [answering, setAnswering] = useState<string | null>(null);

  // The per-card agent selector picks which connected provider runs the card;
  // `card.agent` holds the provider id (dispatch falls back to the globally
  // selected provider when it's null — the "Auto" option below).
  const installedProviders = providers.filter((p) => p.installed);

  // A dispatched agent run is bound to the card via the `agent:<session>` claim.
  const runSession = card.claimed_by?.startsWith("agent:")
    ? card.claimed_by.slice("agent:".length)
    : null;

  useEffect(() => {
    kanbanApi
      .activity(card.id)
      .then(setActivity)
      .catch(() => toast.error("Failed to load activity log"));
  }, [card.id]);

  // A running session opens a gate via the open_gate MCP tool at any time,
  // independent of anything the UI does — poll so it shows up without a
  // manual refresh, and so the session (blocked waiting for the answer)
  // unblocks as soon as this drawer posts one.
  useEffect(() => {
    let cancelled = false;
    const load = () => {
      kanbanApi
        .listGates(card.id)
        .then((g) => {
          if (!cancelled) setGates(g);
        })
        .catch(() => {});
    };
    load();
    const interval = setInterval(load, GATE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [card.id]);

  const openGates = gates.filter((g) => g.status === "open");

  const answerGate = async (gate: Gate, option: string) => {
    setAnswering(gate.id);
    try {
      const updated = await kanbanApi.answerGate(gate.id, option);
      setGates((prev) => prev.map((g) => (g.id === updated.id ? updated : g)));
    } catch {
      toast.error("Failed to submit answer");
    } finally {
      setAnswering(null);
    }
  };

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

  const redispatchNow = async () => {
    try {
      const agent = card.agent || selectedProviderId;
      const r = await kanbanApi.redispatch(card.id, projectPath, agent);
      toast.success(`Re-dispatched — session ${r.session_name}`);
      onChanged();
    } catch {
      toast.error("Re-dispatch failed — the spawn may have errored");
      onChanged();
    }
  };

  const isClaimedByAgent = card.claimed_by?.startsWith("agent:");
  const isClaimedByHuman = card.claimed_by && !isClaimedByAgent;

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

        {openGates.map((gate) => (
          <div
            key={gate.id}
            className="rounded-md border-2 border-primary/50 bg-primary/5 p-3 text-sm"
          >
            <div className="mb-2 text-xs font-semibold uppercase text-primary">
              Decision requested
            </div>
            <MarkdownRenderer content={gate.question} />
            <div className="mt-3 flex flex-wrap gap-2">
              {gate.options.map((option) => (
                <Button
                  key={option}
                  size="sm"
                  disabled={answering === gate.id}
                  onClick={() => answerGate(gate, option)}
                >
                  {option}
                </Button>
              ))}
            </div>
          </div>
        ))}

        <div className="text-sm">
          <MarkdownRenderer content={card.description || "_No description_"} />
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Select value={card.agent ?? AUTO} onValueChange={setAgent}>
            <SelectTrigger className="h-8 w-[140px]">
              <SelectValue placeholder="Provider" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={AUTO}>Auto (selected provider)</SelectItem>
              {installedProviders.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {isClaimedByAgent ? (
            <Button size="sm" variant="outline" onClick={redispatchNow}>
              Re-dispatch
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={dispatchNow}>
              Dispatch
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            Edit
          </Button>
          {isClaimedByHuman ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => act(() => kanbanApi.release(card.id))}
            >
              Release ({card.claimed_by})
            </Button>
          ) : card.claimed_by ? null : (
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
              column: card.column,
              priority: card.priority,
              labels: card.labels,
              transport: card.transport,
              resume_session_id: card.resume_session_id,
              resume_project_folder: card.resume_project_folder,
            }}
            columns={columns.map((c) => c.name)}
            defaultAgent={card.agent}
            projectPath={projectPath}
            onClose={() => setEditing(false)}
            onSubmit={async ({ title, description, column, priority, labels, agent, transport, resume_session_id, resume_project_folder }) => {
              try {
                await kanbanApi.updateCard(card.id, {
                  title,
                  description,
                  column,
                  priority,
                  labels: labels.length ? labels : null,
                  agent,
                  transport,
                  resume_session_id,
                  resume_project_folder,
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
