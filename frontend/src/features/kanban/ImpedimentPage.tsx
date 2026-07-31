import { useMemo, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Card as UiCard,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { RefreshButton } from "@/components/shared/RefreshButton";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useFetchData } from "@/hooks/useFetchData";

import { kanbanApi } from "./api";
import type { ActivityEntry, Card, Gate } from "./types";

// Prefix on the audit-trail comment that `report_impediment` posts on a card
// when an agent gets stuck. The page surfaces the comment text after this
// prefix so the human has full context for their answer.
//
// Kept in lock-step with the same constant inside CardDrawer.tsx — if you
// change one, change both, and update the `comment-prefix-contract` section
// of docs/cockpit/kanban-conventions.md.
const IMPEDIMENT_PREFIX = "**Impediment:** ";

// Cap the number of choice buttons rendered. The backend's
// `mcp_server.report_impediment` validates `options` to exactly 4 entries, so
// every agent-parked card already carries 4 — the slice is a defensive cap
// for gates predating that validation, mirroring the `MAX_CHOICE_BUTTONS` cap
// in CardDrawer.tsx.
const MAX_CHOICE_BUTTONS = 4;

/**
 * Dedicated page for resolving an Impediment card. The previous modal
 * (`ResolveImpedimentControl` inside `CardDrawer.tsx`) capped the question
 * scroll area at 55vh and stacked the action surface below it; a long agent
 * `**Impediment:**` markdown question pushed the Resolve button below the
 * 85vh modal on most viewports (kaart 626e05e3…). This page replaces the
 * modal entirely:
 *
 *   - Route: `/kanban/impediment/:cardId` (added in App.tsx). The Impediment
 *     column cards now navigate here directly instead of opening the drawer;
 *     all other columns keep the existing `?card=<id>` drawer flow.
 *   - **Sticky action row** (chosen option A): the choice buttons + textarea
 *     + Resolve button live in a `flex-shrink-0` row anchored at the bottom
 *     of the viewport, separated from the scrolling question above by a
 *     border. The human can always click Resolve without first scrolling —
 *     only the markdown question above scrolls. Operator kan altijd
 *     beslissen, ook zonder eerst te scrollen.
 *   - Question is rendered through `MarkdownRenderer` (the modal showed it as
 *     plain `whitespace-pre-wrap` text, which silently dropped formatting on
 *     multi-paragraph questions — the kind of long-form explanation that
 *     actually triggers an impediment in the first place).
 *
 * The page is self-contained: it fetches the card, its activity feed, and its
 * gates on mount, and re-fetches on the Refresh button. After a successful
 * resolve it navigates back to `/kanban` so the operator lands on the board
 * and sees the card land on Backlog (where auto-dispatch will pick it up
 * next tick).
 */
export function ImpedimentPage() {
  const { cardId } = useParams<{ cardId: string }>();
  const navigate = useNavigate();
  const { activeProject } = useProjectContext();
  const projectPath = activeProject?.path ?? "";

  const cardFetch = useFetchData<Card | null>(
    () => (cardId ? kanbanApi.getCard(cardId) : Promise.resolve(null)),
    [cardId],
  );
  const gatesFetch = useFetchData<Gate[]>(
    () => (cardId ? kanbanApi.listGates(cardId) : Promise.resolve([])),
    [cardId],
  );
  const activityFetch = useFetchData<ActivityEntry[]>(
    () => (cardId ? kanbanApi.activity(cardId) : Promise.resolve([])),
    [cardId],
  );

  const card = cardFetch.data;
  const gates = gatesFetch.data ?? [];
  const activity = activityFetch.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => navigate("/kanban")}
        className="mb-2 self-start"
      >
        <ArrowLeft className="h-4 w-4 mr-2" />
        Back to board
      </Button>

      {cardFetch.loading && !card && (
        <UiCard>
          <CardContent className="py-8">
            <p className="text-center text-muted-foreground">
              Loading impediment…
            </p>
          </CardContent>
        </UiCard>
      )}

      {cardFetch.error && !card && (
        <UiCard>
          <CardContent className="py-8">
            <p className="text-center text-destructive">
              Card not found —{" "}
              {cardId ? cardId.slice(0, 8) : "(no id)"}… has no open impediment.
            </p>
          </CardContent>
        </UiCard>
      )}

      {card && card.column !== "Impediment" && (
        <UiCard>
          <CardContent className="py-8 space-y-2 text-center">
            <p className="text-muted-foreground">
              This card is no longer in the Impediment column (it sits on{" "}
              <span className="font-medium">{card.column}</span>). There is
              nothing to resolve here.
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate("/kanban")}
            >
              Back to board
            </Button>
          </CardContent>
        </UiCard>
      )}

      {card && card.column === "Impediment" && (
        <ResolveImpediment
          card={card}
          activity={activity}
          gates={gates}
          projectPath={projectPath}
          onRefresh={() => {
            void cardFetch.refresh();
            void gatesFetch.refresh();
            void activityFetch.refresh();
          }}
          onResolved={() => navigate("/kanban")}
        />
      )}
    </div>
  );
}

function ResolveImpediment({
  card,
  activity,
  gates,
  projectPath,
  onRefresh,
  onResolved,
}: {
  card: Card;
  activity: ActivityEntry[];
  gates: Gate[];
  projectPath: string;
  onRefresh: () => void;
  onResolved: () => void;
}) {
  const [answer, setAnswer] = useState("");
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Latest **Impediment:** comment in the activity feed — that's the question
  // the human has to answer. We walk the list in reverse so the *most recent*
  // impediment question wins (a card can theoretically be parked multiple
  // times, and the operator wants the active one, not the historical one).
  const questionText = useMemo(() => {
    for (let i = activity.length - 1; i >= 0; i--) {
      const entry = activity[i];
      if (
        entry.op_type === "comment" &&
        typeof entry.payload?.text === "string" &&
        (entry.payload.text as string).startsWith(IMPEDIMENT_PREFIX)
      ) {
        return (entry.payload.text as string).slice(IMPEDIMENT_PREFIX.length);
      }
    }
    return null;
  }, [activity]);

  // One button per agent-supplied option, verbatim — no synthetic filler. The
  // backend rejects `options` lengths other than 4 at the MCP boundary, so a
  // card parked via the standard agent path always carries 4; the slice is
  // the same defensive cap as the old modal's `MAX_CHOICE_BUTTONS`.
  const choiceButtons: Array<{ key: string; label: string }> = gates
    .filter((g) => g.status === "open")
    .flatMap((gate) => gate.options)
    .slice(0, MAX_CHOICE_BUTTONS)
    .map((label) => ({ key: label, label }));

  const hasGateAnswer = gates.some((g) => g.status === "answered");
  const openGate = gates.find((g) => g.status === "open") ?? null;
  const hasChoiceRow = choiceButtons.length > 0;

  const submit = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      if (openGate && selectedOption) {
        try {
          await kanbanApi.answerGate(openGate.id, selectedOption);
        } catch {
          toast.error("Failed to submit gate answer");
          setSubmitting(false);
          return;
        }
      }
      await kanbanApi.resolveImpediment(
        card.id,
        projectPath,
        answer.trim() || undefined,
      );
      toast.success(
        "Impediment resolved — card moved to Backlog; auto-dispatch will pick it up",
      );
      setAnswer("");
      setSelectedOption(null);
      onResolved();
    } catch {
      toast.error("Failed to resolve impediment");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <UiCard className="flex min-h-0 flex-1 flex-col">
      <CardHeader className="flex-shrink-0">
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardDescription className="text-orange-700 dark:text-orange-400 uppercase text-xs font-semibold">
              Impediment — needs a human answer
            </CardDescription>
            <CardTitle className="mt-1">{card.title}</CardTitle>
          </div>
          {/* RefreshButton expects `onClick` (not `onRefresh` — see
              `frontend/src/components/shared/RefreshButton.tsx`). The
              earlier bug passed `onRefresh={onRefresh}` which TypeScript
              silently ignored, so the button rendered with no handler and
              did nothing on click. */}
          <RefreshButton
            onClick={onRefresh}
            loading={submitting}
          />
        </div>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-3">
        {/* Scrollable question area — `flex-1 + min-h-0` lets this column
            clip with `overflow-y-auto` instead of pushing the action row
            below the viewport. `min-h-0` is the load-bearing flex-child rule
            (without it, `overflow-y-auto` doesn't constrain the child's
            height inside a `flex-col` parent). */}
        <div
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
          data-testid="impediment-question-column"
        >
          {questionText ? (
            <div
              className="text-foreground"
              data-testid="impediment-question"
            >
              <MarkdownRenderer content={questionText} />
            </div>
          ) : (
            <p className="text-muted-foreground">
              No **Impediment:** question found in this card's activity feed
              — the card was probably parked via the REST/UI override rather
              than by an agent. Use the free-text field below to record the
              operator's decision.
            </p>
          )}
        </div>
        {/* Sticky action row (chosen option A). Anchored at the bottom of the
            card body with `flex-shrink-0` and a top border — visible on every
            viewport regardless of question length, so the operator can always
            decide without first scrolling the question into view. */}
        <div
          className="flex flex-shrink-0 flex-col gap-2 border-t pt-3"
          data-testid="impediment-action-column"
        >
          {hasChoiceRow && (
            <div
              className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap"
              data-testid="impediment-choice-row"
            >
              {choiceButtons.map((b) => {
                const isSelected = selectedOption === b.label;
                return (
                  <Button
                    key={b.key}
                    size="sm"
                    variant={isSelected ? "default" : "outline"}
                    disabled={submitting}
                    onClick={() => setSelectedOption(b.label)}
                    data-testid="impediment-choice-option"
                    data-choice-key={b.key}
                    // The same wrap-friendly overrides as the modal version:
                    // `min-w-0` lets the button shrink inside `grid-cols-2`,
                    // and `whitespace-normal break-words` lets a long
                    // multi-sentence agent option break across lines instead
                    // of running off the page edge.
                    className="min-w-0 h-auto whitespace-normal break-words px-3 py-1.5 text-left"
                  >
                    {b.label}
                  </Button>
                );
              })}
            </div>
          )}
          <Textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder={
              hasGateAnswer
                ? "Optional: add extra context for the resumed session."
                : hasChoiceRow
                  ? "Optional: add extra info alongside your pick above, or leave a pick above unclicked and answer here instead."
                  : "Your answer/decision — it's injected into the resumed session's prompt so the agent acts on it."
            }
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
              {submitting ? "Resolving…" : "Resolve impediment"}
            </Button>
          </div>
        </div>
      </CardContent>
    </UiCard>
  );
}
