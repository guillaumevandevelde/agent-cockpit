import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ArrowUpCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { TerminalView } from "@/features/cc-bridge/TerminalView";
import { useCCSessions } from "@/features/cc-bridge/useCCSessions";
import { fetchResumableSessions } from "@/features/cc-bridge/api";
import { ConversationList } from "@/features/sessions/ConversationList";
import { useSessionsApi } from "@/hooks/useSessionsApi";
import type { ResumableSession, SessionDetail } from "@/types/sessions";
import { kanbanApi } from "../api";

type View = "live" | "transcript";

/**
 * Per-card execution view. Reuses the existing CC-Bridge PTY relay (live tmux
 * output) and the Sessions transcript renderer (persistent replay) — no new
 * streaming channel. The card knows its session via the `agent:<session>` claim;
 * `sessionName` is that claim with the prefix stripped.
 */
export function CardRunTab({
  cardId,
  sessionName,
  projectPath,
  fillArea,
}: {
  cardId: string;
  sessionName: string;
  projectPath: string;
  /**
   * Opt-in for the drawer body that already owns scrolling (kanban-kaart
   * 72476d8e…). Switches the xterm container + transcript wrapper from a
   * fixed viewport height (`h-[60vh]`) to `flex-1 h-full` so the widget
   * fills the body and the body itself stops scrolling. Caller is
   * responsible for giving the widget a flex-column parent that has a
   * bounded height (the body in full-area mode does this).
   */
  fillArea?: boolean;
}) {
  const { sessions, refresh } = useCCSessions();
  const isLive = useMemo(
    () => sessions.some((s) => s.session_name === sessionName),
    [sessions, sessionName],
  );
  const liveTarget = useMemo(
    () => sessions.find((s) => s.session_name === sessionName)?.tmux_target ?? sessionName,
    [sessions, sessionName],
  );
  const [takingOver, setTakingOver] = useState(false);

  // Default to the live terminal while the tmux session is alive, otherwise the
  // transcript. Once the user picks a view explicitly we stop auto-switching.
  const [view, setView] = useState<View | null>(null);
  const userPicked = useRef(false);
  useEffect(() => {
    if (!userPicked.current) setView(isLive ? "live" : "transcript");
  }, [isLive]);

  const pick = (v: View) => {
    userPicked.current = true;
    setView(v);
  };

  // Promote a headless (or otherwise dead) session to an attachable tmux pane
  // (docs/cockpit/human-takeover-headless-decision.md §7). Only offered while
  // there's no live pane already — once one exists, "Live" is the takeover.
  const takeOver = async () => {
    setTakingOver(true);
    try {
      await kanbanApi.takeOver(cardId, projectPath);
      await refresh();
      pick("live");
      toast.success("Session promoted — attaching to the tmux pane");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Take over failed";
      toast.error(msg);
    } finally {
      setTakingOver(false);
    }
  };

  const effectiveView = view ?? (isLive ? "live" : "transcript");

  return (
    <div
      data-testid="card-run-tab-root"
      className={cn(fillArea ? "flex-1 min-h-0 flex flex-col space-y-2" : "space-y-2")}
    >
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant={effectiveView === "live" ? "default" : "outline"}
          onClick={() => pick("live")}
        >
          Live
          {isLive && (
            <span className="ml-1.5 h-2 w-2 rounded-full bg-green-500" title="Session alive" />
          )}
        </Button>
        <Button
          size="sm"
          variant={effectiveView === "transcript" ? "default" : "outline"}
          onClick={() => pick("transcript")}
        >
          Transcript
        </Button>
        {!isLive && (
          <Button
            size="sm"
            variant="outline"
            onClick={takeOver}
            disabled={takingOver}
            data-testid="take-over-button"
          >
            <ArrowUpCircle className="mr-1 h-3 w-3" aria-hidden="true" />
            {takingOver ? "Taking over…" : "Take over"}
          </Button>
        )}
        <span className="text-xs text-muted-foreground">session {sessionName}</span>
      </div>

      {effectiveView === "live" ? (
        <div className={cn(fillArea ? "flex-1 min-h-0" : "h-[60vh]", "rounded-md border overflow-hidden")}>
          <TerminalView target={liveTarget} />
        </div>
      ) : (
        <CardTranscript sessionName={sessionName} projectPath={projectPath} fillArea={fillArea} />
      )}
    </div>
  );
}

function CardTranscript({
  sessionName,
  projectPath,
  fillArea,
}: {
  sessionName: string;
  projectPath: string;
  fillArea?: boolean;
}) {
  const { getSessionDetail } = useSessionsApi();
  const [resolved, setResolved] = useState<ResumableSession | null | undefined>(undefined);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Find this card's CC JSONL transcript: resumable-sessions aggregates across
  // the repo's worktrees, labelling each by its dir name — which equals the
  // session name for dispatched worktrees (.claude/worktrees/<session>).
  useEffect(() => {
    let cancelled = false;
    setResolved(undefined);
    fetchResumableSessions(projectPath, 50)
      .then((r) => {
        if (cancelled) return;
        const match = r.sessions.find((s) => s.worktree_label === sessionName) ?? null;
        setResolved(match);
      })
      .catch(() => {
        if (!cancelled) setResolved(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectPath, sessionName]);

  useEffect(() => {
    if (!resolved) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSessionDetail(resolved.project_folder, resolved.id, page)
      .then((data) => {
        if (cancelled) return;
        setDetail(data.session);
        setTotalPages(data.total_pages);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load transcript");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [resolved, page, getSessionDetail]);

  if (resolved === undefined) {
    return <div className="py-8 text-center text-sm text-muted-foreground">Loading…</div>;
  }
  if (resolved === null) {
    return (
      <div className="py-8 text-center text-sm text-muted-foreground">
        No transcript found for this session yet.
      </div>
    );
  }
  if (error) {
    return <div className="py-8 text-center text-sm text-destructive">{error}</div>;
  }

  return (
    <div className={cn(fillArea ? "flex-1 min-h-0 overflow-auto pr-1" : "max-h-[60vh] overflow-y-auto pr-1")}>
      {loading && <div className="py-8 text-center text-sm text-muted-foreground">Loading…</div>}
      {!loading && detail && <ConversationList conversations={detail.conversations} />}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4 pt-4 border-t">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
          >
            <ChevronLeft className="h-4 w-4 mr-1" />
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
          >
            Next
            <ChevronRight className="h-4 w-4 ml-1" />
          </Button>
        </div>
      )}
    </div>
  );
}
