import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { formatDistanceToNow } from "date-fns";
import { Copy, ImagePlus, Link2, Loader2, Play, Trash2 } from "lucide-react";
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
import { cn } from "@/lib/utils";
import { useProviderContext } from "@/contexts/ProviderContext";
import { kanbanApi } from "../api";
import { appsApi } from "../appsApi";
import { CardEditDialog } from "./CardEditDialog";
import { CardRunTab } from "./CardRunTab";
import { CardTokensTab } from "./CardTokensTab";
import { CardLedgerTab } from "./CardLedgerTab";
import { PreviewPane } from "./PreviewPane";
import { ReadyStateBadge } from "./ReadyStateBadge";
import type { CardMeta } from "./Column";
import type { Card, ActivityEntry, Attachment, Deliverable, Gate, RunInstance } from "../types";
import { SPEC_DOC_META_KEY } from "../types";

const LIVE_POLL_INTERVAL_MS = 3000;
const PREVIEW_POLL_INTERVAL_MS = 1500;
const PREVIEW_POLL_TIMEOUT_MS = 35_000;

const AUTO = "__auto__"; // sentinel: agent chosen by column default
const DONE_COLUMN = "Done";
// The drawer is no longer the entry point for Impediment cards — they live
// on a dedicated `/kanban/impediment/:cardId` page so the long-form question
// and the action surface can sit in parallel columns instead of stacked
// inside a 1152px × 85vh modal (kaart 626e05e3… "impediment kaart niet
// leesbaar, kan niet scrollen"). The constant is kept so a deep link
// `?card=<id>` for an Impediment card can be redirected to the new page
// (see KanbanPage's openCard).
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

// Screenshots attached to a card. On dispatch the backend injects each
// image's absolute path into the session prompt so the agent can Read it.
function AttachmentsTab({
  card,
  onChanged,
}: {
  card: Card;
  onChanged: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const attachments: Attachment[] = card.attachments ?? [];

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await kanbanApi.uploadAttachment(card.id, file);
      }
      onChanged();
      toast.success("Screenshot toegevoegd");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Upload mislukt");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDelete = async (attachmentId: string) => {
    try {
      await kanbanApi.deleteAttachment(card.id, attachmentId);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Verwijderen mislukt");
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={uploading}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ImagePlus className="h-4 w-4" />
          )}
          Screenshot uploaden
        </Button>
      </div>
      {attachments.length === 0 ? (
        <div className="text-xs text-muted-foreground">
          Nog geen screenshots. Uploads worden aan de sessie meegegeven zodra
          de kaart gedispatcht wordt.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {attachments.map((a) => (
            <div
              key={a.id}
              className="group relative overflow-hidden rounded border bg-muted/30"
            >
              <a
                href={kanbanApi.attachmentUrl(card.id, a.id)}
                target="_blank"
                rel="noreferrer"
              >
                <img
                  src={kanbanApi.attachmentUrl(card.id, a.id)}
                  alt={a.filename}
                  className="h-28 w-full object-cover"
                  loading="lazy"
                />
              </a>
              <Button
                size="icon"
                variant="destructive"
                className="absolute right-1 top-1 h-6 w-6 opacity-0 transition-opacity group-hover:opacity-100"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(a.id);
                }}
                aria-label="Screenshot verwijderen"
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
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
    case "spec":
      // Companion of `plan`: a brainstorming/design-doc artefact. Renders the
      // markdown body inline like `plan`, but with a distinct icon + label so
      // a card carrying both reads as "design + plan", not "two plans".
      return (
        <div className="text-xs flex flex-col gap-1" data-deliverable-kind={d.kind}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">📐 Spec document</span>
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
//
// Cap: the banner itself is capped at 40vh with `overflow-auto` on the
// inner content area. Without this cap a long summary grows unboundedly
// inside the sticky priority area and pushes the description, spec,
// action buttons, and TabsContent below the 85vh modal edge with no
// scrollbar in sight — kaart d4012bd1… ("Done kaarten nog altijd niet
// goed leesbaar, nu is het onderste deel niet langer leesbaar"). The
// 60/40 split is a deliberate product decision: a long summary stays
// fully readable on its own scrollbar while the rest of the drawer
// always retains at least ~60% of the modal height.
function DoneSummaryBanner({ card }: { card: Card }) {
  const summary = (card.done_summary ?? "").trim();
  const completedAt = card.completed_at ?? null;
  const duration =
    summary && completedAt ? formatDuration(card.created_at, completedAt) : null;

  return (
    <div
      className="flex max-h-[40vh] flex-col rounded-md border-2 border-green-500/40 bg-green-50 text-sm dark:bg-green-950/30"
      data-testid="done-summary-banner"
    >
      <div className="shrink-0 px-3 pt-3 text-xs font-semibold uppercase text-green-700 dark:text-green-400">
        ✅ Completed
      </div>
      {summary ? (
        <div
          className="min-h-0 flex-1 overflow-auto px-3 py-1"
          data-testid="done-summary-content"
        >
          <MarkdownRenderer content={summary} />
        </div>
      ) : (
        <div className="min-h-0 flex-1 px-3 py-1" />
      )}
      {(completedAt || duration) && (
        <div className="shrink-0 flex flex-wrap gap-x-3 px-3 pb-3 pt-1 text-xs text-muted-foreground">
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
        // Compact in the sticky priority area so the body underneath keeps
        // enough vertical room to show the Deliverables / TabsContent (kaart
        // d4012bd1: Done-kaart body bottom-clipped). The default 80px min-h
        // is fine for free-standing forms, but here two of them stack above a
        // single-scroll body and consume half the 85vh modal.
        rows={2}
        className="min-h-[40px]"
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
        // Same compactness rationale as the request-review Textarea above
        // (kaart d4012bd1). Both textareas live in the sticky priority area
        // for Done cards; doubling them at the default 80px min-h is what
        // pushed the body to the "scroll-past-Deliverables-to-see-anything"
        // state captured in the screenshot.
        rows={2}
        className="min-h-[40px]"
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

// Collapsed wrapper around the two rare-action controls on a Done card:
// RequestReviewControl (spawns a sibling analysis card) and ReopenControl
// (moves the *same* card back to Backlog with a rebuttal). Both used to
// render unconditionally above the body, and the two textarea blocks
// (each ~120px in the 85vh dialog) clipped the description / spec /
// subtasks / tabs past the modal edge on long Done summaries (kanban-kaart
// d4012bd1 "Done kaarten nog altijd niet goed leesbaar"). Per the
// operator's decision: "Request review + Heropen inklappen tot één knop,
// pas openen als je ze nodig hebt". The wrapper renders a single toggle
// by default; clicking it reveals the two controls inline, each still
// using its original testid so the existing submit / already-requested /
// in-flight tests keep their hooks intact. The DoneSummaryBanner above
// stays always visible — that's the operator's primary information about
// what shipped; the controls are the rare path.
function DoneActionsPanel({
  card,
  activity,
  onChanged,
}: {
  card: Card;
  activity: ActivityEntry[];
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className="rounded-md border p-3 text-sm space-y-2"
      data-testid="done-actions-panel"
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls="done-actions-panel-body"
        data-testid="done-actions-toggle"
        className="flex w-full items-center justify-between gap-2 rounded-sm text-left text-xs font-semibold uppercase text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span>Review of heropen</span>
        <span aria-hidden="true">{expanded ? "▴" : "▾"}</span>
      </button>
      {expanded && (
        <div
          id="done-actions-panel-body"
          className="space-y-3 pt-1"
          data-testid="done-actions-panel-body"
        >
          <RequestReviewControl
            card={card}
            activity={activity}
            onChanged={onChanged}
          />
          <ReopenControl card={card} onChanged={onChanged} />
        </div>
      )}
    </div>
  );
}

// "Run this branch" control shown on Done cards. Spins up a RunService
// instance for the card's project, polls until it reaches ``healthy`` (or
// fails), posts the preview-URL as an activity-comment, and renders a
// PreviewPane with the iframe. Part of the kanban-card d2689f2d
// preview-URL feature.
//
// `fillArea` is the drawer's full-area-mode signal (kanban-kaart 72476d8e…):
// when set, the inner PreviewPane/iframe fills its parent instead of using
// a fixed `h-[50vh]`. The Start button is rendered regardless — even in
// full-area mode, a Done card with no instance still needs the button so
// the user can spawn one. The full-area wrapper around the body decides
// whether to render *just* the widget or the rest of the card content.
function CardPreviewControl({
  card,
  projectPath,
  fillArea,
}: {
  card: Card;
  projectPath: string;
  fillArea?: boolean;
}) {
  const [instance, setInstance] = useState<RunInstance | null>(null);
  const [starting, setStarting] = useState(false);
  const [errored, setErrored] = useState<string | null>(null);

  const start = async () => {
    setStarting(true);
    setErrored(null);
    try {
      const started = await appsApi.startRun({
        project_path: projectPath,
        // MVP: boot a tiny static HTTP server so the preview flow has a live
        // URL to render. Real project-start-command detection is a separate
        // facet-D follow-up.
        command: ["python3", "-m", "http.server", "4123"],
        health_path: "/",
        health_timeout_s: 30,
      });
      setInstance(started);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to start run";
      setErrored(msg);
      toast.error(msg);
      setStarting(false);
    }
  };

  useEffect(() => {
    if (!instance) return;
    if (instance.status !== "starting") return;
    let cancelled = false;
    const startedAt = Date.now();
    const tick = async () => {
      try {
        const fresh = await appsApi.getRun(instance.instance_id);
        if (cancelled) return;
        setInstance(fresh);
        if (fresh.status === "healthy" || fresh.status === "failed") {
          const text =
            fresh.status === "healthy"
              ? `Preview live: ${fresh.url}`
              : `Preview failed: ${fresh.error ?? "unknown error"}`;
          try {
            await kanbanApi.comment(card.id, text);
          } catch {
            // Best-effort: the run lifecycle is the source of truth; the
            // comment is a human-friendly breadcrumb.
          }
        }
      } catch {
        // Transient — try again next tick.
      }
    };
    const interval = setInterval(tick, PREVIEW_POLL_INTERVAL_MS);
    void tick();
    const timeout = setTimeout(() => {
      cancelled = true;
      clearInterval(interval);
      setStarting(false);
    }, PREVIEW_POLL_TIMEOUT_MS);
    void startedAt;
    return () => {
      cancelled = true;
      clearInterval(interval);
      clearTimeout(timeout);
      setStarting(false);
    };
  }, [instance, card.id]);

  const onStopped = () => {
    setInstance(null);
    setStarting(false);
  };

  return (
    <div
      className="rounded-md border p-3 text-sm space-y-2"
      data-testid="run-this-branch-control"
    >
      <div className="text-xs font-semibold uppercase text-muted-foreground">
        Preview
      </div>
      {!instance && !errored && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-muted-foreground">
            Spin up a sandboxed instance of this branch and surface the URL.
          </span>
          <Button
            size="sm"
            onClick={start}
            disabled={starting}
            data-testid="run-this-branch-button"
          >
            <Play className="mr-1 h-3 w-3" aria-hidden="true" />
            Run this branch
          </Button>
        </div>
      )}
      {starting && !instance && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
          Starting…
        </div>
      )}
      {errored && !instance && (
        <div className="text-destructive">Failed to start: {errored}</div>
      )}
      {instance && instance.status === "starting" && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
          Health-checking {instance.url}…
        </div>
      )}
      {instance && instance.status !== "starting" && (
        <PreviewPane instance={instance} onStopped={onStopped} fillArea={fillArea} />
      )}
    </div>
  );
}

// Impediment resolution moved off the drawer entirely — the page at
// `/kanban/impediment/:cardId` (see `ImpedimentPage.tsx`) is the only entry
// point now. Two reasons:
//   1. The previous `ResolveImpedimentControl` lived inside the 1152px × 85vh
//      Radix `Dialog`, and a long agent `**Impediment:**` markdown question
//      pushed the recorded-choice + 4 choice buttons + textarea + Resolve
//      button past the modal edge on most viewports — operators literally
//      could not click Resolve without reloading the page (kaart 626e05e3…,
//      "impediment kaart niet leesbaar, kan niet scrollen").
//   2. The 55vh split-layout cap was a workaround for the modal's height
//      limit; a dedicated page lets the question live in a proper scrollable
//      column with no height ceiling while the action surface anchors at
//      the bottom of that column.
//
// The drawer therefore no longer hosts the resolve flow. The Impediment
// branch in the sticky priority area below renders a small pointer to the
// page so the rare stale-tab / mid-state case is not stranded.

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
        flexibleHeight
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
  const navigate = useNavigate();

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
    // Both references navigate via the `?card=<id>` deep-link (card-
    // references-analysis §D2) — KanbanPage's own reconciliation effect
    // opens the drawer (falling back to the cross-project `getCard` lookup
    // or an error toast for an unknown id), so this only needs to update
    // the query param.
    return (
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">Parent plan:</span>
          {parentId ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate(`?card=${parentId}`)}
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
              <button
                key={depId}
                type="button"
                onClick={() => navigate(`?card=${depId}`)}
                className="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Badge variant="secondary" className="cursor-pointer hover:border-primary/50">
                  {depId.slice(0, 8)}
                </Badge>
              </button>
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

// Card → spec-doc link (spec-driven-development Fase 1). A functional card names
// the canonical `docs/cockpit/` doc it implements/updates under
// `metadata[SPEC_DOC_META_KEY]` — the machine-readable anchor Fase 2 drift-
// detection reads. Reuses the existing `metadata` bag (no new datamodel). An
// analyst plan-attachment counts as the spec by definition, so a card with a
// plan deliverable and no explicit link surfaces that instead.
function SpecLinkSection({ card, onChanged }: { card: Card; onChanged: () => void }) {
  const specDoc =
    typeof card.metadata?.[SPEC_DOC_META_KEY] === "string"
      ? (card.metadata[SPEC_DOC_META_KEY] as string).trim()
      : "";
  const hasPlan = card.deliverables.some(
    (d) => d.kind === "plan" || d.kind === "plan_ref",
  );

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(specDoc);
  const [saving, setSaving] = useState(false);

  const startEdit = () => {
    setDraft(specDoc);
    setEditing(true);
  };

  const save = async () => {
    const trimmed = draft.trim();
    // metadata is replaced wholesale on update, so merge with the existing bag.
    const rest = { ...(card.metadata ?? {}) };
    if (trimmed) {
      rest[SPEC_DOC_META_KEY] = trimmed;
    } else {
      delete rest[SPEC_DOC_META_KEY];
    }
    setSaving(true);
    try {
      await kanbanApi.updateCard(card.id, { metadata: rest });
      setEditing(false);
      onChanged();
    } catch {
      toast.error("Failed to save spec link");
    } finally {
      setSaving(false);
    }
  };

  const isUrl = /^https?:\/\//i.test(specDoc);

  return (
    <div className="rounded-md border p-3 text-sm space-y-2" data-testid="spec-link-section">
      <div className="text-xs font-semibold uppercase text-muted-foreground">
        Spec
      </div>
      {editing ? (
        <div className="space-y-2">
          <input
            className="w-full rounded-md border bg-background px-2 py-1 font-mono text-xs"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="docs/cockpit/<doc>.md of https://…"
            disabled={saving}
            data-testid="spec-link-input"
          />
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setEditing(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button size="sm" onClick={save} disabled={saving} data-testid="spec-link-save">
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      ) : specDoc ? (
        <div className="flex flex-wrap items-center gap-2" data-testid="spec-link-value">
          <span className="font-mono">📄</span>
          {isUrl ? (
            <a
              href={specDoc}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline break-all"
            >
              {specDoc}
            </a>
          ) : (
            <span className="font-mono break-all">{specDoc}</span>
          )}
          <Button size="sm" variant="ghost" className="h-6 px-2" onClick={startEdit} data-testid="spec-link-edit">
            Edit
          </Button>
        </div>
      ) : hasPlan ? (
        <div className="flex flex-wrap items-center gap-2" data-testid="spec-from-plan">
          <span className="text-muted-foreground">
            📋 Plan-attachment geldt als de spec.
          </span>
          <Button size="sm" variant="ghost" className="h-6 px-2" onClick={startEdit} data-testid="spec-link-edit">
            Link doc
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">Geen spec-link.</span>
          <Button size="sm" variant="ghost" className="h-6 px-2" onClick={startEdit} data-testid="spec-link-edit">
            Link doc
          </Button>
        </div>
      )}
    </div>
  );
}

// "Subtasks" section shown on a parent card with ≥1 child (`parent_card_id
// === card.id`) — kanban card 81797046. Each row shows the child's title +
// its `ReadyStateBadge`, reusing the precedence-derived `cardMeta` map
// KanbanPage already computes for the board (not reimplemented here), and
// is clickable to navigate the operator from parent to child via the
// existing `?card=<id>` deep-link (same pattern as the "Parent plan" /
// "Depends on" links in PlanTabContent below).
function SubtasksSection({
  childCards,
  cardMeta,
  onNavigate,
}: {
  childCards: Card[];
  cardMeta?: Map<string, CardMeta>;
  onNavigate: (cardId: string) => void;
}) {
  if (childCards.length === 0) return null;
  return (
    <div className="rounded-md border p-3 text-sm space-y-2" data-testid="subtasks-section">
      <div className="text-xs font-semibold uppercase text-muted-foreground">
        Subtasks
      </div>
      <div className="space-y-1">
        {childCards.map((child) => {
          const meta = cardMeta?.get(child.id);
          return (
            <button
              key={child.id}
              type="button"
              onClick={() => onNavigate(child.id)}
              className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              data-testid="subtask-row"
            >
              <span className="truncate">{child.title}</span>
              {meta?.readyState && (
                <ReadyStateBadge
                  state={meta.readyState}
                  blockerTitles={meta.blockerTitles}
                  missingDepIds={meta.missingDepIds}
                  heldSince={meta.heldSince}
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function CardDrawer({
  card,
  projectPath,
  cards = [],
  cardMeta,
  onClose,
  onChanged,
}: {
  card: Card;
  projectPath: string;
  // All cards for the current project (KanbanPage already loads them) —
  // used to derive this card's children (`parent_card_id === card.id`) for
  // the Subtasks section. No dedicated child-fetch/API-filter (AC4).
  cards?: Card[];
  // Precomputed ready-state map (KanbanPage's precedence derivation) reused
  // for the Subtasks section's per-child ReadyStateBadge.
  cardMeta?: Map<string, CardMeta>;
  onClose: () => void;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
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

  // Controlled active tab so the Ledger tab's outcome step can jump the drawer
  // to the Run (transcript) / Tokens tabs instead of re-rendering them itself.
  const [activeTab, setActiveTab] = useState<string>(runSession ? "run" : "deliverables");

  // Full-area-mode signal for the two viewport-bound widgets (xterm in
  // CardRunTab + preview iframe in PreviewPane). Kanban-kaart 72476d8e…
  // chose the lees-first body: when the Run tab is active or the Done
  // preview iframe is showing, the body switches from `overflow-auto` to
  // `overflow-hidden flex flex-col` and the widget fills the body. The
  // rest of the drawer content (description, spec, subtasks, buttons,
  // tabs) is hidden behind the widget in this mode — the user picked
  // the Run/preview tab to focus on it, and gates + Done summary remain
  // visible above the widget so action-required content is never lost.
  const isFullAreaMode = activeTab === "run" && Boolean(runSession);
  // Inside the `isFullAreaMode` branch below, TypeScript narrows `activeTab`
  // to the literal `"run"` (because the only branch that flips the flag is
  // `activeTab === "run"`). Since the user can still navigate to other tabs
  // from the TabsList, widen the type for the per-tab comparisons.
  const tab: string = activeTab;

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

  // Resolve an Impediment card: dispatches a fresh session that picks up the
  // original question + (when report_impediment used options=) the human's
  // chosen answer. Mirrors the backend's `POST /cards/{cid}/resolve-impediment`.
  // The button is enabled as soon as the card is in Impediment: the legacy
  // free-text path has no gate to wait for, and the structured path is
  // dispatchable the moment the human picks an option (their choice is what
  // gets forwarded, not the unresolved question). The actual button lives
  // in `ImpedimentPage.tsx` (kaart 626e05e3… moved the resolve flow off the
  // drawer entirely; this comment predates that move) — it calls
  // `kanbanApi.resolveImpediment` directly with the optional textarea
  // answer so the two paths share one codepath. (kaart 4279448c: merge the
  // "impediment resolved" + "decision human answered needed" flows into a
  // single control.)

  const isClaimedByAgent = card.claimed_by?.startsWith("agent:");
  const isClaimedByHuman = card.claimed_by && !isClaimedByAgent;

  const childCards = cards.filter((c) => c.parent_card_id === card.id);

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
      <DialogContent
        className={cn(
          // MODAL_SIZES.XL ships with `overflow-y-auto`; the body below owns
          // scrolling, so the modal itself clips with `overflow-hidden` and
          // `cn` (twMerge) ensures that wins over the constant's `overflow-y-auto`.
          // The `flex flex-col` switch replaces the default `grid` so a
          // single flex child (the body) can take the remaining vertical
          // space below the sticky DialogHeader. `h-[85vh]` lifts the
          // drawer above the previous 80vh so a tall tab body (Run transcript,
          // Ledger files table) gets more usable height on big monitors
          // (kanban card b4985b42…: kaarten niet overzichtelijk).
          MODAL_SIZES.XL,
          "h-[85vh] flex flex-col overflow-hidden",
        )}
      >
        <DialogHeader className="shrink-0">
          <div className="flex items-center gap-2 pr-6">
            <DialogTitle className="min-w-0 flex-1 truncate">{card.title}</DialogTitle>
            <button
              type="button"
              data-testid="card-id-chip"
              title={`Copy full card id (${card.id})`}
              onClick={() => {
                navigator.clipboard.writeText(card.id);
                toast.success("Card id copied");
              }}
              className="inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-xs font-normal text-muted-foreground hover:border-primary/50 hover:text-foreground"
            >
              {card.id.slice(0, 8)}…
              <Copy className="h-3 w-3" aria-hidden="true" />
            </button>
            <button
              type="button"
              data-testid="card-copy-reference"
              title="Copy reference — a markdown link to this card, clickable when pasted into another card's description"
              onClick={() => {
                navigator.clipboard.writeText(`[${card.title}](/kanban?card=${card.id})`);
                toast.success("Reference copied");
              }}
              className="inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5 text-muted-foreground hover:border-primary/50 hover:text-foreground"
            >
              <Link2 className="h-3 w-3" aria-hidden="true" />
            </button>
          </div>
        </DialogHeader>

        {/* Sticky priority area: action-required content always visible
            above the body, even when the body is in full-area mode (Run
            tab). Decisions + Done summary are never hidden behind a
            widget.

            Height cap (kaart d4012bd1…): this area is `shrink-0`, so
            whatever it contains is subtracted from the body's `flex-1`
            before the body gets anything. Capping only the Done banner
            (40vh) was not enough — expanding the "Review of heropen"
            panel adds ~320px of textareas, and on a 1280×720 viewport
            that measured body = 0px with the Heropen submit button
            113px *below* the modal edge and no scrollbar anywhere: the
            exact symptom this card reports. `max-h-[50vh]` +
            `overflow-y-auto` bounds the whole area instead of each
            child: the collapsed state (banner ≤ 40vh + the 42px toggle)
            still fits without a scrollbar, and the rare expanded state
            scrolls inside this area instead of pushing the body off the
            modal. */}
        <div
          className="max-h-[50vh] shrink-0 space-y-3 overflow-y-auto"
          data-testid="card-drawer-priority-area"
        >
          {openGates
            // On the Impediment column the open-gate choice row is absorbed
            // into `ImpedimentPage.tsx` (kaart 626e05e3… moved the resolve
            // flow off the drawer entirely; this filter predates that move)
            // — never render the separate "Decision needed — pick one to
            // unblock" panel for those cards, that was the "twee panelen
            // boven elkaar" the Revisit note called out (kaart 4279448c
            // revisit).
            .filter(() => card.column !== IMPEDIMENT_COLUMN)
            .map((gate) => (
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

          {card.column === IMPEDIMENT_COLUMN && (
            // The drawer is no longer the entry point for Impediment cards
            // (see the rationale at the top of this file). When the drawer
            // *does* open on an Impediment card (e.g. an agent flipped the
            // column while the operator was reading, or a stale tab landed
            // here from before the page-route shipped), we render a small
            // pointer to the dedicated resolve page so the operator is not
            // stranded without an action surface.
            <div
              className="rounded-md border-2 border-orange-500/40 bg-orange-50 p-3 text-sm space-y-2 dark:bg-orange-950/30"
              data-testid="impediment-drawer-pointer"
            >
              <div className="text-xs font-semibold uppercase text-orange-700 dark:text-orange-400">
                Impediment — needs a human answer
              </div>
              <p className="text-muted-foreground">
                Resolve this card on the dedicated Impediment page — it gives
                the full question a proper scroll surface and keeps the
                Resolve button always reachable.
              </p>
              <div className="flex justify-end">
                <Button
                  size="sm"
                  onClick={() => navigate(`/kanban/impediment/${card.id}`)}
                  data-testid="impediment-open-page"
                >
                  Open resolve page
                </Button>
              </div>
            </div>
          )}

          {card.column === DONE_COLUMN && (
            <>
              <DoneSummaryBanner card={card} />
              <DoneActionsPanel
                card={card}
                activity={activity}
                onChanged={onChanged}
              />
            </>
          )}
        </div>

        {isFullAreaMode ? (
          /* Full-area mode: the TabsList stays at the top (shrink-0) so the
             user can still navigate to another tab; the active TabsContent
             below fills the body via flex-1 + overflow-hidden + flex-col.
             Other drawer content (description, spec, subtasks, buttons) is
             intentionally hidden — the user picked the Run tab to focus
             on the live session, and Radix only renders the active
             TabsContent anyway. */
          <Tabs
            value={activeTab}
            onValueChange={setActiveTab}
            className="flex-1 min-h-0 flex flex-col overflow-hidden"
            data-testid="card-drawer-full-area"
          >
            <TabsList className="shrink-0">
              <TabsTrigger value="deliverables">Deliverables</TabsTrigger>
              <TabsTrigger value="screenshots">
                Screenshots
                {(card.attachments?.length ?? 0) > 0
                  ? ` (${card.attachments?.length})`
                  : ""}
              </TabsTrigger>
              <TabsTrigger value="activity">Activity</TabsTrigger>
              <TabsTrigger value="plan">Plan</TabsTrigger>
              <TabsTrigger value="ledger">Ledger</TabsTrigger>
              <TabsTrigger value="tokens">Tokens</TabsTrigger>
              {runSession && <TabsTrigger value="run">Run</TabsTrigger>}
            </TabsList>

            <TabsContent
              value="deliverables"
              className={cn(
                "flex-1 min-h-0 mt-2",
                tab === "deliverables" && "overflow-auto",
              )}
            >
              {card.deliverables.length === 0 && (
                <div className="text-xs text-muted-foreground">None</div>
              )}
              <div className="space-y-2">
                {card.deliverables.map((d) => (
                  <DeliverableRow key={d.id} d={d} />
                ))}
              </div>
            </TabsContent>

            <TabsContent
              value="screenshots"
              className={cn(
                "flex-1 min-h-0 mt-2",
                tab === "screenshots" && "overflow-auto",
              )}
            >
              <AttachmentsTab card={card} onChanged={onChanged} />
            </TabsContent>

            <TabsContent
              value="activity"
              className={cn(
                "flex-1 min-h-0 mt-2",
                tab === "activity" && "overflow-auto",
              )}
            >
              {activity.map((e) => (
                <div key={e.hlc} className="text-xs text-muted-foreground">
                  {e.op_type} &mdash; {new Date(e.created_at).toLocaleString()}
                  {e.op_type === "comment"
                    ? `: ${String(e.payload.text ?? "")}`
                    : ""}
                </div>
              ))}
            </TabsContent>

            <TabsContent
              value="plan"
              className={cn(
                "flex-1 min-h-0 mt-2",
                tab === "plan" && "overflow-auto",
              )}
            >
              <PlanTabContent card={card} onChanged={onChanged} />
            </TabsContent>

            <TabsContent
              value="ledger"
              className={cn(
                "flex-1 min-h-0 mt-2",
                tab === "ledger" && "overflow-auto",
              )}
            >
              <CardLedgerTab
                card={card}
                onNavigateTab={setActiveTab}
                runAvailable={Boolean(runSession)}
              />
            </TabsContent>

            <TabsContent
              value="tokens"
              className={cn(
                "flex-1 min-h-0 mt-2",
                tab === "tokens" && "overflow-auto",
              )}
            >
              <CardTokensTab card={card} />
            </TabsContent>

            {runSession && (
              <TabsContent
                value="run"
                className={cn(
                  "flex-1 min-h-0 mt-2",
                  tab === "run" && "overflow-hidden flex flex-col",
                )}
              >
                <CardRunTab
                  cardId={card.id}
                  sessionName={runSession}
                  projectPath={projectPath}
                  fillArea
                />
              </TabsContent>
            )}
          </Tabs>
) : (
          /* Default mode: a single scroll container so the operator scrolls
             description → spec → subtasks → action buttons, and the TabsList
             stays sticky at the top of that body — it remains reachable
             without scrolling all the way back up past the description, while
             the active tab content scrolls inline with the rest (one
             scrollbar, no nested scroll containers). Children must not
             declare their own height-cap + overflow (the nested scroll
             containers removed earlier lived in CardRunTab, CardLedgerTab,
             and MarkdownPreviewToggle). */
          <div
            className="flex-1 min-h-0 space-y-4 overflow-auto"
            data-testid="card-drawer-body"
          >
            {card.column === DONE_COLUMN && (
              <CardPreviewControl card={card} projectPath={projectPath} />
            )}

            <div className="text-sm">
              <MarkdownRenderer content={card.description || "_No description_"} />
            </div>

            <SpecLinkSection card={card} onChanged={onChanged} />

            <SubtasksSection
              childCards={childCards}
              cardMeta={cardMeta}
              onNavigate={(childId) => navigate(`?card=${childId}`)}
            />

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

            <Tabs value={activeTab} onValueChange={setActiveTab}>
              {/* Sticky TabsList so an operator scrolling past the description
                  can still switch tabs without scrolling back up. The bar
                  pulls a translucent backdrop so the description fading past
                  stays legible (preferred over an opaque strip that would
                  punch a hard line through the markdown). `-mx-1 px-1`
                  matches the body's `space-y-4` gutters so the sticky strip
                  aligns with the surrounding content edges. */}
              <div className="sticky top-0 z-10 -mx-1 mb-1 bg-background/95 px-1 py-2 backdrop-blur supports-[backdrop-filter]:bg-background/80">
                <TabsList className="flex w-full flex-wrap">
                  <TabsTrigger value="deliverables">Deliverables</TabsTrigger>
                  <TabsTrigger value="screenshots">
                    Screenshots
                    {(card.attachments?.length ?? 0) > 0
                      ? ` (${card.attachments?.length})`
                      : ""}
                  </TabsTrigger>
                  <TabsTrigger value="activity">Activity</TabsTrigger>
                  <TabsTrigger value="plan">Plan</TabsTrigger>
                  <TabsTrigger value="ledger">Ledger</TabsTrigger>
                  <TabsTrigger value="tokens">Tokens</TabsTrigger>
                  {runSession && <TabsTrigger value="run">Run</TabsTrigger>}
                </TabsList>
              </div>

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

              <TabsContent value="screenshots">
                <AttachmentsTab card={card} onChanged={onChanged} />
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

              <TabsContent value="ledger">
                <CardLedgerTab
                  card={card}
                  onNavigateTab={setActiveTab}
                  runAvailable={Boolean(runSession)}
                />
              </TabsContent>

              <TabsContent value="tokens">
                <CardTokensTab card={card} />
              </TabsContent>

              {runSession && (
                <TabsContent value="run">
                  <CardRunTab cardId={card.id} sessionName={runSession} projectPath={projectPath} />
                </TabsContent>
              )}
            </Tabs>
          </div>
        )}

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
