import { useEffect, useState } from "react";
import {
  CheckCircle2,
  FileText,
  FlaskConical,
  ListChecks,
  Loader2,
  MessageSquareText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { kanbanApi } from "../api";
import type { RunLedger } from "../runLedger";
import type { Card } from "../types";

/**
 * Per-card run-ledger tab (docs/cockpit/run-ledger-decision.md §3-5).
 *
 * Renders the spine `prompt → files → tests → outcome → model` stitched by
 * GET /kanban/cards/{cid}/run-ledger as a vertical timeline. It never
 * re-renders the raw transcript or token breakdown — those already live on
 * the Run and Tokens tabs, so the outcome step *links* to them via
 * `onNavigateTab` instead of duplicating them.
 *
 * Best-effort per step: the backend marks a step `available: false` with a
 * `note` when its source is missing (a Backlog card with no branch, a gc'd
 * worktree), and each step renders that as an empty/"not yet" state — the
 * whole tab never crashes on a card without a branch/dispatch (same
 * discipline as CardTokensTab).
 */
export function CardLedgerTab({
  card,
  onNavigateTab,
  runAvailable,
}: {
  card: Card;
  onNavigateTab: (tab: string) => void;
  runAvailable: boolean;
}) {
  // undefined = first fetch in flight; null is never used (the endpoint
  // always returns a ledger for an existing card — 404 only when the card
  // doesn't exist, which the surrounding drawer already rules out).
  const [ledger, setLedger] = useState<RunLedger | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    kanbanApi
      .getRunLedger(card.id)
      .then((resp) => {
        if (!cancelled) {
          setLedger(resp);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [card.id]);

  if (error) {
    return (
      <div className="p-4 text-sm text-destructive" data-testid="ledger-error">
        Failed to load run ledger: {error}
      </div>
    );
  }

  if (ledger === undefined) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading run ledger…
      </div>
    );
  }

  const { task, context, files, tests, outcome } = ledger;

  return (
    <ol className="space-y-1 p-2" data-testid="ledger-timeline">
      <TimelineStep icon={<ListChecks className="h-4 w-4" />} title="Task">
        <div className="font-medium text-foreground">{task.title}</div>
        {task.description ? (
          <div className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">
            {task.description}
          </div>
        ) : (
          <div className="text-xs text-muted-foreground">No description.</div>
        )}
      </TimelineStep>

      <TimelineStep icon={<MessageSquareText className="h-4 w-4" />} title="Context">
        {context.available ? (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {context.phase && (
                <Badge variant="outline" data-testid="ledger-context-phase">
                  phase: {context.phase}
                </Badge>
              )}
              {context.ship_mode && (
                <Badge variant="outline">ship: {context.ship_mode}</Badge>
              )}
            </div>
            {context.impediment_question && (
              <LabeledBlock label="Impediment question">
                {context.impediment_question}
              </LabeledBlock>
            )}
            {context.impediment_answer && (
              <LabeledBlock label="Human answer">
                {context.impediment_answer}
              </LabeledBlock>
            )}
            {context.revisit_question && (
              <LabeledBlock label="Revisit">{context.revisit_question}</LabeledBlock>
            )}
            {context.prompt ? (
              <details className="rounded-md border bg-muted/30" data-testid="ledger-context-prompt">
                <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted-foreground">
                  Dispatch prompt (persona omitted)
                </summary>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap px-3 pb-3 text-xs text-muted-foreground">
                  {context.prompt}
                </pre>
              </details>
            ) : (
              <EmptyNote>Prompt could not be reconstructed.</EmptyNote>
            )}
          </div>
        ) : (
          <EmptyNote>No dispatch context yet.</EmptyNote>
        )}
      </TimelineStep>

      <TimelineStep icon={<FileText className="h-4 w-4" />} title="Files">
        {files.available ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {files.branch && (
                <span className="font-mono">🔀 {files.branch}</span>
              )}
              <span>
                {files.files_changed} file{files.files_changed === 1 ? "" : "s"} ·{" "}
                <span className="text-emerald-600 dark:text-emerald-400">
                  +{files.insertions_total}
                </span>{" "}
                <span className="text-destructive">−{files.deletions_total}</span>
              </span>
            </div>
            {files.files.length > 0 && (
              <div className="overflow-hidden rounded-md border" data-testid="ledger-files">
                <table className="w-full text-xs">
                  <tbody>
                    {files.files.map((f) => (
                      <tr key={f.path} className="border-t first:border-t-0">
                        <td className="px-3 py-1.5 font-mono break-all">{f.path}</td>
                        <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-emerald-600 dark:text-emerald-400">
                          +{f.insertions}
                        </td>
                        <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-destructive">
                          −{f.deletions}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <EmptyNote>{files.note ?? "No file changes yet."}</EmptyNote>
        )}
      </TimelineStep>

      <TimelineStep icon={<FlaskConical className="h-4 w-4" />} title="Tests">
        {tests.available ? (
          <div className="space-y-1 text-xs text-muted-foreground">
            <div className="flex flex-wrap items-center gap-2">
              {tests.status && (
                <Badge variant="outline" data-testid="ledger-tests-status">
                  {tests.status}
                </Badge>
              )}
              {tests.iteration_count != null && (
                <span>
                  {tests.iteration_count} iteration
                  {tests.iteration_count === 1 ? "" : "s"}
                </span>
              )}
            </div>
            {tests.last_line && (
              <div className="font-mono break-all">{tests.last_line}</div>
            )}
            {tests.ci_url && <CiLink url={tests.ci_url} />}
          </div>
        ) : (
          <div className="space-y-1">
            <EmptyNote>{tests.note ?? "No verify/CI run recorded."}</EmptyNote>
            {tests.ci_url && <CiLink url={tests.ci_url} />}
          </div>
        )}
      </TimelineStep>

      <TimelineStep
        icon={<CheckCircle2 className="h-4 w-4" />}
        title="Outcome & model"
        last
      >
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge variant="secondary" data-testid="ledger-outcome-column">
              {outcome.column}
            </Badge>
            {outcome.model && (
              <span className="text-muted-foreground">
                model: <span className="font-mono">{outcome.model}</span>
              </span>
            )}
            {outcome.completed_at && (
              <span className="text-muted-foreground">
                {formatTimestamp(outcome.completed_at)}
              </span>
            )}
          </div>
          {outcome.outcome_text ? (
            <div className="whitespace-pre-wrap rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">
              {outcome.outcome_text}
            </div>
          ) : (
            <EmptyNote>No outcome recorded yet.</EmptyNote>
          )}
          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              size="sm"
              variant="outline"
              onClick={() => onNavigateTab("tokens")}
              data-testid="ledger-link-tokens"
            >
              View token breakdown
            </Button>
            {runAvailable && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onNavigateTab("run")}
                data-testid="ledger-link-run"
              >
                View transcript
              </Button>
            )}
          </div>
        </div>
      </TimelineStep>
    </ol>
  );
}

// A single node in the vertical timeline: an icon marker on a left rail plus
// the step's content. `last` drops the connecting line below the marker.
function TimelineStep({
  icon,
  title,
  last,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className="flex gap-3">
      <div className="flex flex-col items-center">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-background text-muted-foreground">
          {icon}
        </div>
        {!last && <div className="w-px flex-1 bg-border" />}
      </div>
      <div className="min-w-0 flex-1 pb-4">
        <div className="mb-1 text-sm font-semibold">{title}</div>
        {children}
      </div>
    </li>
  );
}

function LabeledBlock({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border bg-muted/30 p-2 text-xs">
      <div className="mb-0.5 font-medium text-muted-foreground">{label}</div>
      <div className="whitespace-pre-wrap text-foreground">{children}</div>
    </div>
  );
}

function EmptyNote({ children }: { children: React.ReactNode }) {
  return <div className="text-xs text-muted-foreground">{children}</div>;
}

// The tests-step CI link. `ci_url` is the `pr` deliverable ref — a full URL
// (render as a link) or a shorthand like "PR #123" (render as text), mirroring
// the DeliverableRow pr-kind handling in CardDrawer.
function CiLink({ url }: { url: string }) {
  const isUrl = /^https?:\/\//i.test(url);
  if (!isUrl) return <span className="font-mono text-xs">{url}</span>;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline break-all"
      data-testid="ledger-ci-link"
    >
      {url}
    </a>
  );
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
