import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TerminalView } from "@/features/cc-bridge/TerminalView";
import { useCCSessions } from "@/features/cc-bridge/useCCSessions";
import { fetchResumableSessions } from "@/features/cc-bridge/api";
import { ConversationList } from "@/features/sessions/ConversationList";
import { useSessionsApi } from "@/hooks/useSessionsApi";
import type { ResumableSession, SessionDetail } from "@/types/sessions";

type View = "live" | "transcript";

/**
 * Per-card execution view. Reuses the existing CC-Bridge PTY relay (live tmux
 * output) and the Sessions transcript renderer (persistent replay) — no new
 * streaming channel. The card knows its session via the `agent:<session>` claim;
 * `sessionName` is that claim with the prefix stripped.
 */
export function CardRunTab({
  sessionName,
  projectPath,
}: {
  sessionName: string;
  projectPath: string;
}) {
  const { sessions } = useCCSessions();
  const isLive = useMemo(
    () => sessions.some((s) => s.session_name === sessionName),
    [sessions, sessionName],
  );
  const liveTarget = useMemo(
    () => sessions.find((s) => s.session_name === sessionName)?.tmux_target ?? sessionName,
    [sessions, sessionName],
  );

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

  const effectiveView = view ?? (isLive ? "live" : "transcript");

  return (
    <div className="space-y-2">
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
        <span className="text-xs text-muted-foreground">session {sessionName}</span>
      </div>

      {effectiveView === "live" ? (
        <div className="h-[60vh] rounded-md border overflow-hidden">
          <TerminalView target={liveTarget} />
        </div>
      ) : (
        <CardTranscript sessionName={sessionName} projectPath={projectPath} />
      )}
    </div>
  );
}

function CardTranscript({
  sessionName,
  projectPath,
}: {
  sessionName: string;
  projectPath: string;
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
    <div className="max-h-[60vh] overflow-y-auto pr-1">
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
