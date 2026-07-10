import { useEffect, useState } from "react";
import { formatDistanceToNow } from "date-fns";
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
import { Badge } from "@/components/ui/badge";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { MODAL_SIZES } from "@/lib/constants";
import { useProviderContext } from "@/contexts/ProviderContext";
import { kanbanApi } from "../api";
import { CardEditDialog } from "./CardEditDialog";
import { CardRunTab } from "./CardRunTab";
import type { Card, ActivityEntry, Deliverable, Gate } from "../types";

const LIVE_POLL_INTERVAL_MS = 3000;

const AUTO = "__auto__"; // sentinel: agent chosen by column default
const DONE_COLUMN = "Done";

// "Completed on 10 July 2026 at 14:30" — explicit, locale-independent format
// so a screenshot is reproducible across machines.
function formatCompletedAt(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  const time = d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `Completed on ${date} at ${time}`;
}

// "Took 2h 15m" / "Took 45m" / "Took 3d 4h" — coarse, human-friendly.
// Returns null when the duration is zero/negative so the caller can omit the
// "Took ..." row entirely.
function formatDuration(startIso: string, endIso: string): string | null {
  const start = new Date(startIso).getTime();
  const end = new Date(endIso).getTime();
  if (isNaN(start) || isNaN(end) || end <= start) return null;
  const totalMinutes = Math.floor((end - start) / 60_000);
  if (totalMinutes < 1) return null;
  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `Took ${days}d ${hours}h`;
  if (hours > 0) return `Took ${hours}h ${minutes}m`;
  return `Took ${minutes}m`;
}

// "2 hours ago" — short relative timestamp for deliverable rows.
function formatRelativeTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return formatDistanceToNow(d, { addSuffix: true });
}

// Render a single deliverable with kind-specific icon + ref formatting.
function DeliverableRow({ d }: { d: Deliverable }) {
  const created = formatRelativeTime(d.created_at);

  switch (d.kind) {
    case "branch":
      return (
        <div className="text-xs flex flex-wrap items-center gap-2" data-deliverable-kind={d.kind}>
          <span className="font-mono">🔀 {d.ref}</span>
          <span className="text-muted-foreground">· {created}</span>
        </div>
      );
    case "pr": {
      // `ref` may be a full URL or a shorthand like "PR #123" — render it as
      // a clickable link when it parses as http(s), otherwise as plain text.
      const isUrl = /^https?:\/\//i.test(d.ref);
      return (
        <div className="text-xs flex flex-wrap items-center gap-2" data-deliverable-kind={d.kind}>
          <span className="font-mono">🔗</span>
          {isUrl ? (
            <a
              href={d.ref}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline break-all"
            >
              {d.ref}
            </a>
          ) : (
            <span>{d.ref}</span>
          )}
          <span className="text-muted-foreground">· {created}</span>
        </div>
      );
    }
    case "commit":
      return (
        <div className="text-xs flex flex-wrap items-center gap-2" data-deliverable-kind={d.kind}>
          <span className="font-mono">💻 {d.ref.slice(0, 7)}</span>
          <span className="text-muted-foreground">· {created}</span>
        </div>
      );
    case "note":
      return (
        <div className="text-xs flex flex-wrap items-center gap-2" data-deliverable-kind={d.kind}>
          <span className="font-mono">📝</span>
          <span>{d.ref}</span>
          <span className="text-muted-foreground">· {created}</span>
        </div>
      );
    case "plan":
      return (
        <div className="text-xs flex flex-col gap-1" data-deliverable-kind={d.kind}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">📋 Plan document</span>
            <span className="text-muted-foreground">· {created}</span>
          </div>
          <div className="ml-6">
            <MarkdownRenderer content={d.ref} />
          </div>
        </div>
      );
    case "plan_ref": {
      // The `ref` is a JSON payload `{parent_card_id, plan_deliverable_id}`
      // — surface a pointer to the parent plan for context.
      let parentId = "";
      try {
        const parsed = JSON.parse(d.ref) as { parent_card_id?: string };
        parentId = parsed.parent_card_id ?? "";
      } catch {
        // unparseable — fall through with empty parentId
      }
      return (
        <div className="text-xs flex flex-wrap items-center gap-2" data-deliverable-kind={d.kind}>
          <span className="font-mono">🔗</span>
          <span>
            Verwijst naar parent-plan {parentId ? parentId.slice(0, 8) : "unknown"}
          </span>
          <span className="text-muted-foreground">· {created}</span>
        </div>
      );
    }
    case "link":
      return (
        <div className="text-xs flex flex-wrap items-center gap-2" data-deliverable-kind={d.kind}>
          <span className="font-mono">🔗</span>
          <a
            href={d.ref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline break-all"
          >
            {d.ref}
          </a>
          <span className="text-muted-foreground">· {created}</span>
        </div>
      );
  }
}

// Banner shown when a card is in the Done column. The full banner (with
// summary + completed_at + duration) only renders when the backend-supplied
// `done_summary` is non-empty; otherwise a slim "Completed" line keeps the
// status visible without inventing a summary.
function DoneSummaryBanner({ card }: { card: Card }) {
  const summary = (card.done_summary ?? "").trim();
  const completedAt = card.completed_at ?? null;
  const duration =
    summary && completedAt ? formatDuration(card.created_at, completedAt) : null;

  return (
    <div
      className="rounded-md border-2 border-green-500/40 bg-green-50 p-3 text-sm dark:bg-green-950/30"
      data-testid="done-summary-banner"
    >
      <div className="mb-1 text-xs font-semibold uppercase text-green-700 dark:text-green-400">
        ✅ Completed
      </div>
      {summary && (
        <div className="text-foreground whitespace-pre-wrap">{summary}</div>
      )}
      {(completedAt || duration) && (
        <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
          {completedAt && <span>{formatCompletedAt(completedAt)}</span>}
          {duration && <span>{duration}</span>}
        </div>
      )}
    </div>
  );
}

function PlanTabContent({ card }: { card: Card }) {
  // Parent case: a "plan" deliverable carries the markdown directly in `ref`.
  const planDeliverable = card.deliverables.find((d) => d.kind === "plan");
  if (planDeliverable) {
    return <MarkdownRenderer content={planDeliverable.ref} />;
  }

  // Child case: a "plan_ref" deliverable's `ref` is a JSON string with the
  // parent's card id and plan deliverable id.
  const planRef = card.deliverables.find((d) => d.kind === "plan_ref");
  if (planRef) {
    let parsed: { parent_card_id?: string } = {};
    try {
      parsed = JSON.parse(planRef.ref) as { parent_card_id?: string };
    } catch {
      // fall through — treat as missing parent id
    }
    const parentId = parsed.parent_card_id ?? card.parent_card_id ?? null;
    const dependsOn = card.depends_on ?? [];
    return (
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">Parent plan:</span>
          {parentId ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                void kanbanApi
                  .getCard(parentId)
                  .then((parent) => {
                    toast.info(`Open parent "${parent.title}" in the board`);
                  })
                  .catch(() => toast.error("Failed to load parent card"));
              }}
            >
              {parentId.slice(0, 8)}
            </Button>
          ) : (
            <Badge variant="outline">unknown</Badge>
          )}
        </div>
        {dependsOn.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground">Depends on:</span>
            {dependsOn.map((depId) => (
              <Badge key={depId} variant="secondary">
                {depId.slice(0, 8)}
              </Badge>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="text-xs text-muted-foreground">
      Geen plan &mdash; dit is een single-agent kaart of het plan is nog niet opgeslagen.
    </div>
  );
}

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

  // A running session posts activity (comments, moves) and may open a gate via
  // the open_gate MCP tool at any time, independent of anything the UI does —
  // poll both so they show up without the drawer needing to be closed and
  // reopened, and so a session blocked on a gate unblocks as soon as this
  // drawer posts an answer.
  useEffect(() => {
    let cancelled = false;
    const load = () => {
      kanbanApi
        .activity(card.id)
        .then((a) => {
          if (!cancelled) setActivity(a);
        })
        .catch(() => {});
      kanbanApi
        .listGates(card.id)
        .then((g) => {
          if (!cancelled) setGates(g);
        })
        .catch(() => {});
    };
    load();
    const interval = setInterval(load, LIVE_POLL_INTERVAL_MS);
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

  const remove = async (force = false) => {
    try {
      await kanbanApi.deleteCard(card.id, force);
      toast.success("Card deleted");
      onChanged();
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to delete card";
      if (!force && window.confirm(`${message}\n\nDelete anyway?`)) {
        await remove(true);
        return;
      }
      if (force) toast.error("Failed to delete card");
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

        {card.column === DONE_COLUMN && <DoneSummaryBanner card={card} />}

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
                <AlertDialogAction onClick={() => remove()}>Delete</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>

        <Tabs defaultValue={runSession ? "run" : "deliverables"}>
          <TabsList>
            <TabsTrigger value="deliverables">Deliverables</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
            <TabsTrigger value="plan">Plan</TabsTrigger>
            {runSession && <TabsTrigger value="run">Run</TabsTrigger>}
          </TabsList>

          <TabsContent value="deliverables">
            {card.deliverables.length === 0 && (
              <div className="text-xs text-muted-foreground">None</div>
            )}
            <div className="space-y-2">
              {card.deliverables.map((d) => (
                <DeliverableRow key={d.id} d={d} />
              ))}
            </div>
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

          <TabsContent value="plan">
            <PlanTabContent card={card} />
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
              work_type: card.work_type,
              transport: card.transport,
              resume_session_id: card.resume_session_id,
              resume_project_folder: card.resume_project_folder,
              scheduled_at: card.scheduled_at,
              analyst_agent_id: card.analyst_agent_id,
              executor_agent_id: card.executor_agent_id,
            }}
            defaultAgent={card.agent}
            projectPath={projectPath}
            onClose={() => setEditing(false)}
            onSubmit={async ({ title, description, priority, labels, work_type, agent, transport, resume_session_id, resume_project_folder, scheduled_at, analyst_agent_id, executor_agent_id }) => {
              try {
                await kanbanApi.updateCard(card.id, {
                  title,
                  description,
                  priority,
                  labels: labels.length ? labels : null,
                  work_type,
                  agent,
                  transport,
                  resume_session_id,
                  resume_project_folder,
                  scheduled_at,
                  analyst_agent_id,
                  executor_agent_id,
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
