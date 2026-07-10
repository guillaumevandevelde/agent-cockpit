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
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { MarkdownPreviewToggle } from "@/components/shared/MarkdownPreviewToggle";
import { MODAL_SIZES } from "@/lib/constants";
import { useProviderContext } from "@/contexts/ProviderContext";
import { kanbanApi } from "../api";
import { CardEditDialog } from "./CardEditDialog";
import { CardRunTab } from "./CardRunTab";
import type { Card, ActivityEntry, Deliverable, Gate } from "../types";

const LIVE_POLL_INTERVAL_MS = 3000;

const AUTO = "__auto__"; // sentinel: agent chosen by column default
const DONE_COLUMN = "Done";
const IMPEDIMENT_COLUMN = "Impediment";

// Prefix on the audit-trail comment that a review request posts on the
// original card. Deliberately distinct from `_DONE_SUMMARY_PREFIX` in the
// backend `service.py` so `enrich_done_info` never mistakes it for the Done
// summary. The UI also scans the polled activity for this prefix to render the
// "already requested" state instead of a fresh input.
const REVIEW_REQUEST_PREFIX = "**Review requested:** ";

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

// "Request review" control shown under the Done banner. Lets a human flag a
// doubt on a completed card: the note is posted as a `**Review requested:**`
// comment on this card and a new analysis card is spun up (backend). To avoid
// piling up duplicate requests, if the polled `activity` already contains a
// matching comment we render the note that was sent in a disabled state
// instead of a fresh input.
function RequestReviewControl({
  card,
  activity,
  onChanged,
}: {
  card: Card;
  activity: ActivityEntry[];
  onChanged: () => void;
}) {
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const existing = activity.find(
    (e) =>
      e.op_type === "comment" &&
      typeof e.payload.text === "string" &&
      (e.payload.text as string).startsWith(REVIEW_REQUEST_PREFIX),
  );

  if (existing) {
    const sentNote = (existing.payload.text as string).slice(
      REVIEW_REQUEST_PREFIX.length,
    );
    return (
      <div
        className="rounded-md border-2 border-amber-500/40 bg-amber-50 p-3 text-sm dark:bg-amber-950/30"
        data-testid="review-requested-state"
      >
        <div className="mb-1 text-xs font-semibold uppercase text-amber-700 dark:text-amber-400">
          Review requested
        </div>
        <div className="text-foreground whitespace-pre-wrap">{sentNote}</div>
      </div>
    );
  }

  const submit = async () => {
    const trimmed = note.trim();
    if (!trimmed) return;
    setSubmitting(true);
    try {
      const reviewCard = await kanbanApi.requestReview(card.id, trimmed);
      toast.success(`Review card created — ${reviewCard.id}`);
      setNote("");
      onChanged();
    } catch {
      toast.error("Failed to request review");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="rounded-md border p-3 text-sm space-y-2"
      data-testid="request-review-control"
    >
      <div className="text-xs font-semibold uppercase text-muted-foreground">
        Request review
      </div>
      <Textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Describe your doubt about this implementation…"
        disabled={submitting}
        data-testid="request-review-note"
      />
      <div className="flex justify-end">
        <Button
          size="sm"
          onClick={submit}
          disabled={submitting || !note.trim()}
          data-testid="request-review-submit"
        >
          {submitting ? "Requesting…" : "Request review"}
        </Button>
      </div>
    </div>
  );
}

// "Heropen met feedback" control shown under the Done banner. Lets a human
// post a rebuttal on a completed decision: the note is posted as a
// `**Revisit:**` comment on this card and the *same* card is moved back
// to Backlog (reopen) — distinct from RequestReviewControl which spawns
// a sibling analysis card. The dispatcher then re-picks the card and
// injects the rebuttal into the spawned session's prompt via the
// `## REVISIT` section + a pointer to the prior decision's summary and
// deliverables. Like the review control, the textarea + submit are the
// only UI for this — the activity feed stays read-only.
function ReopenControl({
  card,
  onChanged,
}: {
  card: Card;
  onChanged: () => void;
}) {
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const trimmed = note.trim();
    if (!trimmed) return;
    setSubmitting(true);
    try {
      const reopened = await kanbanApi.reopen(card.id, trimmed);
      toast.success(`Heropend — kaart terug in ${reopened.column}`);
      setNote("");
      onChanged();
    } catch {
      toast.error("Heropen mislukt");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="rounded-md border p-3 text-sm space-y-2"
      data-testid="reopen-control"
    >
      <div className="text-xs font-semibold uppercase text-muted-foreground">
        Heropen met feedback
      </div>
      <Textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Weerleg deze beslissing — de kaart gaat terug naar Backlog en een nieuwe sessie pakt hem op met jouw tegengewicht in de prompt."
        disabled={submitting}
        data-testid="reopen-note"
      />
      <div className="flex justify-end">
        <Button
          size="sm"
          onClick={submit}
          disabled={submitting || !note.trim()}
          data-testid="reopen-submit"
        >
          {submitting ? "Heropenen…" : "Heropen met feedback"}
        </Button>
      </div>
    </div>
  );
}

// Prefix on the audit-trail comment that `report_impediment` posts on a card
// when an agent gets stuck. The control below surfaces this question so a human
// knows what they're answering.
const IMPEDIMENT_PREFIX = "**Impediment:** ";

// Control shown when a card sits in the Impediment column. An agent that got
// stuck posted an `**Impediment:**` question and released its claim; this lets
// a human type an answer/decision and re-dispatch the card. The answer is
// posted as a durable `**Resolution:**` comment and injected into the resumed
// session's `## IMPEDIMENT` prompt section (backend /resolve-impediment) — the
// reliable channel that a plain activity-feed comment never was.
function ResolveImpedimentControl({
  card,
  activity,
  projectPath,
  onChanged,
}: {
  card: Card;
  activity: ActivityEntry[];
  projectPath: string;
  onChanged: () => void;
}) {
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Surface the agent's question (latest `**Impediment:**` comment) so the
  // human has context for their answer.
  const question = [...activity]
    .reverse()
    .find(
      (e) =>
        e.op_type === "comment" &&
        typeof e.payload.text === "string" &&
        (e.payload.text as string).startsWith(IMPEDIMENT_PREFIX),
    );
  const questionText = question
    ? (question.payload.text as string).slice(IMPEDIMENT_PREFIX.length)
    : null;

  const submit = async () => {
    setSubmitting(true);
    try {
      await kanbanApi.resolveImpediment(
        card.id,
        projectPath,
        answer.trim() || undefined,
      );
      toast.success("Impediment resolved — card re-dispatched");
      setAnswer("");
      onChanged();
    } catch {
      toast.error("Failed to resolve impediment");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="rounded-md border-2 border-orange-500/40 bg-orange-50 p-3 text-sm space-y-2 dark:bg-orange-950/30"
      data-testid="resolve-impediment-control"
    >
      <div className="text-xs font-semibold uppercase text-orange-700 dark:text-orange-400">
        Impediment — needs a human answer
      </div>
      {questionText && (
        <div className="text-foreground whitespace-pre-wrap" data-testid="impediment-question">
          {questionText}
        </div>
      )}
      <Textarea
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder="Your answer/decision — it's injected into the resumed session's prompt so the agent acts on it."
        disabled={submitting}
        data-testid="resolve-impediment-answer"
      />
      <div className="flex justify-end">
        <Button
          size="sm"
          onClick={submit}
          disabled={submitting}
          data-testid="resolve-impediment-submit"
        >
          {submitting ? "Resolving…" : "Answer & re-dispatch"}
        </Button>
      </div>
    </div>
  );
}

function EditablePlan({
  plan,
  cardId,
  onChanged,
}: {
  plan: string;
  cardId: string;
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState(plan);
  const [saving, setSaving] = useState(false);
  // Mirror upstream changes of `plan` (e.g. after the parent re-loads the
  // card via `onChanged`) into `draft`. The `prevPlan` state-tracker is the
  // React-idiomatic "previous props" pattern — assigning to state during
  // render is supported and avoids the cascading-render warning that an
  // unconditional `useEffect(() => setDraft(plan), [plan])` would produce.
  const [prevPlan, setPrevPlan] = useState(plan);
  if (plan !== prevPlan) {
    setPrevPlan(plan);
    setDraft(plan);
  }

  const dirty = draft !== plan;

  const save = async () => {
    setSaving(true);
    try {
      await kanbanApi.updatePlanAttachment(cardId, draft);
      toast.success("Plan saved");
      onChanged();
    } catch {
      toast.error("Failed to save plan");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <MarkdownPreviewToggle
        value={draft}
        onChange={setDraft}
        defaultTab="preview"
        disabled={saving}
      />
      <div className="flex justify-end">
        <Button
          size="sm"
          onClick={save}
          disabled={saving || !dirty}
          data-testid="save-plan-button"
        >
          {saving ? "Saving…" : "Save plan"}
        </Button>
      </div>
    </div>
  );
}

function PlanTabContent({ card, onChanged }: { card: Card; onChanged: () => void }) {
  // Parent case: a "plan" deliverable carries the markdown directly in `ref`
  // and is editable. The child `plan_ref` case below stays read-only.
  const planDeliverable = card.deliverables.find((d) => d.kind === "plan");
  if (planDeliverable) {
    return <EditablePlan plan={planDeliverable.ref} cardId={card.id} onChanged={onChanged} />;
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
  // Latest answered gate (if any). Shown on Impediment cards so the human can
  // see what they picked before clicking "Resolve impediment" — and so the
  // resolved session knows the answer was captured. Mirrors the
  // service.latest_gate_answer ordering (newest first) on the backend.
  const latestAnsweredGate = (() => {
    const answered = gates.filter((g) => g.status === "answered");
    if (!answered.length) return null;
    return [...answered].sort((a, b) => {
      const ta = a.answered_at ? Date.parse(a.answered_at) : 0;
      const tb = b.answered_at ? Date.parse(b.answered_at) : 0;
      return tb - ta;
    })[0];
  })();

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

  // Resolve an Impediment card: dispatches a fresh session that picks up the
  // original question + (when report_impediment used options=) the human's
  // chosen answer. Mirrors the backend's `POST /cards/{cid}/resolve-impediment`.
  // The button is enabled as soon as the card is in Impediment: the legacy
  // free-text path has no gate to wait for, and the structured path is
  // dispatchable the moment the human picks an option (their choice is what
  // gets forwarded, not the unresolved question).
  const resolveImpediment = async () => {
    try {
      await kanbanApi.resolveImpediment(card.id, projectPath);
      toast.success("Impediment resolved — fresh session dispatched");
      onChanged();
    } catch {
      toast.error("Resolve failed — card may have changed; reloading");
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
              {card.column === IMPEDIMENT_COLUMN
                ? "Decision needed — pick one to unblock"
                : "Decision requested"}
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

        {card.column === IMPEDIMENT_COLUMN && latestAnsweredGate && (
          <div
            data-testid="impediment-resolved-pending"
            className="rounded-md border-2 border-emerald-600/50 bg-emerald-50 dark:bg-emerald-950/30 p-3 text-sm"
          >
            <div className="mb-2 text-xs font-semibold uppercase text-emerald-700 dark:text-emerald-400">
              Choice recorded
            </div>
            <div className="text-muted-foreground">The human picked:</div>
            <div className="mt-1 font-medium">
              <MarkdownRenderer
                content={`> ${latestAnsweredGate.answer ?? ""}`}
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={resolveImpediment}
                data-testid="resolve-impediment-button"
              >
                Resolve impediment
              </Button>
            </div>
          </div>
        )}

        {card.column === DONE_COLUMN && (
          <>
            <DoneSummaryBanner card={card} />
            <RequestReviewControl card={card} activity={activity} onChanged={onChanged} />
            <ReopenControl card={card} onChanged={onChanged} />
          </>
        )}

        {card.column === IMPEDIMENT_COLUMN && (
          <ResolveImpedimentControl
            card={card}
            activity={activity}
            projectPath={projectPath}
            onChanged={onChanged}
          />
        )}

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
            <PlanTabContent card={card} onChanged={onChanged} />
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
              model: card.model,
              column_overrides: card.column_overrides,
              transport: card.transport,
              resume_session_id: card.resume_session_id,
              resume_project_folder: card.resume_project_folder,
              scheduled_at: card.scheduled_at,
              analyst_agent_id: card.analyst_agent_id,
              executor_agent_id: card.executor_agent_id,
            }}
            defaultAgent={card.agent}
            projectKey={card.project_key}
            projectPath={projectPath}
            onClose={() => setEditing(false)}
            onSubmit={async ({ title, description, priority, labels, work_type, agent, model, column_overrides, transport, resume_session_id, resume_project_folder, scheduled_at, analyst_agent_id, executor_agent_id }) => {
              try {
                await kanbanApi.updateCard(card.id, {
                  title,
                  description,
                  priority,
                  labels: labels.length ? labels : null,
                  work_type,
                  agent,
                  model,
                  column_overrides,
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
